"""
Departments: the part of the organisation someone works in.

Admins add them as they need them, usually from the employee form while adding
a person. Reporting only - what someone may do in the app is their role, which
stays a fixed list because every permission check reads it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select

from app.core.deps import AdminUser, DbSession
from app.core.enums import AuditAction, UserStatus
from app.models.department import Department
from app.models.user import User
from app.schemas.department import DepartmentCreate, DepartmentRead
from app.services import audit

router = APIRouter(prefix="/departments", tags=["departments"])


def _member_count(db: DbSession, department_id: int) -> int:
    return db.execute(
        select(func.count(User.id)).where(
            User.department_id == department_id, User.status != UserStatus.DELETED
        )
    ).scalar_one()


def _get(db: DbSession, actor: AdminUser, department_id: int) -> Department:
    found = db.get(Department, department_id)
    if found is None or found.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    return found


def _by_name(db: DbSession, tenant_id: str, name: str) -> Department | None:
    # An equality match is enough: the column's collation ignores case and
    # accents, which is also what the unique index enforces.
    return db.execute(
        select(Department).where(Department.tenant_id == tenant_id, Department.name == name)
    ).scalar_one_or_none()


@router.get("", response_model=list[DepartmentRead])
def list_departments(actor: AdminUser, db: DbSession) -> list[DepartmentRead]:
    counts = dict(
        db.execute(
            select(User.department_id, func.count(User.id))
            .where(
                User.tenant_id == actor.tenant_id,
                User.department_id.is_not(None),
                User.status != UserStatus.DELETED,
            )
            .group_by(User.department_id)
        ).all()
    )
    rows = db.execute(
        select(Department).where(Department.tenant_id == actor.tenant_id).order_by(Department.name)
    ).scalars()
    return [
        DepartmentRead(id=row.id, name=row.name, member_count=counts.get(row.id, 0)) for row in rows
    ]


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    actor: AdminUser,
    request: Request,
    response: Response,
    db: DbSession,
) -> DepartmentRead:
    """Add a department, or return the one that already has this name.

    An existing name answers 200 with that row rather than 409: the picker
    only wants "the department called X", and a double click or two admins
    typing the same name at once should both simply get it.
    """
    existing = _by_name(db, actor.tenant_id, payload.name)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return DepartmentRead(
            id=existing.id, name=existing.name, member_count=_member_count(db, existing.id)
        )

    row = Department(tenant_id=actor.tenant_id, name=payload.name)
    db.add(row)
    db.flush()
    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="department",
        entity_id=row.id,
        summary=f"{actor.full_name} added the department {row.name}",
        changes={"name": {"from": None, "to": row.name}},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    return DepartmentRead(id=row.id, name=row.name, member_count=0)


@router.patch("/{department_id}", response_model=DepartmentRead)
def rename_department(
    department_id: int,
    payload: DepartmentCreate,
    actor: AdminUser,
    request: Request,
    db: DbSession,
) -> DepartmentRead:
    row = _get(db, actor, department_id)
    clash = _by_name(db, actor.tenant_id, payload.name)
    if clash is not None and clash.id != row.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"There is already a department called {clash.name}.",
        )

    if row.name != payload.name:
        old = row.name
        row.name = payload.name
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="department",
            entity_id=row.id,
            summary=f"{actor.full_name} renamed the department {old} to {row.name}",
            changes={"name": {"from": old, "to": row.name}},
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
        db.commit()
    return DepartmentRead(id=row.id, name=row.name, member_count=_member_count(db, row.id))


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    department_id: int, actor: AdminUser, request: Request, db: DbSession
) -> Response:
    """Only an empty department can go, so nobody silently loses theirs.
    Deleted accounts do not count; their link is simply cleared."""
    row = _get(db, actor, department_id)
    members = _member_count(db, row.id)
    if members:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{row.name} still has {members} "
                f"{'person' if members == 1 else 'people'} in it. "
                "Move them to another department first."
            ),
        )

    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="department",
        entity_id=row.id,
        summary=f"{actor.full_name} deleted the department {row.name}",
        changes={"name": {"from": row.name, "to": None}},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
