"""
Sign-in, invite redemption and password management.

Two rules shape this module:

* Never confirm whether an email exists. Login, forgot-password and token
  preview all answer identically for unknown accounts, otherwise the endpoints
  become a staff directory for anyone with a browser.
* Every outcome that matters is audited, including failures. A run of
  LOGIN_FAILED rows against one account is the signal someone is being attacked.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.enums import AuditAction, TokenPurpose
from app.core import ratelimit
from app.core.security import (
    PasswordPolicyError,
    create_access_token,
    generate_url_token,
    hash_password,
    hash_url_token,
    validate_password,
    verify_password,
)
from app.models.auth_token import AuthToken
from app.models.base import naive_utcnow
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    InviteLinkResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    SetPasswordRequest,
    ThemePreferenceRequest,
    TokenPreview,
    UserProfile,
)
from app.services import audit
from app.services import email as email_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

#: Lock the account after this many consecutive failures, for this long. Short
#: enough that a locked-out colleague is not blocked for the afternoon, long
#: enough that online guessing is hopeless.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

INVITE_VALID_HOURS = 72
RESET_VALID_HOURS = 2

#: One message for "no such account" and "wrong password" alike.
_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password.",
)


def _issue_token(
    db: DbSession,
    user: User,
    purpose: TokenPurpose,
    *,
    valid_hours: int,
    created_by: User | None = None,
) -> tuple[str, AuthToken]:
    """Mint a single-use token, invalidating any outstanding one of the same kind."""
    now = naive_utcnow()

    outstanding = db.execute(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
    ).scalars()
    for stale in outstanding:
        # Burn rather than delete: the ledger should still show it existed.
        stale.used_at = now

    raw, token_hash = generate_url_token()
    token = AuthToken(
        tenant_id=user.tenant_id,
        user_id=user.id,
        purpose=purpose,
        token_hash=token_hash,
        expires_at=now + timedelta(hours=valid_hours),
        created_by_id=created_by.id if created_by else None,
    )
    db.add(token)
    return raw, token


def build_invite_url(raw_token: str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}/set-password?token={raw_token}"


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: DbSession) -> LoginResponse:
    email = payload.email.strip().lower()

    # Account lockout stops one password being ground against one account. The
    # tight window here stops the same grind from one address before lockout
    # even engages; the loose one stops a spray across many accounts without
    # punishing an office that shares a NAT egress.
    # The burst tier is charged now; the per-account tier is only checked, and
    # charged below if the attempt fails. Signing in correctly is not something
    # to hold against someone.
    attempt_key = ratelimit.enforce_pair(
        ratelimit.LOGIN, ratelimit.LOGIN_BURST, request, "sign-in", subject=email
    )
    user = db.execute(
        select(User).where(User.tenant_id == settings.default_tenant, User.email == email)
    ).scalar_one_or_none()

    if user is None:
        ratelimit.penalise(ratelimit.LOGIN, attempt_key)
        # Still burn a bcrypt round so timing does not reveal the account's absence.
        verify_password(payload.password, None)
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            summary=f"Failed sign-in for unknown address {email}",
            tenant_id=settings.default_tenant,
            request=request,
        )
        db.commit()
        raise _BAD_CREDENTIALS

    now = naive_utcnow()

    if user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            summary=f"Sign-in attempt on locked account {user.email}",
            tenant_id=user.tenant_id,
            actor=user,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Too many failed attempts. Try again in {remaining} minute(s).",
        )

    if not verify_password(payload.password, user.password_hash):
        ratelimit.penalise(ratelimit.LOGIN, attempt_key)
        user.failed_login_count += 1
        locked = user.failed_login_count >= MAX_FAILED_ATTEMPTS
        if locked:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            summary=(
                f"Failed sign-in for {user.email}"
                + (f" - account locked for {LOCKOUT_MINUTES} minutes" if locked else "")
            ),
            tenant_id=user.tenant_id,
            actor=user,
            request=request,
        )
        db.commit()
        raise _BAD_CREDENTIALS

    if not user.is_active:
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            summary=f"Sign-in attempt on deactivated account {user.email}",
            tenant_id=user.tenant_id,
            actor=user,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact an administrator.",
        )

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    token, expires_at = create_access_token(
        user_id=user.id, role=str(user.role), tenant_id=user.tenant_id
    )
    audit.record(
        db,
        action=AuditAction.LOGIN,
        entity_type="user",
        entity_id=user.id,
        summary=f"{user.full_name} signed in",
        tenant_id=user.tenant_id,
        actor=user,
        request=request,
    )
    db.commit()
    db.refresh(user)

    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserProfile.model_validate(user),
    )


@router.post("/logout", response_model=MessageResponse)
def logout(user: CurrentUser, request: Request, db: DbSession) -> MessageResponse:
    """Access tokens are stateless, so this records the intent rather than
    revoking anything. The client discards the token; the ledger keeps the line."""
    audit.record(
        db,
        action=AuditAction.LOGOUT,
        entity_type="user",
        entity_id=user.id,
        summary=f"{user.full_name} signed out",
        tenant_id=user.tenant_id,
        actor=user,
        request=request,
    )
    db.commit()
    return MessageResponse(detail="Signed out.")


@router.get("/me", response_model=UserProfile)
def me(user: CurrentUser) -> UserProfile:
    return UserProfile.model_validate(user)


@router.patch("/me/theme", response_model=UserProfile)
def set_theme(payload: ThemePreferenceRequest, user: CurrentUser, db: DbSession) -> UserProfile:
    """Persisted server-side so the choice follows the user across devices
    (addendum B11). Not audited - it is a preference, not an action."""
    user.theme_preference = payload.theme_preference
    db.commit()
    db.refresh(user)
    return UserProfile.model_validate(user)


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, request: Request, db: DbSession
) -> MessageResponse:
    if not verify_password(payload.current_password, user.password_hash):
        audit.record(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            summary=f"{user.full_name} gave the wrong current password when changing it",
            tenant_id=user.tenant_id,
            actor=user,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect."
        )

    try:
        validate_password(payload.new_password, email=user.email, name=user.full_name)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    user.password_hash = hash_password(payload.new_password)
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{user.full_name} changed their password",
        # The ledger records that it changed, never what it changed to.
        changes={"password": {"from": "***", "to": "***"}},
        tenant_id=user.tenant_id,
        actor=user,
        request=request,
    )
    db.commit()
    return MessageResponse(detail="Password updated.")


@router.get("/token/{raw_token}", response_model=TokenPreview)
def preview_token(raw_token: str, request: Request, db: DbSession) -> TokenPreview:
    """Confirm a link is live before asking someone to type a new password."""
    ratelimit.enforce(ratelimit.TOKEN, request, "token")
    token = db.execute(
        select(AuthToken).where(AuthToken.token_hash == hash_url_token(raw_token))
    ).scalar_one_or_none()

    if token is None or not token.is_usable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link is invalid or has expired. Ask an administrator for a new one.",
        )

    return TokenPreview(
        full_name=token.user.full_name,
        email=token.user.email,
        purpose=str(token.purpose),
    )


@router.post("/set-password", response_model=MessageResponse)
def set_password(payload: SetPasswordRequest, request: Request, db: DbSession) -> MessageResponse:
    """Redeem an invite or reset token. Single use, enforced by `used_at`."""
    ratelimit.enforce(ratelimit.TOKEN, request, "token")
    token = db.execute(
        select(AuthToken)
        .where(AuthToken.token_hash == hash_url_token(payload.token))
        .with_for_update()
    ).scalar_one_or_none()

    if token is None or not token.is_usable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link is invalid or has expired. Ask an administrator for a new one.",
        )

    user = token.user
    try:
        validate_password(payload.password, email=user.email, name=user.full_name)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    first_time = not user.has_password

    user.password_hash = hash_password(payload.password)
    # Redeeming a reset is also the way out of a lockout.
    user.failed_login_count = 0
    user.locked_until = None
    token.used_at = naive_utcnow()

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=(
            f"{user.full_name} accepted their invitation and set a password"
            if first_time
            else f"{user.full_name} reset their password"
        ),
        changes={"password": {"from": "***", "to": "***"}},
        tenant_id=user.tenant_id,
        actor=user,
        request=request,
    )
    db.commit()

    return MessageResponse(
        detail="Password set. You can sign in now."
        if first_time
        else "Password reset. You can sign in now."
    )


@router.post("/forgot-password", response_model=InviteLinkResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> InviteLinkResponse:
    """Always answers the same way, whether or not the address is real.

    Rate limited harder than sign-in: each call can send mail, and an
    unauthenticated endpoint that sends mail is a way to use this server to
    harass someone.

    The link is emailed to the account's own address, after the response has
    gone, so the answer takes the same time whether or not the account exists.
    It never goes into the response, which would turn this endpoint into an
    account-takeover primitive for anyone who can guess an address.
    """
    # Both tiers are charged outright here, unlike sign-in. This endpoint
    # answers identically whether or not the address exists and can send mail
    # either way, so there is no "failure" to charge against - every request is
    # the thing being limited.
    ratelimit.enforce(ratelimit.RESET_BURST, request, "password reset")
    ratelimit.enforce(
        ratelimit.RESET, request, "password reset", subject=payload.email
    )

    generic = InviteLinkResponse(
        detail="If that address belongs to an account, a reset link is on its way."
    )

    email = payload.email.strip().lower()
    user = db.execute(
        select(User).where(User.tenant_id == settings.default_tenant, User.email == email)
    ).scalar_one_or_none()

    if user is None or not user.is_active:
        return generic

    raw, _ = _issue_token(db, user, TokenPurpose.PASSWORD_RESET, valid_hours=RESET_VALID_HOURS)
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"Password reset requested for {user.email}",
        tenant_id=user.tenant_id,
        request=request,
    )
    db.commit()

    logger.info("Password reset link issued for %s", user.email)
    background.add_task(
        email_service.send_account_link,
        user.email,
        user.full_name,
        build_invite_url(raw),
        purpose="reset",
        valid_hours=RESET_VALID_HOURS,
    )
    return generic
