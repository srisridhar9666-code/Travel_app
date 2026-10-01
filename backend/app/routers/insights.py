"""
Travel logs across every employee, and the filterable admin dashboard.

Admin-only, like the cost reports: both carry spend, and a log of where every
colleague has been is not something ground staff need. Each person can still
read their own timeline through `/users/{id}/travel-history`.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, DbSession
from app.core.enums import RequestType, TravellerStatus
from app.services import insights

router = APIRouter(tags=["insights"])


def _filters(
    since: date | None,
    until: date | None,
    user_id: int | None,
    project_id: int | None,
    request_type: RequestType | None,
    statuses: list[TravellerStatus] | None,
    state: str | None,
    search: str | None = None,
) -> insights.Filters:
    return insights.Filters(
        since=since,
        until=until,
        user_id=user_id,
        project_id=project_id,
        request_type=request_type,
        statuses=tuple(statuses) if statuses else None,
        state=(state or "").strip() or None,
        search=(search or "").strip() or None,
    )


@router.get("/travel-logs")
def travel_logs(
    actor: AdminUser,
    db: DbSession,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
    request_type: Annotated[RequestType | None, Query()] = None,
    status: Annotated[list[TravellerStatus] | None, Query()] = None,
    state: Annotated[str | None, Query(max_length=80)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=insights.MAX_LOG_ROWS)] = 500,
) -> dict:
    """Every movement in a window, for one employee or all of them.

    Without a status filter, only movements that happened or are going to
    (pending, approved, booked) are listed - a rejected trip is not somewhere
    anyone went. Pass `status` to see rejected or cancelled ones too.
    """
    filters = _filters(
        since, until, user_id, project_id, request_type,
        status or list(insights.TRAVELLED), state, search,
    )
    return insights.travel_log(db, actor.tenant_id, filters, limit=limit)


@router.get("/analytics/insights")
def dashboard(
    actor: AdminUser,
    db: DbSession,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
    request_type: Annotated[RequestType | None, Query()] = None,
    state: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """The dashboard for a window and slice. Defaults to the last 30 days."""
    filters = _filters(since, until, user_id, project_id, request_type, None, state)
    return insights.dashboard(db, actor.tenant_id, filters)


@router.get("/analytics/filter-options")
def filter_options(actor: AdminUser, db: DbSession) -> dict:
    """Campaigns, people and states, for the dashboard and log filters."""
    return insights.filter_options(db, actor.tenant_id)
