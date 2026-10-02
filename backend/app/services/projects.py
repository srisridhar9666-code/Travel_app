"""
Campaign codes, and the facts about a campaign the admin screen needs.

Admins found the mandatory code confusing: nothing said what it was for or what
to type. It is a short tag shown where there is no room for the full name -
request lists, emails, CSV exports - so it is now optional and, when left
blank, made here from the name's initials and the start year, the same shape
as the codes already in use ("MRA-26").
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.project import Project
from app.models.request import TravelRequest
from app.services.seed import OTHER_PROJECT_CODE

#: Words that carry no meaning in an initialism: "Survey of the Godavari
#: Districts" is SGD, not SOTGD.
STOPWORDS = frozenset(
    {"A", "AN", "AND", "THE", "OF", "FOR", "IN", "ON", "AT", "TO", "BY", "WITH", "FROM"}
)

#: When a name has no Latin letters at all (one typed in Telugu or Hindi), there
#: are no initials to take.
NO_INITIALS = "CMP"


def code_base(name: str, year: int) -> str:
    """The code a campaign called `name` starting in `year` would get, before
    making it unique. 'Monsoon Retail Audit', 2026 -> 'MRA-26'."""
    # Accents folded the same way place names are, so "Mahé" gives M, not
    # nothing.
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = [w for w in re.findall(r"[A-Z0-9]+", folded.upper()) if not w.isdigit()]
    meaningful = [w for w in words if w not in STOPWORDS] or words

    if len(meaningful) >= 2:
        initials = "".join(w[0] for w in meaningful[:4])
    elif meaningful:
        initials = meaningful[0][:4]
    else:
        initials = NO_INITIALS
    return f"{initials}-{year % 100:02d}"


def unique_code(
    db: Session,
    tenant_id: str,
    base: str,
    *,
    exclude_id: int | None = None,
    also_taken: set[str] | frozenset[str] = frozenset(),
) -> str:
    """`base`, or the first of BASE-2, BASE-3 ... not already taken.

    Compared case-insensitively, as the unique index is. 'OTHER' always counts
    as taken: the built-in campaign is found by it. `also_taken` names codes a
    caller just lost an insert race for: its transaction's snapshot cannot see
    the winner's row, so the SELECT alone would hand the same code back.
    """
    query = select(Project.code).where(
        Project.tenant_id == tenant_id, Project.code.like(f"{base}%")
    )
    if exclude_id is not None:
        query = query.where(Project.id != exclude_id)
    taken = {code.upper() for code in db.execute(query).scalars()}
    taken.add(OTHER_PROJECT_CODE)
    taken.update(code.upper() for code in also_taken)

    if base.upper() not in taken:
        return base
    n = 2
    while f"{base}-{n}".upper() in taken:
        n += 1
    return f"{base}-{n}"


def generate_code(
    db: Session,
    tenant_id: str,
    name: str,
    start_date: date | None,
    *,
    exclude_id: int | None = None,
    also_taken: set[str] | frozenset[str] = frozenset(),
) -> str:
    year = start_date.year if start_date else clock.local_today().year
    return unique_code(
        db, tenant_id, code_base(name, year), exclude_id=exclude_id, also_taken=also_taken
    )


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
