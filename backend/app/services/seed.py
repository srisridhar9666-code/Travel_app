"""
First-run bootstrap.

Creates exactly one system administrator so there is someone to sign in as. It
is idempotent, and it never touches an existing account - a deployment must not
be able to silently reset the admin password by restarting.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import AuditAction, ProjectStatus, Role
from app.core.security import hash_password
from app.models.project import Project
from app.models.user import User
from app.services import audit

logger = logging.getLogger(__name__)
settings = get_settings()


def ensure_bootstrap_admin(db: Session) -> User | None:
    """Create the first system admin if the tenant has no admin at all."""
    existing_admin = db.execute(
        select(User).where(
            User.tenant_id == settings.default_tenant,
            User.role.in_([Role.SYSTEM_ADMIN, Role.ADMIN]),
        )
    ).scalars().first()

    if existing_admin is not None:
        return None

    email = settings.admin_email.strip().lower()
    admin = User(
        tenant_id=settings.default_tenant,
        email=email,
        full_name=settings.admin_name,
        role=Role.SYSTEM_ADMIN,
        password_hash=hash_password(settings.admin_password),
        is_active=True,
    )
    db.add(admin)
    db.flush()

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="user",
        entity_id=admin.id,
        summary=f"Bootstrap system administrator {admin.email} created on first run",
        tenant_id=admin.tenant_id,
    )
    db.commit()

    logger.warning(
        "Created bootstrap admin %s with the password from ADMIN_PASSWORD. "
        "Change it immediately - it is in a config file.",
        admin.email,
    )
    return admin


#: Code of the campaign that carries requests whose project is not on the list.
OTHER_PROJECT_CODE = "OTHER"


def ensure_other_project(db: Session, tenant_id: str) -> Project:
    """The campaign that "Other" requests point at.

    Ground staff can raise a request before a campaign has been set up, and
    blocking them on an admin creating one first would mean the trip happens
    anyway and the system simply does not know about it. So the request points
    here and carries the name they typed in `other_project_name`.

    A real project rather than a null `project_id`: every join, report and
    analytics grouping keeps working, and the untriaged requests collect in one
    visible place instead of becoming a special case in eleven files.
    """
    existing = db.execute(
        select(Project).where(
            Project.tenant_id == tenant_id, Project.code == OTHER_PROJECT_CODE
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    project = Project(
        tenant_id=tenant_id,
        name="Other / not yet listed",
        code=OTHER_PROJECT_CODE,
        description=(
            "Requests raised before their campaign existed. The name the "
            "requester typed is on the request; reassign it to a real campaign "
            "once one is created."
        ),
        status=ProjectStatus.ACTIVE,
    )
    db.add(project)
    db.flush()
    logger.info("Created the fallback %r campaign for %s", OTHER_PROJECT_CODE, tenant_id)
    return project
