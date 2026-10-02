"""
Projects and campaigns (SOW section 2).

Admins get full CRUD, with one limit on delete: a campaign with requests
against it - drafts and cancelled ones included - cannot be removed without
erasing that history, so `ARCHIVED` hides it from the request dropdowns instead.
Only a campaign created by mistake, with no requests, can be deleted. The
built-in "Other" campaign can be neither: the request form needs it. Ground
staff get a read-only list of what they are allowed to tag a request against.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction, ProjectStatus
from app.models.project import Project
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectRead,
    ProjectUpdate,
)
from app.services import audit, locations
from app.services.projects import generate_code, is_fallback, request_counts
from app.services.seed import OTHER_PROJECT_CODE, ensure_other_project

router = APIRouter(prefix="/projects", tags=["projects"])

#: How many times create tries an automatic code that lost a race to another
#: admin creating a same-named campaign in the same moment.
_CODE_ATTEMPTS = 3

#: Live campaigns first, put-away ones last - not alphabetical on the enum.
_STATUS_ORDER = case(
    {
        ProjectStatus.ACTIVE: 0,
        ProjectStatus.PAUSED: 1,
        ProjectStatus.COMPLETED: 2,
        ProjectStatus.ARCHIVED: 3,
    },
    value=Project.status,
    else_=4,
)


def _to_read(project: Project, request_count: int = 0) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        code=project.code,
        description=project.description,
        client_name=project.client_name,
        state=project.state,
        city=project.city,
        location=project.location,
        status=project.status,
        start_date=project.start_date,
        end_date=project.end_date,
        created_at=project.created_at,
        accepts_requests=project.accepts_requests,
        request_count=request_count,
        is_fallback=is_fallback(project),
    )


def _count(db: DbSession, project: Project) -> int:
    return request_counts(db, project.tenant_id, [project.id]).get(project.id, 0)


def _get_or_404(db: DbSession, project_id: int, tenant_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def _refuse_typed_code(
    db: DbSession, tenant_id: str, code: str, exclude_id: int | None = None
) -> None:
    """409 when a typed code is already taken, or is the built-in campaign's."""
    if code.upper() == OTHER_PROJECT_CODE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{OTHER_PROJECT_CODE} is kept for the built-in Other campaign. "
                "Type another code, or leave it blank."
            ),
        )
    query = select(Project).where(Project.tenant_id == tenant_id, Project.code == code)
    if exclude_id is not None:
        query = query.where(Project.id != exclude_id)
    clash = db.execute(query).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Code {code} is already used by “{clash.name}”.",
        )


def _fallback_locked(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"The built-in Other campaign can't be {what}. The request form needs it.",
    )


def _in_use(name: str, count: int) -> str:
    noun = "request" if count == 1 else "requests"
    return (
        f"{name} has {count} {noun}, so it can't be deleted. Archive it instead; "
        "that hides it from new requests and keeps its history."
    )


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
    # The "Other" campaign is normally created at startup, but that step can
    # fail on a server that started before its migration ran. Making sure of
    # it here means the request form's "Other" option cannot silently vanish.
    ensure_other_project(db, user.tenant_id)
    db.commit()

    filters = [Project.tenant_id == user.tenant_id]

    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(
                Project.name.like(like),
                Project.code.like(like),
                Project.client_name.like(like),
                Project.state.like(like),
                Project.city.like(like),
            )
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
            .order_by(_STATUS_ORDER, Project.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    counts = request_counts(db, user.tenant_id, [p.id for p in rows])
    return ProjectListResponse(
        items=[_to_read(p, counts.get(p.id, 0)) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate, actor: AdminUser, request: Request, db: DbSession
) -> ProjectRead:
    if payload.code is not None:
        _refuse_typed_code(db, actor.tenant_id, payload.code)
    # Spelling cleaned the way request places are, so "hyd" is Hyderabad.
    state, city = locations.canonical(db, actor.tenant_id, payload.state, payload.city)
    fields = payload.model_dump(exclude={"code", "state", "city"})

    project: Project | None = None
    for _ in range(_CODE_ATTEMPTS):
        candidate = Project(
            tenant_id=actor.tenant_id,
            created_by_id=actor.id,
            code=payload.code
            or generate_code(db, actor.tenant_id, payload.name, payload.start_date),
            state=state,
            city=city,
            **fields,
        )
        try:
            # A savepoint, so losing the race for a code undoes only this insert.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            if payload.code is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Code {payload.code} is already used by another campaign.",
                ) from None
            continue
        project = candidate
        break
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not make a unique code for this campaign. Type one in and try again.",
        )

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
            "state": {"from": None, "to": project.state},
            "city": {"from": None, "to": project.city},
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
    return _to_read(project, _count(db, project))


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

    if is_fallback(project):
        # The request form and seed find it by its code, and offer it only
        # while it is Active. Its name and description are free to change.
        if "code" in updates and updates["code"] != project.code:
            raise _fallback_locked("given another code")
        if updates.get("status", ProjectStatus.ACTIVE) is not ProjectStatus.ACTIVE:
            raise _fallback_locked("paused, completed or archived")

    if "code" in updates:
        if updates["code"] is None:
            # Blank means "make one for me", on edit as on create. Excluding
            # itself means an unchanged automatic code comes back the same.
            updates["code"] = generate_code(
                db,
                actor.tenant_id,
                updates.get("name") or project.name,
                updates.get("start_date", project.start_date),
                exclude_id=project.id,
            )
        elif updates["code"] != project.code:
            _refuse_typed_code(db, actor.tenant_id, updates["code"], exclude_id=project.id)

    if "state" in updates or "city" in updates:
        updates["state"], updates["city"] = locations.canonical(
            db,
            actor.tenant_id,
            updates.get("state", project.state),
            updates.get("city", project.city),
        )
        if updates["state"] is not None:
            # The picked state supersedes the old free text. Only then: an
            # admin editing just the name keeps an unmatched legacy value.
            updates["location"] = None

    # The schema checks a pair sent together; this checks one date against the
    # other already stored.
    start = updates.get("start_date", project.start_date)
    end = updates.get("end_date", project.end_date)
    if start and end and end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="End date cannot be before the start date.",
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
    try:
        db.commit()
    except IntegrityError:
        # Another admin took the same code in the same moment.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That code was just taken by another campaign. Try again.",
        ) from None
    db.refresh(project)
    return _to_read(project, _count(db, project))


@router.post("/{project_id}/archive", response_model=ProjectRead)
def archive_project(
    project_id: int, actor: AdminUser, request: Request, db: DbSession
) -> ProjectRead:
    """Hide a campaign from new requests, keeping every past request intact.

    This is what the SOW's "delete" means for a campaign with requests - see
    addendum B4. One with none can also be deleted outright.
    """
    project = _get_or_404(db, project_id, actor.tenant_id)
    if is_fallback(project):
        raise _fallback_locked("archived")
    if project.status is ProjectStatus.ARCHIVED:
        return _to_read(project, _count(db, project))

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
    return _to_read(project, _count(db, project))


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
    return _to_read(project, _count(db, project))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int, actor: AdminUser, request: Request, db: DbSession
) -> Response:
    """Remove a campaign created by mistake.

    Only one nothing points at: a request of any status, a draft or a cancelled
    one too, is history - and the foreign key would refuse anyway.
    """
    project = _get_or_404(db, project_id, actor.tenant_id)
    if is_fallback(project):
        raise _fallback_locked("deleted")
    in_use = _count(db, project)
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_in_use(project.name, in_use)
        )

    name = project.name
    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="project",
        entity_id=project.id,
        summary=f"{actor.full_name} deleted campaign {project.code} – {name}",
        changes={
            "name": {"from": name, "to": None},
            "code": {"from": project.code, "to": None},
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.delete(project)
    try:
        db.commit()
    except IntegrityError:
        # A request was raised against it between the count and the delete.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_in_use(
                name, request_counts(db, actor.tenant_id, [project_id]).get(project_id, 1)
            ),
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
