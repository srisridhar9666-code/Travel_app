"""
Projects and campaigns (SOW section 2).

Admins get full CRUD, with one deliberate exception: there is no hard delete.
A campaign with requests against it cannot be removed without erasing that
history, so `ARCHIVED` hides it from the request dropdowns instead. Ground staff
get a read-only list of what they are allowed to tag a request against.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction, ProjectStatus
from app.models.project import Project
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectRead,
    ProjectUpdate,
)
from app.services import audit

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        code=project.code,
        description=project.description,
        client_name=project.client_name,
        location=project.location,
        status=project.status,
        start_date=project.start_date,
        end_date=project.end_date,
        created_at=project.created_at,
        accepts_requests=project.accepts_requests,
    )


def _get_or_404(db: DbSession, project_id: int, tenant_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


@router.get("", response_model=ProjectListResponse)
def list_projects(
    user: CurrentUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=120)] = None,
    project_status: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProjectListResponse:
    """Every signed-in user can read the campaign list - ground staff need it to
    tag a request. Only admins can change it."""
    filters = [Project.tenant_id == user.tenant_id]

    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(Project.name.like(like), Project.code.like(like), Project.client_name.like(like))
        )
    if project_status is not None:
        filters.append(Project.status == project_status)
    elif not user.is_admin:
        # Ground staff never see archived campaigns; an archived campaign is one
        # they must not be raising new requests against.
        filters.append(Project.status != ProjectStatus.ARCHIVED)

    total = db.execute(select(func.count()).select_from(Project).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(Project)
            .where(*filters)
            .order_by(Project.status, Project.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return ProjectListResponse(
        items=[_to_read(p) for p in rows], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate, actor: AdminUser, request: Request, db: DbSession
) -> ProjectRead:
    clash = db.execute(
        select(Project).where(
            Project.tenant_id == actor.tenant_id, Project.code == payload.code
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Code {payload.code} is already used by “{clash.name}”.",
        )

    project = Project(
        tenant_id=actor.tenant_id, created_by_id=actor.id, **payload.model_dump()
    )
    db.add(project)
    db.flush()

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="project",
        entity_id=project.id,
        summary=f"{actor.full_name} created campaign {project.code} – {project.name}",
        changes={
            "name": {"from": None, "to": project.name},
            "code": {"from": None, "to": project.code},
            "status": {"from": None, "to": str(project.status)},
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(project)
    return _to_read(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, user: CurrentUser, db: DbSession) -> ProjectRead:
    project = _get_or_404(db, project_id, user.tenant_id)
    if not user.is_admin and project.status is ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return _to_read(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    actor: AdminUser,
    request: Request,
    db: DbSession,
) -> ProjectRead:
    project = _get_or_404(db, project_id, actor.tenant_id)
    updates = payload.model_dump(exclude_unset=True)

    if "code" in updates and updates["code"] != project.code:
        clash = db.execute(
            select(Project).where(
                Project.tenant_id == actor.tenant_id,
                Project.code == updates["code"],
                Project.id != project.id,
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code {updates['code']} is already used by “{clash.name}”.",
            )

    before = {key: getattr(project, key) for key in updates}
    for key, value in updates.items():
        setattr(project, key, value)
    after = {key: getattr(project, key) for key in updates}

    changes = audit.diff(before, after)
    if changes:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="project",
            entity_id=project.id,
            summary=(
                f"{actor.full_name} updated campaign {project.code} "
                f"({', '.join(sorted(changes))})"
            ),
            changes=changes,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    db.commit()
    db.refresh(project)
    return _to_read(project)


@router.post("/{project_id}/archive", response_model=ProjectRead)
def archive_project(
    project_id: int, actor: AdminUser, request: Request, db: DbSession
) -> ProjectRead:
    """Hide a campaign from new requests, keeping every past request intact.

    This is what the SOW's "delete" means in practice - see addendum B4.
    """
    project = _get_or_404(db, project_id, actor.tenant_id)
    if project.status is ProjectStatus.ARCHIVED:
        return _to_read(project)

    previous = str(project.status)
    project.status = ProjectStatus.ARCHIVED

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="project",
        entity_id=project.id,
        summary=f"{actor.full_name} archived campaign {project.code} – {project.name}",
        changes={"status": {"from": previous, "to": str(ProjectStatus.ARCHIVED)}},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(project)
    return _to_read(project)


@router.post("/{project_id}/restore", response_model=ProjectRead)
def restore_project(
    project_id: int, actor: AdminUser, request: Request, db: DbSession
) -> ProjectRead:
    project = _get_or_404(db, project_id, actor.tenant_id)
    previous = str(project.status)
    project.status = ProjectStatus.ACTIVE

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="project",
        entity_id=project.id,
        summary=f"{actor.full_name} restored campaign {project.code} to active",
        changes={"status": {"from": previous, "to": str(ProjectStatus.ACTIVE)}},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(project)
    return _to_read(project)
