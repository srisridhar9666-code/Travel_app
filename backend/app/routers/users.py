"""
User administration (SOW section 5, directory management).

Accounts are created by an admin and activated by the person themselves through
an emailed invite link. There is no self-registration - addendum B10.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction, Role, TokenPurpose
from app.models.base import naive_utcnow
from app.models.user import User
from app.routers.auth import INVITE_VALID_HOURS, _issue_token, build_invite_url
from app.schemas.auth import InviteLinkResponse
from app.schemas.bulk import ImportPreview, ImportResult
from app.schemas.user import UserCreate, UserListResponse, UserRead, UserUpdate
from app.services import audit, bulk_import, history

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/users", tags=["users"])


def _to_read(user: User) -> UserRead:
    now = naive_utcnow()
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        employee_code=user.employee_code,
        role=user.role,
        designation=user.designation,
        gender=user.gender,
        phone=user.phone,
        base_location=user.base_location,
        is_active=user.is_active,
        exited_on=user.exited_on,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        has_password=user.has_password,
        is_locked=bool(user.locked_until and user.locked_until > now),
    )


def _guard_role_assignment(actor: User, target_role: Role | None) -> None:
    """Only a system admin may mint another system admin.

    Without this, any admin could promote themselves past the role that governs
    user management and the audit log.
    """
    if target_role is Role.SYSTEM_ADMIN and actor.role is not Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a system administrator can grant the system administrator role.",
        )


@router.get("", response_model=UserListResponse)
def list_users(
    actor: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=120)] = None,
    role: Annotated[Role | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> UserListResponse:
    filters = [User.tenant_id == actor.tenant_id]
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(User.full_name.like(like), User.email.like(like), User.employee_code.like(like))
        )
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))

    total = db.execute(select(func.count()).select_from(User).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(User)
            .where(*filters)
            .order_by(User.full_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return UserListResponse(
        items=[_to_read(u) for u in rows], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=InviteLinkResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate, actor: AdminUser, request: Request, db: DbSession
) -> InviteLinkResponse:
    _guard_role_assignment(actor, payload.role)

    existing = db.execute(
        select(User).where(User.tenant_id == actor.tenant_id, User.email == payload.email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.email} already has an account.",
        )

    user = User(
        tenant_id=actor.tenant_id,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        designation=payload.designation,
        gender=payload.gender,
        phone=payload.phone,
        employee_code=payload.employee_code,
        base_location=payload.base_location,
        password_hash=None,  # set by the invitee, never by the admin
        created_by_id=actor.id,
    )
    db.add(user)
    db.flush()

    raw, token = _issue_token(
        db, user, TokenPurpose.INVITE, valid_hours=INVITE_VALID_HOURS, created_by=actor
    )

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} invited {user.full_name} ({user.email}) as {user.role}",
        changes={
            "email": {"from": None, "to": user.email},
            "full_name": {"from": None, "to": user.full_name},
            "role": {"from": None, "to": str(user.role)},
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    invite_url = build_invite_url(raw)
    logger.info("Invite issued for %s: %s", user.email, invite_url)

    # Returned to the inviting admin only, over an authenticated request, so
    # they can relay it until Phase 6 wires email delivery.
    return InviteLinkResponse(
        detail=f"{user.full_name} invited. Send them this link to set a password.",
        invite_url=invite_url,
        expires_at=token.expires_at,
    )


# ---------------------------------------------------------------------------
# Bulk import. Declared before "/{user_id}" so the literal paths win.
# ---------------------------------------------------------------------------

@router.get("/import/template")
def import_template(actor: AdminUser) -> Response:
    """A starter CSV with the headers and one worked example row."""
    return Response(
        content=bulk_import.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="team-import-template.csv"'},
    )


@router.post("/import/preview", response_model=ImportPreview)
async def import_preview(
    actor: AdminUser, db: DbSession, file: Annotated[UploadFile, File()]
) -> ImportPreview:
    """Parse and validate without writing anything.

    Creating a hundred accounts, each with an invitation, is not something an
    admin should first understand the shape of after it has happened.
    """
    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That file is larger than the {settings.max_upload_mb} MB limit.",
        )
    return bulk_import.parse(raw, db, actor.tenant_id)


@router.post("/import", response_model=ImportResult)
async def import_commit(
    actor: AdminUser,
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> ImportResult:
    """Create every importable row, each with an invitation link.

    Re-parses the same file rather than trusting a preview the client echoes
    back, so what gets written is always what the server itself validated.
    Invalid rows are skipped, not fatal - one bad line should not cost the
    admin the other ninety-nine.
    """
    raw = await file.read()
    preview = bulk_import.parse(raw, db, actor.tenant_id)

    if preview.file_errors and not preview.rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=preview.file_errors[0],
        )

    invite_urls: dict[str, str] = {}
    errors: list[str] = []
    created = 0

    for row in preview.rows:
        if not row.importable or not row.email or not row.full_name:
            continue

        _guard_role_assignment(actor, row.role)

        user = User(
            tenant_id=actor.tenant_id,
            email=row.email,
            full_name=row.full_name,
            role=row.role,
            designation=row.designation,
            gender=row.gender,
            phone=row.phone,
            employee_code=row.employee_code,
            base_location=row.base_location,
            password_hash=None,
            created_by_id=actor.id,
        )
        db.add(user)
        db.flush()

        token_raw, _ = _issue_token(
            db, user, TokenPurpose.INVITE, valid_hours=INVITE_VALID_HOURS, created_by=actor
        )
        invite_urls[row.email] = build_invite_url(token_raw)
        created += 1

    if created:
        audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="user",
            summary=(
                f"{actor.full_name} bulk-imported {created} account(s) "
                f"from {file.filename or 'a CSV'}"
            ),
            changes={"imported": {"from": None, "to": sorted(invite_urls)}},
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    db.commit()

    return ImportResult(
        created=created,
        skipped=preview.skipped,
        invite_urls=invite_urls,
        errors=errors + preview.file_errors,
    )


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: int, actor: AdminUser, db: DbSession) -> UserRead:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _to_read(user)


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int, payload: UserUpdate, actor: AdminUser, request: Request, db: DbSession
) -> UserRead:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    updates = payload.model_dump(exclude_unset=True)
    _guard_role_assignment(actor, updates.get("role"))

    # Nobody edits their own role or switches off their own account - either
    # would let an admin lock the organisation out of its own admin tier.
    if user.id == actor.id:
        if "role" in updates and updates["role"] != user.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role.",
            )
        if updates.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own account.",
            )

    before = {key: getattr(user, key) for key in updates}
    for key, value in updates.items():
        setattr(user, key, value)
    after = {key: getattr(user, key) for key in updates}

    changes = audit.diff(before, after)
    if changes:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=user.id,
            summary=f"{actor.full_name} updated {user.full_name} ({', '.join(sorted(changes))})",
            changes=changes,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    db.commit()
    db.refresh(user)
    return _to_read(user)


@router.post("/{user_id}/reinvite", response_model=InviteLinkResponse)
def reinvite(
    user_id: int, actor: AdminUser, request: Request, db: DbSession
) -> InviteLinkResponse:
    """Issue a fresh invite link, invalidating any earlier one."""
    user = db.get(User, user_id)
    if user is None or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reactivate this account before inviting them again.",
        )

    purpose = TokenPurpose.INVITE if not user.has_password else TokenPurpose.PASSWORD_RESET
    raw, token = _issue_token(
        db, user, purpose, valid_hours=INVITE_VALID_HOURS, created_by=actor
    )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} issued a new {purpose.lower().replace('_', ' ')} link for {user.email}",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    return InviteLinkResponse(
        detail=f"New link generated for {user.full_name}.",
        invite_url=build_invite_url(raw),
        expires_at=token.expires_at,
    )


@router.post("/{user_id}/unlock", response_model=UserRead)
def unlock(user_id: int, actor: AdminUser, request: Request, db: DbSession) -> UserRead:
    """Clear a failed-attempt lockout without waiting it out."""
    user = db.get(User, user_id)
    if user is None or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.locked_until = None
    user.failed_login_count = 0
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} unlocked {user.full_name}'s account",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(user)
    return _to_read(user)


@router.get("/{user_id}/travel-history")
def travel_history(
    user_id: int,
    actor: CurrentUser,
    db: DbSession,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> dict:
    """Where this person has been, and with whom (SOW section 5).

    Built from the traveller rows rather than from requests, so a movement someone
    was *tagged onto* appears too - they were in the cab even though the request
    was not theirs.

    Anyone may read their own timeline; only an admin may read someone else's.
    Costs are included only for admins: a ground staff member reading their own
    history has no business seeing what the company paid for their seat.
    """
    if actor.id != user_id and not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own travel history.",
        )

    target = db.get(User, user_id)
    if target is None or target.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    result = history.build(
        db,
        user_id=user_id,
        tenant_id=actor.tenant_id,
        since=since,
        until=until,
        include_costs=actor.is_admin,
    )
    result["full_name"] = target.full_name
    result["email"] = target.email
    result["designation"] = str(target.designation) if target.designation else None
    return result


@router.get("/{user_id}/self-check", response_model=UserRead, include_in_schema=False)
def self_check(user_id: int, actor: CurrentUser, db: DbSession) -> UserRead:
    """A non-admin may read only their own row. Used by the profile screen."""
    if actor.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _to_read(user)
