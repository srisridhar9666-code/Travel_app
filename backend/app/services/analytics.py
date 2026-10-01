"""
Campaign financials and cost analytics (SOW sections 2 and 6).

Everything here aggregates `request_travellers`, because that row is the unit
that carries both a status and a cost. Counting requests instead would report a
three-person trip as one journey and one fare, which is neither what finance
wants nor what operations sees.

Two decisions worth stating, because both change what the numbers mean:

* **Only `BOOKED` travellers count as spend.** A cost on an approved-but-
  unticketed row is a forecast, not money. They are reported separately as
  *committed*, so nobody has to reconcile a dashboard against an invoice and
  find the difference is a trip that never happened.
* **A missing cost is reported as missing, not as zero.** A campaign with three
  booked trips and one blank fare must not show up as cheaper than it is.
  `uncosted` is on every response for exactly that reason.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import RequestType, TravellerStatus
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import costs

#: Spend is money that has actually been committed to a ticket.
SPENT_STATUSES = {TravellerStatus.BOOKED}

#: Approved but not yet ticketed. Real intent, not yet real money.
COMMITTED_STATUSES = {TravellerStatus.APPROVED}


def _rows(db: Session, tenant_id: str, *, since: date | None = None):
    """Every live traveller row with its request and campaign attached.

    One query rather than per-campaign aggregation in SQL: at roughly a hundred
    staff the whole table is small, and doing the grouping in Python keeps the
    status rules in one place instead of duplicating `SPENT_STATUSES` into
    several `CASE` expressions that can drift apart.
    """
    stmt = (
        select(RequestTraveller, TravelRequest, Project, User)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .join(Project, Project.id == TravelRequest.project_id)
        .join(User, User.id == RequestTraveller.user_id)
        .where(
            TravelRequest.tenant_id == tenant_id,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
        )
    )
    if since is not None:
        stmt = stmt.where(TravelRequest.submitted_at >= since)
    return db.execute(stmt).all()


def _trip_date(request: TravelRequest) -> date | None:
    if request.request_type is RequestType.HOTEL:
        return request.check_in
    return request.start_at.date() if request.start_at else None


def overview(db: Session, tenant_id: str, *, days: int = 90) -> dict:
    """The headline numbers, over a window.

    Ninety days by default, matching the retention view in addendum B11 - the
    default *view* is 90 days even though all history is kept.
    """
    since = date.today() - timedelta(days=days)
    rows = _rows(db, tenant_id)

    spent = Decimal("0.00")
    committed = Decimal("0.00")
    uncosted = 0
    booked = 0
    pending = 0
    travellers_in_window = set()
    trips_in_window = set()

    for traveller, request, _project, _user in rows:
        trip_on = _trip_date(request)
        recent = trip_on is not None and trip_on >= since

        if traveller.status is TravellerStatus.PENDING:
            pending += 1
        if traveller.status in SPENT_STATUSES:
            booked += 1
            if traveller.cost_amount is None:
                uncosted += 1
            else:
                spent += costs.to_money(traveller.cost_amount)
        elif traveller.status in COMMITTED_STATUSES and traveller.cost_amount is not None:
            committed += costs.to_money(traveller.cost_amount)

        if recent and traveller.status in SPENT_STATUSES | COMMITTED_STATUSES:
            travellers_in_window.add(traveller.user_id)
            trips_in_window.add(request.id)

    average = (
        (spent / (booked - uncosted)).quantize(costs.PAISA)
        if booked - uncosted > 0
        else Decimal("0.00")
    )

    return {
        "window_days": days,
        "spent": str(spent),
        "committed": str(committed),
        "average_per_traveller": str(average),
        "booked_travellers": booked,
        "pending_travellers": pending,
        #: Booked rows with no cost recorded. The honesty check on every other
        #: number here.
        "uncosted": uncosted,
        "people_travelling": len(travellers_in_window),
        "trips": len(trips_in_window),
        "currency": costs.DEFAULT_CURRENCY,
    }


def by_campaign(db: Session, tenant_id: str) -> list[dict]:
    """Campaign Financials (SOW section 2): what each campaign has cost."""
    buckets: dict[int, dict] = {}

    for traveller, request, project, _user in _rows(db, tenant_id):
        bucket = buckets.setdefault(
            project.id,
            {
                "project_id": project.id,
                "code": project.code,
                "name": project.name,
                "status": str(project.status),
                "spent": Decimal("0.00"),
                "committed": Decimal("0.00"),
                "trips": set(),
                "travellers": set(),
                "uncosted": 0,
            },
        )
        bucket["trips"].add(request.id)

        if traveller.status in SPENT_STATUSES:
            bucket["travellers"].add(traveller.user_id)
            if traveller.cost_amount is None:
                bucket["uncosted"] += 1
            else:
                bucket["spent"] += costs.to_money(traveller.cost_amount)
        elif traveller.status in COMMITTED_STATUSES and traveller.cost_amount is not None:
            bucket["committed"] += costs.to_money(traveller.cost_amount)

    out = [
        {
            **bucket,
            "spent": str(bucket["spent"]),
            "committed": str(bucket["committed"]),
            "trips": len(bucket["trips"]),
            "travellers": len(bucket["travellers"]),
        }
        for bucket in buckets.values()
    ]
    out.sort(key=lambda b: Decimal(b["spent"]), reverse=True)
    return out


def by_type(db: Session, tenant_id: str) -> list[dict]:
    """Where the money goes: flights and trains, cabs, or hotels."""
    buckets: dict[str, dict] = {
        str(t): {"request_type": str(t), "spent": Decimal("0.00"), "travellers": 0}
        for t in RequestType
    }

    for traveller, request, _project, _user in _rows(db, tenant_id):
        if traveller.status not in SPENT_STATUSES:
            continue
        bucket = buckets[str(request.request_type)]
        bucket["travellers"] += 1
        if traveller.cost_amount is not None:
            bucket["spent"] += costs.to_money(traveller.cost_amount)

    return [{**b, "spent": str(b["spent"])} for b in buckets.values()]


def by_month(db: Session, tenant_id: str, *, months: int = 6) -> list[dict]:
    """Spend per calendar month of travel, oldest first.

    Bucketed by the *trip* date rather than the booking date, because a
    campaign's budget is spent in the month the team is in the field.

    The window is **centred on the current month** rather than looking only
    backwards. Field teams book weeks ahead, so most booked spend at any moment
    sits in the future; a backwards-only window showed an empty chart for a team
    that was planning perfectly well. Half the window is history, half is what is
    already committed to a ticket.
    """
    today = date.today()
    ahead = months // 2
    behind = months - ahead - 1

    window: list[str] = []
    year, month = today.year, today.month - behind
    while month < 1:
        year, month = year - 1, month + 12
    for _ in range(months):
        window.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    counts: dict[str, int] = defaultdict(int)

    for traveller, request, _project, _user in _rows(db, tenant_id):
        if traveller.status not in SPENT_STATUSES:
            continue
        trip_on = _trip_date(request)
        if trip_on is None:
            continue
        key = f"{trip_on.year:04d}-{trip_on.month:02d}"
        if key not in window:
            continue
        counts[key] += 1
        if traveller.cost_amount is not None:
            totals[key] += costs.to_money(traveller.cost_amount)

    return [
        {"month": key, "spent": str(totals[key]), "travellers": counts[key]}
        for key in window
    ]


def deployment(db: Session, tenant_id: str) -> list[dict]:
    """Deployed staff by location (SOW section 6).

    Counted by where the campaign is running, not where staff are based - the
    question the report answers is "who is in Maharashtra this month", and a
    Hyderabad-based auditor working a Pune campaign is in Maharashtra.
    """
    buckets: dict[str, dict] = {}
    today = date.today()

    for traveller, request, project, _user in _rows(db, tenant_id):
        if traveller.status not in SPENT_STATUSES | COMMITTED_STATUSES:
            continue
        trip_on = _trip_date(request)
        if trip_on is None or trip_on < today:
            continue   # deployment is a forward-looking question

        where = project.location or "Unspecified"
        bucket = buckets.setdefault(
            where, {"location": where, "people": set(), "trips": set()}
        )
        bucket["people"].add(traveller.user_id)
        bucket["trips"].add(request.id)

    out = [
        {"location": b["location"], "people": len(b["people"]), "trips": len(b["trips"])}
        for b in buckets.values()
    ]
    out.sort(key=lambda b: (-b["people"], b["location"]))
    return out


def uncosted_bookings(db: Session, tenant_id: str, *, limit: int = 50) -> list[dict]:
    """Booked travellers with no cost recorded - the list that makes the rest of
    these numbers trustworthy, and the one an admin works through."""
    out = []
    for traveller, request, project, user in _rows(db, tenant_id):
        if traveller.status in SPENT_STATUSES and traveller.cost_amount is None:
            out.append(
                {
                    "traveller_id": traveller.id,
                    "request_id": request.id,
                    "traveller_name": user.full_name,
                    "project_code": project.code,
                    "request_type": str(request.request_type),
                    "booking_reference": traveller.booking_reference,
                    "trip_date": _trip_date(request).isoformat() if _trip_date(request) else None,
                }
            )
    out.sort(key=lambda r: r["trip_date"] or "")
    return out[:limit]
