"""
Campaign IDs, and the facts about a campaign the admin screen needs.

A campaign's ID is shown wherever there is no room for its full name - request
lists, emails, CSV exports, invoices - so it is never typed: every new campaign
gets the next one in sequence for the year it is created, CMP-2026-0001,
CMP-2026-0002 ..., and keeps it whatever happens to its name or dates.
Campaigns made before this keep the codes they were given ("MRA-26").
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.project import Project
from app.models.request import TravelRequest
from app.services.seed import OTHER_PROJECT_CODE

CODE_PREFIX = "CMP"


def next_code(
    db: Session,
    tenant_id: str,
    *,
    year: int | None = None,
    also_taken: Iterable[str] = (),
) -> str:
    """The next unused CMP-<year>-<number> for this organisation.

    `also_taken` names codes a caller just lost an insert race for: its
    transaction's snapshot cannot see the winner's row, so the SELECT alone
    would hand the same number back.
    """
    year = year or clock.local_today().year
    prefix = f"{CODE_PREFIX}-{year}-"
    existing = db.execute(
        select(Project.code).where(
            Project.tenant_id == tenant_id, Project.code.like(f"{prefix}%")
        )
    ).scalars()
    numbers = [
        int(code[len(prefix):])
        for code in (*existing, *also_taken)
        if code.upper().startswith(prefix) and code[len(prefix):].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def is_fallback(project: Project) -> bool:
    """The built-in "Other / not yet listed" campaign the request form needs."""
    return project.code == OTHER_PROJECT_CODE


def request_counts(db: Session, tenant_id: str, ids: Iterable[int]) -> dict[int, int]:
    """Requests of any status - drafts and cancelled ones too - per campaign.

    One grouped query for the whole page, not one per row.
    """
    ids = list(ids)
    if not ids:
        return {}
    rows = db.execute(
        select(TravelRequest.project_id, func.count())
        .where(TravelRequest.tenant_id == tenant_id, TravelRequest.project_id.in_(ids))
        .group_by(TravelRequest.project_id)
    ).all()
    return {project_id: count for project_id, count in rows}
