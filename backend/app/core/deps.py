"""
Request-scoped dependencies: who is calling, and may they.

Authorisation is a dependency rather than a check inside each handler, so a new
endpoint is insecure by omission only if someone actively leaves the guard off,
and the requirement shows up in the OpenAPI schema.
"""
from __future__ import annotations

from datetime import timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import Role
from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User
from app.services import accounts

settings = get_settings()

# auto_error=False so a missing header produces our own 401 with a useful body
# rather than FastAPI's bare "Not authenticated".
_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not signed in, or the session has expired.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_tenant_id(request: Request) -> str:
    """Resolve the tenant for this request.

    Single-tenant today, so this is the configured default. It exists as a
    dependency now so that every query is already tenant-scoped when a second
    company is onboarded - see addendum D.
    """
    authenticated = getattr(request.state, "tenant_id", None)
    return authenticated or settings.default_tenant


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the signed-in user, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _UNAUTHENTICATED

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise _UNAUTHENTICATED from None

    user = db.get(User, user_id)
    if user is None:
        raise _UNAUTHENTICATED

    # A password change or reset signs out every other device: a token minted
    # before it no longer counts. `iat` is whole seconds, so a token issued in
    # the same second as the change survives - acceptable, and it is what lets
    # the device that made the change carry on with its fresh token.
    if user.password_changed_at is not None:
        cutoff = int(user.password_changed_at.replace(tzinfo=timezone.utc).timestamp())
        issued = payload.get("iat")
        if not isinstance(issued, int) or issued < cutoff:
            raise _UNAUTHENTICATED

    # A token outlives a status change, so the live row is the authority - not
    # the claims baked into the token when it was signed. 401 rather than 403,
    # so the client drops the session at once (it signs out on any 401) and
    # shows the person why, instead of leaving them in a shell where every
    # call fails.
    blocked = accounts.blocked_message(user)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=blocked,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.has_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has not finished setup. Use your invitation link.",
        )

    request.state.tenant_id = user.tenant_id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
TenantId = Annotated[str, Depends(get_tenant_id)]


def require_roles(*allowed: Role):
    """Dependency factory guarding an endpoint behind one or more roles."""

    def guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not permit this action.",
            )
        return user

    return guard


require_admin = require_roles(Role.ADMIN, Role.SYSTEM_ADMIN)
require_system_admin = require_roles(Role.SYSTEM_ADMIN)

AdminUser = Annotated[User, Depends(require_admin)]
SystemAdminUser = Annotated[User, Depends(require_system_admin)]
