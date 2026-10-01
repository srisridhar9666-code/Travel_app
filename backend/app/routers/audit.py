"""
Reading the activity ledger (SOW section 7).

Read-only by construction: there is no endpoint here that writes, updates or
deletes a row, and `verify` exposes the tamper check so the log's integrity is
something an administrator can confirm rather than take on faith.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import and_, func, or_, select

from app.core.deps import DbSession, SystemAdminUser
from app.core.enums import AuditAction
from app.models.audit import AuditLog
from app.models.request import RequestTraveller
from app.models.ticket import TicketDocument
from app.schemas.user import AuditListResponse, AuditRead
from app.services import audit as audit_service
from app.services import ledger_guard

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def list_audit(
    actor: SystemAdminUser,
    db: DbSession,
    action: Annotated[AuditAction | None, Query()] = None,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: Annotated[int | None, Query()] = None,
    actor_user_id: Annotated[int | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditListResponse:
    filters = [AuditLog.tenant_id == actor.tenant_id]
    if action is not None:
        filters.append(AuditLog.action == action)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        filters.append(AuditLog.entity_id == entity_id)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if since is not None:
        filters.append(AuditLog.created_at >= since.replace(tzinfo=None))
    if until is not None:
        filters.append(AuditLog.created_at <= until.replace(tzinfo=None))

    total = db.execute(select(func.count()).select_from(AuditLog).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return AuditListResponse(
        items=[AuditRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/verify")
def verify(actor: SystemAdminUser, db: DbSession) -> dict:
    """Walk the hash chain and report the first break, if any.

    A clean result means no row has been edited, removed or reordered since it
    was written. This is what section 7's "immutable" claim actually rests on.
    """
    return audit_service.verify_chain(db, actor.tenant_id)


@router.get("/grants")
def grants(actor: SystemAdminUser, db: DbSession) -> dict:
    """Whether the database itself refuses to rewrite the ledger (addendum B9).

    The hash chain makes tampering detectable; the grant makes it impossible
    through this application's own user. Both are reported, because either one
    alone leaves a gap: a chain with no grant can be rewritten and re-hashed by
    anything holding the DB credentials, and a grant with no chain says nothing
    about what happened before it was applied.
    """
    result = ledger_guard.probe(db)
    if result.get("checked") and not result.get("append_only"):
        # Only useful when the probe failed, and only to a system admin who is
        # about to go and fix it.
        result["grants"] = ledger_guard.current_grants(db)
    return result


@router.get("/entity/{entity_type}/{entity_id}", response_model=list[AuditRead])
def entity_history(
    entity_type: str,
    entity_id: int,
    actor: SystemAdminUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AuditRead]:
    """Everything that ever happened to one thing, oldest first.

    The question an audit log is actually asked - "what happened to request
    #412?" - rather than "what happened on Tuesday". Oldest first, because a
    history is read forwards.

    For a request this **rolls up its parts**. Approvals and rejections are
    recorded against the traveller row, because that is the decision unit
    (addendum B1), and ticket uploads against the ticket. Asking for the
    request's history and being shown everything except who approved it would be
    a correct answer to a question nobody asked.
    """
    scopes: list[tuple[str, list[int]]] = [(entity_type, [entity_id])]

    if entity_type == "travel_request":
        traveller_ids = (
            db.execute(
                select(RequestTraveller.id).where(RequestTraveller.request_id == entity_id)
            )
            .scalars()
            .all()
        )
        ticket_ids = (
            db.execute(
                select(TicketDocument.id).where(TicketDocument.request_id == entity_id)
            )
            .scalars()
            .all()
        )
        if traveller_ids:
            scopes.append(("request_traveller", list(traveller_ids)))
        if ticket_ids:
            scopes.append(("ticket_document", list(ticket_ids)))

    matches = or_(
        *[
            and_(AuditLog.entity_type == kind, AuditLog.entity_id.in_(ids))
            for kind, ids in scopes
        ]
    )

    rows = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == actor.tenant_id, matches)
            .order_by(AuditLog.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [AuditRead.model_validate(row) for row in rows]


@router.get("/export")
def export_csv(
    actor: SystemAdminUser,
    db: DbSession,
    request: Request,
    action: Annotated[AuditAction | None, Query()] = None,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> Response:
    """The filtered ledger as CSV, for an auditor who wants it in their own tool.

    Exporting the log is itself an event worth recording, so this writes a
    VIEW_SENSITIVE row before returning - the same rule that governs reading an
    identity number. The `changes` column can carry personal data, and a file
    leaving the system is exactly when someone later wants to know who took it.
    """
    filters = [AuditLog.tenant_id == actor.tenant_id]
    if action is not None:
        filters.append(AuditLog.action == action)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if since is not None:
        filters.append(AuditLog.created_at >= since.replace(tzinfo=None))
    if until is not None:
        filters.append(AuditLog.created_at <= until.replace(tzinfo=None))

    rows = (
        db.execute(select(AuditLog).where(*filters).order_by(AuditLog.id.desc()).limit(limit))
        .scalars()
        .all()
    )

    audit_service.record(
        db,
        action=AuditAction.VIEW_SENSITIVE,
        entity_type="audit_log",
        summary=f"{actor.full_name} exported {len(rows)} ledger entries to CSV",
        changes={"rows": len(rows), "action": str(action) if action else None},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "when_utc", "actor", "actor_email", "role", "action",
         "entity_type", "entity_id", "summary", "reason", "ip", "changes"]
    )
    for row in rows:
        writer.writerow([
            row.id,
            row.created_at.isoformat(),
            row.actor_name or "",
            row.actor_email or "",
            row.actor_role or "",
            str(row.action),
            row.entity_type,
            row.entity_id if row.entity_id is not None else "",
            row.summary,
            row.reason or "",
            row.ip_address or "",
            json.dumps(row.changes, separators=(",", ":")) if row.changes else "",
        ])

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="activity-log-{stamp}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/summary")
def summary(actor: SystemAdminUser, db: DbSession) -> dict:
    """Counts by action and by entity, so the viewer can offer real filters
    rather than a dropdown of every value the enum happens to define."""
    by_action = dict(
        db.execute(
            select(AuditLog.action, func.count())
            .where(AuditLog.tenant_id == actor.tenant_id)
            .group_by(AuditLog.action)
        ).all()
    )
    by_entity = dict(
        db.execute(
            select(AuditLog.entity_type, func.count())
            .where(AuditLog.tenant_id == actor.tenant_id)
            .group_by(AuditLog.entity_type)
        ).all()
    )
    oldest, newest = db.execute(
        select(func.min(AuditLog.created_at), func.max(AuditLog.created_at)).where(
            AuditLog.tenant_id == actor.tenant_id
        )
    ).one()

    return {
        "total": sum(by_action.values()),
        "by_action": {str(k): v for k, v in by_action.items()},
        "by_entity": {str(k): v for k, v in by_entity.items()},
        "oldest": oldest.isoformat() if oldest else None,
        "newest": newest.isoformat() if newest else None,
    }
