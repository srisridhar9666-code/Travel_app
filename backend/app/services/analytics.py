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

Every figure follows the same filters as the dashboard (dates, campaign,
person, type, destination state and city), read once through `insights.load`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.core.enums import RequestType, TravellerStatus
from app.models.request import RequestTraveller
from app.services import costs, insights
from app.services.insights import Filters, movement_date

#: Spend is money that has actually been committed to a ticket.
SPENT_STATUSES = {TravellerStatus.BOOKED}

#: Approved but not yet ticketed. Real intent, not yet real money.
COMMITTED_STATUSES = {TravellerStatus.APPROVED}

#: How a place with nothing on record is labelled. Shown as missing, never
#: guessed: "Nagpur Station" is an address, not a city a filter can match.
NO_STATE = "State not recorded"
NO_CITY = "City not recorded"


def rows_for(db: Session, tenant_id: str, filters: Filters | None = None) -> list[RequestTraveller]:
    """Every traveller row in the slice, with its request, campaign and person.

    The same filtered read the dashboard and the travel log use, so "Booked
    spend" on the dashboard and "Spent" here agree for the same filters. The
    bundle loads it once and hands it to every aggregator below, which is what
    makes the figures on one screen add up.

    A cancelled request is left out entirely, as it always was here: its money
    was never spent, whatever a traveller row on it still says.
    """
    rows = insights.load(db, tenant_id, filters or Filters())
    return [row for row in rows if not row.request.is_cancelled]


def _rows(
    db: Session, tenant_id: str, filters: Filters | None, rows: list[RequestTraveller] | None
) -> list[RequestTraveller]:
    return rows if rows is not None else rows_for(db, tenant_id, filters)


def overview(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> dict:
    """The headline numbers for the slice."""
    spent = Decimal("0.00")
    committed = Decimal("0.00")
    uncosted = 0
    booked = 0
    pending = 0
    travellers = set()
    trips = set()

    for row in _rows(db, tenant_id, filters, rows):
        if row.status is TravellerStatus.PENDING:
            pending += 1
        if row.status in SPENT_STATUSES:
            booked += 1
            if row.cost_amount is None:
                uncosted += 1
            else:
                spent += costs.to_money(row.cost_amount)
        elif row.status in COMMITTED_STATUSES and row.cost_amount is not None:
            committed += costs.to_money(row.cost_amount)

        if row.status in SPENT_STATUSES | COMMITTED_STATUSES:
            travellers.add(row.user_id)
            trips.add(row.request_id)

    average = (
        (spent / (booked - uncosted)).quantize(costs.PAISA)
        if booked - uncosted > 0
        else Decimal("0.00")
    )

    return {
        "spent": str(spent),
        "committed": str(committed),
        "average_per_traveller": str(average),
        "booked_travellers": booked,
        "pending_travellers": pending,
        #: Booked rows with no cost recorded. The honesty check on every other
        #: number here.
        "uncosted": uncosted,
        "people_travelling": len(travellers),
        "trips": len(trips),
        "currency": costs.DEFAULT_CURRENCY,
    }


def by_campaign(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> list[dict]:
    """Campaign Financials (SOW section 2): what each campaign has cost."""
    buckets: dict[int, dict] = {}

    for row in _rows(db, tenant_id, filters, rows):
        project = row.request.project
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
        bucket["trips"].add(row.request_id)

        if row.status in SPENT_STATUSES:
            bucket["travellers"].add(row.user_id)
            if row.cost_amount is None:
                bucket["uncosted"] += 1
            else:
                bucket["spent"] += costs.to_money(row.cost_amount)
        elif row.status in COMMITTED_STATUSES and row.cost_amount is not None:
            bucket["committed"] += costs.to_money(row.cost_amount)

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


def by_type(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> list[dict]:
    """Where the money goes: flights and trains, cabs, or hotels."""
    buckets: dict[str, dict] = {
        str(t): {"request_type": str(t), "spent": Decimal("0.00"), "travellers": 0}
        for t in RequestType
    }

    for row in _rows(db, tenant_id, filters, rows):
        if row.status not in SPENT_STATUSES:
            continue
        bucket = buckets[str(row.request.request_type)]
        bucket["travellers"] += 1
        if row.cost_amount is not None:
            bucket["spent"] += costs.to_money(row.cost_amount)

    return [{**b, "spent": str(b["spent"])} for b in buckets.values()]


def by_person(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> list[dict]:
    """What each employee's travel cost, biggest first.

    Only people with something booked or approved appear: a row of zeroes for
    everyone who stayed at their desk says nothing.
    """
    buckets: dict[int, dict] = {}
    for row in _rows(db, tenant_id, filters, rows):
        if row.status not in SPENT_STATUSES | COMMITTED_STATUSES:
            continue
        bucket = buckets.setdefault(
            row.user_id,
            {
                "user_id": row.user_id,
                "full_name": row.user.full_name if row.user else "Unknown",
                "employee_code": row.user.employee_code if row.user else None,
                "spent": Decimal("0.00"),
                "committed": Decimal("0.00"),
                "trips": set(),
                "uncosted": 0,
            },
        )
        if row.status in SPENT_STATUSES:
            bucket["trips"].add(row.request_id)
            if row.cost_amount is None:
                bucket["uncosted"] += 1
            else:
                bucket["spent"] += costs.to_money(row.cost_amount)
        elif row.cost_amount is not None:
            bucket["committed"] += costs.to_money(row.cost_amount)

    out = [
        {**b, "spent": str(b["spent"]), "committed": str(b["committed"]), "trips": len(b["trips"])}
        for b in buckets.values()
    ]
    out.sort(key=lambda b: (-Decimal(b["spent"]), -Decimal(b["committed"]), b["full_name"]))
    return out


def _by_place(rows: list[RequestTraveller], key) -> list[dict]:
    """Booked spend grouped by where each trip was headed.

    Each booked row lands in exactly one bucket, so the buckets add up to the
    overview's spend.
    """
    buckets: dict[tuple, dict] = {}
    for row in rows:
        if row.status not in SPENT_STATUSES:
            continue
        group, label, state, city = key(row)
        bucket = buckets.setdefault(
            group,
            {"label": label, "state": state, "city": city, "spent": Decimal("0.00"),
             "people": set(), "trips": set(), "uncosted": 0},
        )
        bucket["people"].add(row.user_id)
        bucket["trips"].add(row.request_id)
        if row.cost_amount is None:
            bucket["uncosted"] += 1
        else:
            bucket["spent"] += costs.to_money(row.cost_amount)

    out = [
        {
            "label": b["label"],
            "state": b["state"],
            "city": b["city"],
            "spent": str(b["spent"]),
            "travellers": len(b["people"]),
            "trips": len(b["trips"]),
            "uncosted": b["uncosted"],
        }
        for b in buckets.values()
    ]
    # Among equal amounts, a real place reads before a "not recorded" one.
    out.sort(key=lambda b: (-Decimal(b["spent"]), b["label"] in (NO_STATE, NO_CITY), b["label"]))
    return out


def by_state(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> list[dict]:
    """Booked spend by destination state (for a hotel, the hotel's state)."""

    def key(row: RequestTraveller):
        state, _ = insights.destination(row.request)
        return (state,), state or NO_STATE, state, None

    return _by_place(_rows(db, tenant_id, filters, rows), key)


def by_city(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None
) -> list[dict]:
    """Booked spend by destination city: a flight's destination, a cab's drop
    city, a hotel's city. Old cabs with only a street address share one
    "City not recorded" bucket."""

    def key(row: RequestTraveller):
        state, city = insights.destination(row.request)
        if not city:
            return (None, None), NO_CITY, None, None
        # Keyed on the state too: two states can have a place of one name.
        return (state, city.lower()), city, state, city

    return _by_place(_rows(db, tenant_id, filters, rows), key)


def trend(
    rows: list[RequestTraveller],
    *,
    since: date | None,
    until: date | None,
    today: date | None = None,
) -> dict:
    """Booked spend over time, bucketed by the *trip* date.

    A campaign's budget is spent in the month the team is in the field, not the
    month the ticket was bought. The chart covers the chosen dates; an open side
    runs to the earliest or latest booking (and at least to today), so "all
    time" includes trips already ticketed for next month. Days, weeks or months
    follow the span, as on the dashboard.
    """
    today = today or clock.local_today()
    booked = [row for row in rows if row.status in SPENT_STATUSES]
    lo, hi = insights.axis(
        [d for d in (movement_date(row.request) for row in booked) if d is not None],
        since=since,
        until=until,
        today=today,
    )
    grain = insights.grain_for(lo, hi)
    points = {
        key: {"period": key, "spent": Decimal("0.00"), "travellers": 0}
        for key in insights.periods_between(lo, hi, grain)
    }
    for row in booked:
        day = movement_date(row.request)
        point = points.get(insights.bucket_key(day, grain)) if day else None
        if point is None:
            continue   # outside a chosen window, not piled onto its last bucket
        point["travellers"] += 1
        if row.cost_amount is not None:
            point["spent"] += costs.to_money(row.cost_amount)

    return {
        "since": lo,
        "until": hi,
        "grain": grain,
        "points": [{**p, "spent": str(p["spent"])} for p in points.values()],
    }


def deployment(
    db: Session,
    tenant_id: str,
    filters: Filters | None = None,
    *,
    rows=None,
    today: date | None = None,
) -> list[dict]:
    """Deployed staff by location (SOW section 6).

    Counted by where the trip is headed, not where staff are based - the
    question the report answers is "who is in Maharashtra this month", and a
    Hyderabad-based auditor heading to Pune is in Maharashtra. A trip with no
    state on record falls back to its campaign's state, then to the campaign's
    old free-text location.
    """
    buckets: dict[str, dict] = {}
    today = today or clock.local_today()

    for row in _rows(db, tenant_id, filters, rows):
        if row.status not in SPENT_STATUSES | COMMITTED_STATUSES:
            continue
        trip_on = movement_date(row.request)
        if trip_on is None or trip_on < today:
            continue   # deployment is a forward-looking question

        project = row.request.project
        where = (
            insights.destination(row.request)[0]
            or project.state
            or project.location
            or "Unspecified"
        )
        bucket = buckets.setdefault(where, {"location": where, "people": set(), "trips": set()})
        bucket["people"].add(row.user_id)
        bucket["trips"].add(row.request_id)

    out = [
        {"location": b["location"], "people": len(b["people"]), "trips": len(b["trips"])}
        for b in buckets.values()
    ]
    out.sort(key=lambda b: (-b["people"], b["location"]))
    return out


def uncosted_bookings(
    db: Session, tenant_id: str, filters: Filters | None = None, *, rows=None, limit: int = 50
) -> list[dict]:
    """Booked travellers with no cost recorded - the list that makes the rest of
    these numbers trustworthy, and the one an admin works through."""
    out = []
    for row in _rows(db, tenant_id, filters, rows):
        if row.status in SPENT_STATUSES and row.cost_amount is None:
            trip_on = movement_date(row.request)
            out.append(
                {
                    "traveller_id": row.id,
                    "request_id": row.request_id,
                    "traveller_name": row.user.full_name if row.user else "Unknown",
                    "project_code": row.request.project.code,
                    "request_type": str(row.request.request_type),
                    "booking_reference": row.booking_reference,
                    "trip_date": trip_on.isoformat() if trip_on else None,
                }
            )
    out.sort(key=lambda r: r["trip_date"] or "")
    return out[:limit]
