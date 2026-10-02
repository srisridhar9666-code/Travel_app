"""
Travel logs and the filterable dashboard.

Both answer "what happened, for whom, where and when" over a window an admin
chooses, so both are built from one filtered read of `request_travellers` - the
row that carries a person, a status and a cost. The per-person timeline in
`history.py` answers the same question for one employee from their own side;
this module answers it across everyone, for the admin.

Rows are filtered by the date the movement *happens* (departure, or hotel
check-in), not the date it was requested: "where was Ravi last month" is about
the trip, and a trip booked in March for May belongs to May.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import clock
from app.core.enums import RequestStatus, RequestType, TravellerStatus, UserStatus
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import costs
from app.services import requests as request_service

#: Money that has actually been committed to a ticket. Same rule as analytics.
SPENT = {TravellerStatus.BOOKED}
COMMITTED = {TravellerStatus.APPROVED}

#: Movements that happened or are going to. A rejected or cancelled traveller
#: did not travel: the log lists them, but no total counts them as a trip.
TRAVELLED = (TravellerStatus.PENDING, TravellerStatus.APPROVED, TravellerStatus.BOOKED)

#: Hard ceiling on one log read. Roughly a hundred staff produce a few thousand
#: movements a year, so this is a year of everyone - enough for any export.
MAX_LOG_ROWS = 5000


@dataclass
class Filters:
    since: date | None = None
    until: date | None = None
    user_id: int | None = None
    project_id: int | None = None
    request_type: RequestType | None = None
    statuses: tuple[TravellerStatus, ...] | None = None
    #: The destination state and city: where the trip goes, not where it starts.
    #: A Hyderabad team choosing Telangana wants trips *to* Telangana, and the
    #: "top destination states" row they click has to match its own count.
    state: str | None = None
    city: str | None = None
    search: str | None = None

    def __post_init__(self) -> None:
        # Reversed dates are a slip, not a request for nothing.
        if self.since and self.until and self.until < self.since:
            self.since, self.until = self.until, self.since


def movement_date(request: TravelRequest) -> date | None:
    """The day a movement starts, whichever field carries it for the type."""
    if request.request_type is RequestType.HOTEL:
        return request.check_in
    return request.start_at.date() if request.start_at else None


def _states(request: TravelRequest) -> set[str]:
    return {s for s in (request.origin_state, request.destination_state, request.hotel_state) if s}


def destination(request: TravelRequest) -> tuple[str | None, str | None]:
    """Where the movement goes to: (state, city).

    A cab's city is its drop city, never the street address: "places visited"
    counts Hyderabad once, not every address in it, and an address cannot be
    matched by the city filter anyway. Cabs raised before drop cities were
    captured have no city at all, and are reported as such.
    """
    if request.request_type is RequestType.HOTEL:
        return request.hotel_state, request.hotel_city
    if request.request_type is RequestType.LOCAL_CAB:
        return request.destination_state, request.drop_city
    return request.destination_state, request.destination


def _where(request: TravelRequest) -> str:
    if request.request_type is RequestType.HOTEL:
        return request.hotel_city or "Hotel"
    return f"{request.origin_label or '?'} → {request.destination_label or '?'}"


def _nights(request: TravelRequest) -> int | None:
    if request.request_type is not RequestType.HOTEL or not (request.check_in and request.check_out):
        return None
    return max((request.check_out - request.check_in).days, 0)


def load(db: Session, tenant_id: str, filters: Filters) -> list[RequestTraveller]:
    """Every traveller row matching the filters, newest movement first.

    The date window is applied in SQL, branching on request type the same way
    `history.build` does - a hotel is dated by check-in, everything else by
    departure, and a flat OR across both columns silently matches everything.
    """
    stmt = (
        select(RequestTraveller)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .options(
            selectinload(RequestTraveller.user),
            selectinload(RequestTraveller.request).selectinload(TravelRequest.project),
            selectinload(RequestTraveller.request)
            .selectinload(TravelRequest.travellers)
            .selectinload(RequestTraveller.user),
        )
        # Cancelled requests are not excluded here: cancelling sets every
        # traveller CANCELLED, which every travelled-only total leaves out
        # already, and the log's "Cancelled" choice has to be able to find them.
        .where(
            TravelRequest.tenant_id == tenant_id,
            TravelRequest.is_draft.is_(False),
        )
    )

    if filters.user_id is not None:
        stmt = stmt.where(RequestTraveller.user_id == filters.user_id)
    if filters.project_id is not None:
        stmt = stmt.where(TravelRequest.project_id == filters.project_id)
    if filters.request_type is not None:
        stmt = stmt.where(TravelRequest.request_type == filters.request_type)
    if filters.statuses:
        stmt = stmt.where(RequestTraveller.status.in_(filters.statuses))
    # Destination only, by the same rule as `destination()`. MySQL's collation
    # makes both comparisons case-insensitive.
    is_hotel = TravelRequest.request_type == RequestType.HOTEL
    if filters.state:
        stmt = stmt.where(
            or_(
                and_(is_hotel, TravelRequest.hotel_state == filters.state),
                and_(~is_hotel, TravelRequest.destination_state == filters.state),
            )
        )
    if filters.city:
        stmt = stmt.where(
            or_(
                and_(is_hotel, TravelRequest.hotel_city == filters.city),
                and_(
                    TravelRequest.request_type == RequestType.LOCAL_CAB,
                    TravelRequest.drop_city == filters.city,
                ),
                and_(
                    TravelRequest.request_type == RequestType.LONG_DISTANCE,
                    TravelRequest.destination == filters.city,
                ),
            )
        )

    if filters.since or filters.until:
        hotel = [TravelRequest.request_type == RequestType.HOTEL]
        other = [TravelRequest.request_type != RequestType.HOTEL]
        if filters.since:
            hotel.append(TravelRequest.check_in >= filters.since)
            other.append(
                TravelRequest.start_at >= datetime.combine(filters.since, datetime.min.time())
            )
        if filters.until:
            hotel.append(TravelRequest.check_in <= filters.until)
            other.append(
                TravelRequest.start_at
                < datetime.combine(filters.until + timedelta(days=1), datetime.min.time())
            )
        stmt = stmt.where(or_(and_(*hotel), and_(*other)))

    rows = list(db.execute(stmt).scalars().unique().all())

    if filters.search:
        needle = filters.search.strip().lower()
        rows = [row for row in rows if needle in _haystack(row)]

    rows.sort(
        key=lambda r: (movement_date(r.request) is not None, movement_date(r.request) or date.min, r.id),
        reverse=True,
    )
    return rows


def _haystack(row: RequestTraveller) -> str:
    """Everything a free-text search over the log should match."""
    request = row.request
    parts = [
        row.user.full_name if row.user else "",
        row.user.email if row.user else "",
        row.user.employee_code if row.user and row.user.employee_code else "",
        request.origin or "",
        request.destination or "",
        request.pickup_city or "",
        request.drop_city or "",
        request.hotel_city or "",
        *(_states(request)),
        request.project.code if request.project else "",
        request.project.name if request.project else "",
        request.other_project_name or "",
        request.travel_reason or "",
        row.booking_reference or "",
    ]
    return " ".join(parts).lower()


def _log_entry(row: RequestTraveller, *, include_costs: bool) -> dict:
    request = row.request
    started = movement_date(request)
    companions = [
        other.user.full_name
        for other in request.travellers
        if other.user_id != row.user_id and other.status in TRAVELLED and other.user
    ]
    return {
        "traveller_id": row.id,
        "request_id": request.id,
        "user_id": row.user_id,
        "full_name": row.user.full_name if row.user else "Unknown",
        "employee_code": row.user.employee_code if row.user else None,
        "designation": str(row.user.designation) if row.user and row.user.designation else None,
        "request_type": str(request.request_type),
        "mode": str(request.mode) if request.mode else None,
        "status": str(row.status),
        "where": _where(request),
        "origin": request.origin,
        "origin_state": request.origin_state,
        "destination": request.destination,
        "destination_state": request.destination_state,
        "pickup_city": request.pickup_city,
        "drop_city": request.drop_city,
        "hotel_city": request.hotel_city,
        "hotel_state": request.hotel_state,
        "started_on": started.isoformat() if started else None,
        "start_at": request.start_at.isoformat() if request.start_at else None,
        "end_at": request.end_at.isoformat() if request.end_at else None,
        "check_in": request.check_in.isoformat() if request.check_in else None,
        "check_out": request.check_out.isoformat() if request.check_out else None,
        "nights": _nights(request),
        "project_id": request.project_id,
        "project_code": request.project.code if request.project else None,
        "project_name": request.other_project_name
        or (request.project.name if request.project else None),
        "travel_reason": request.travel_reason,
        "priority": str(request.priority),
        "booking_reference": row.booking_reference,
        "companions": companions,
        "cost_amount": (
            str(row.cost_amount) if include_costs and row.cost_amount is not None else None
        ),
    }


def travel_log(
    db: Session,
    tenant_id: str,
    filters: Filters,
    *,
    page: int = 1,
    page_size: int = 50,
    include_costs: bool = True,
) -> dict:
    """One page of the movements matching the filters, with totals over all of them.

    The summary always counts every match, not just the page on screen - "8
    people travelled last month" must not change when someone pages on. It
    counts only movements that happened or are going to, even when rejected
    and cancelled rows are listed: a rejected hotel is not a night away.
    """
    rows = load(db, tenant_id, filters)
    total = len(rows)
    page_size = max(1, min(page_size, MAX_LOG_ROWS))
    pages = max(1, -(-total // page_size))
    # A page past the end - left in the address bar after narrowing the
    # filters - shows the last page rather than an empty table.
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    shown = rows[start : start + page_size]

    spent = Decimal("0.00")
    for row in rows:
        if row.status in SPENT and row.cost_amount is not None:
            spent += costs.to_money(row.cost_amount)
    travelled = [row for row in rows if row.status in TRAVELLED]

    return {
        "since": filters.since.isoformat() if filters.since else None,
        "until": filters.until.isoformat() if filters.until else None,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "truncated": start + len(shown) < total,
        "summary": {
            "movements": len(travelled),
            "people": len({row.user_id for row in travelled}),
            "requests": len({row.request_id for row in travelled}),
            "nights": sum(_nights(row.request) or 0 for row in travelled),
            "places": len({p for row in travelled for p in [destination(row.request)[1]] if p}),
            "spent": str(spent) if include_costs else None,
        },
        "entries": [_log_entry(row, include_costs=include_costs) for row in shown],
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------




def bucket_key(day: date, grain: str) -> str:
    if grain == "day":
        return day.isoformat()
    if grain == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    return f"{day.year:04d}-{day.month:02d}"


def grain_for(since: date, until: date) -> str:
    """Days for a month, weeks for a quarter or two, months beyond that."""
    span = (until - since).days
    if span <= 45:
        return "day"
    if span <= 190:
        return "week"
    return "month"


def periods_between(since: date, until: date, grain: str) -> list[str]:
    """Every bucket in the window, so a quiet week shows as zero, not a gap."""
    keys: list[str] = []
    day = since
    while day <= until:
        key = bucket_key(day, grain)
        if not keys or keys[-1] != key:
            keys.append(key)
        day += timedelta(days=1)
    return keys


def axis(
    days: list[date], *, since: date | None, until: date | None, today: date
) -> tuple[date, date]:
    """The span a trend chart covers.

    A chosen side is kept as chosen. An open side is taken from the data: back
    to the earliest movement, and forward to the latest one or today, whichever
    is later - "all time" has to include trips already booked for next month,
    and must not draw empty columns back to the year 2000. With no data at all
    it is the last 30 days.
    """
    lo = since or (min(days) if days else today - timedelta(days=29))
    hi = until or max([today, *days])
    if lo > hi:
        # Only one side was chosen and the data lies wholly beyond it.
        if since is None:
            lo = hi - timedelta(days=29)
        else:
            hi = lo
    return lo, hi


def _top(counter: Counter, n: int = 8) -> list[dict]:
    return [{"label": label, "count": count} for label, count in counter.most_common(n)]


def dashboard(db: Session, tenant_id: str, filters: Filters) -> dict:
    """The admin dashboard for whatever window and slice the filters describe.

    Status is not pre-filtered: the breakdown by status is one of the things
    the dashboard is for. Everything else counts what the filters let through.
    No dates means all time, and a missing side is open, as in the log.
    """
    today = clock.local_today()
    rows = load(
        db,
        tenant_id,
        Filters(
            since=filters.since,
            until=filters.until,
            user_id=filters.user_id,
            project_id=filters.project_id,
            request_type=filters.request_type,
            statuses=filters.statuses,
            state=filters.state,
            city=filters.city,
        ),
    )

    since, until = axis(
        [d for d in (movement_date(row.request) for row in rows) if d is not None],
        since=filters.since,
        until=filters.until,
        today=today,
    )
    grain = grain_for(since, until)
    periods = periods_between(since, until, grain)
    trend = {key: {"period": key, "movements": 0, "people": set(), "spent": Decimal("0.00")} for key in periods}

    statuses: Counter = Counter()
    types: dict[str, dict] = {
        str(t): {"request_type": str(t), "count": 0, "spent": Decimal("0.00")} for t in RequestType
    }
    modes: Counter = Counter()
    dest_states: Counter = Counter()
    dest_places: Counter = Counter()
    campaigns: dict[int, dict] = {}
    travellers: dict[int, dict] = {}

    spent = committed = Decimal("0.00")
    uncosted = nights = unstated = 0
    live_people: set[int] = set()
    live_requests: set[int] = set()

    # "Awaiting a decision" over this slice, by the same rule as the Approvals
    # tab the tile opens: the request's own status is Submitted, and someone on
    # it inside the slice is still pending. A pending row whose trip date has
    # passed is Expired there and nobody can act on it, so it is not counted.
    request_status: dict[int, RequestStatus] = {}
    awaiting: dict[int, TravelRequest] = {}
    awaiting_people: set[tuple[int, int]] = set()
    partly: set[int] = set()

    for row in rows:
        request = row.request
        status = row.status
        statuses[str(status)] += 1
        travelled = status in TRAVELLED
        cost = costs.to_money(row.cost_amount) if row.cost_amount is not None else None
        is_spent = status in SPENT and cost is not None

        if status is TravellerStatus.PENDING:
            if request.id not in request_status:
                request_status[request.id] = request_service.status_of(request)
            overall = request_status[request.id]
            if overall is RequestStatus.SUBMITTED:
                awaiting[request.id] = request
                awaiting_people.add((request.id, row.user_id))
            elif overall is RequestStatus.PARTIALLY_APPROVED:
                partly.add(request.id)

        if status in SPENT:
            if cost is None:
                uncosted += 1
            else:
                spent += cost
        elif status in COMMITTED and cost is not None:
            committed += cost

        if not travelled:
            continue

        live_people.add(row.user_id)
        live_requests.add(request.id)
        nights += _nights(request) or 0

        bucket = types[str(request.request_type)]
        bucket["count"] += 1
        if is_spent:
            bucket["spent"] += cost

        if request.mode:
            modes[str(request.mode)] += 1

        # A trip with no state on record (raised before states were captured)
        # is counted as such, so the panel can say so instead of "no travel".
        state, place = destination(request)
        if state:
            dest_states[state] += 1
        else:
            unstated += 1
        if place:
            dest_places[place] += 1

        project = request.project
        if project is not None:
            entry = campaigns.setdefault(
                project.id,
                {"project_id": project.id, "code": project.code, "name": project.name,
                 "count": 0, "people": set(), "spent": Decimal("0.00")},
            )
            entry["count"] += 1
            entry["people"].add(row.user_id)
            if is_spent:
                entry["spent"] += cost

        person = travellers.setdefault(
            row.user_id,
            {"user_id": row.user_id, "full_name": row.user.full_name if row.user else "Unknown",
             "count": 0, "nights": 0, "spent": Decimal("0.00")},
        )
        person["count"] += 1
        person["nights"] += _nights(request) or 0
        if is_spent:
            person["spent"] += cost

        day = movement_date(request)
        if day is not None:
            key = bucket_key(day, grain)
            if key in trend:
                trend[key]["movements"] += 1
                trend[key]["people"].add(row.user_id)
                if is_spent:
                    trend[key]["spent"] += cost

    # One conflict check per awaiting request, not per row: tens at most.
    clashing = sum(
        1
        for request in awaiting.values()
        if request_service.check_conflicts(db, tenant_id=tenant_id, request=request)
    )

    booked = statuses.get(str(TravellerStatus.BOOKED), 0)
    costed = booked - uncosted
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "grain": grain,
        "currency": costs.DEFAULT_CURRENCY,
        "kpis": {
            "movements": sum(1 for row in rows if row.status in TRAVELLED),
            "requests": len(live_requests),
            "people": len(live_people),
            "nights": nights,
            "pending": statuses.get(str(TravellerStatus.PENDING), 0),
            "approved": statuses.get(str(TravellerStatus.APPROVED), 0),
            "booked": booked,
            "rejected": statuses.get(str(TravellerStatus.REJECTED), 0),
            "cancelled": statuses.get(str(TravellerStatus.CANCELLED), 0),
            "spent": str(spent),
            "committed": str(committed),
            "uncosted": uncosted,
            "average_per_booking": str(
                (spent / costed).quantize(costs.PAISA) if costed > 0 else Decimal("0.00")
            ),
            # Travelled movements with no destination state on record.
            "unstated": unstated,
        },
        "awaiting": {
            "requests": len(awaiting),
            "people": len(awaiting_people),
            "partly_approved": len(partly),
            "with_conflicts": clashing,
        },
        "trend": [
            {"period": b["period"], "movements": b["movements"], "people": len(b["people"]),
             "spent": str(b["spent"])}
            for b in trend.values()
        ],
        "by_status": [
            {"status": str(s), "count": statuses.get(str(s), 0)} for s in TravellerStatus
        ],
        "by_type": [{**b, "spent": str(b["spent"])} for b in types.values()],
        "by_mode": _top(modes),
        "top_states": _top(dest_states),
        "top_places": _top(dest_places, 10),
        "by_campaign": sorted(
            (
                {**c, "people": len(c["people"]), "spent": str(c["spent"])}
                for c in campaigns.values()
            ),
            key=lambda c: (-c["count"], c["code"]),
        )[:10],
        "top_travellers": sorted(
            ({**p, "spent": str(p["spent"])} for p in travellers.values()),
            key=lambda p: (-p["count"], p["full_name"]),
        )[:10],
    }


def _person_status(status: UserStatus | None, is_active: bool) -> str:
    # Rows switched off through the old is_active flag alone still read as
    # ACTIVE in `status`; they cannot sign in, so they are not shown as active.
    if status in (None, UserStatus.ACTIVE) and not is_active:
        return str(UserStatus.DEACTIVATED)
    return str(status or UserStatus.ACTIVE)


def filter_options(db: Session, tenant_id: str) -> dict:
    """What the filter dropdowns offer: campaigns, people, and places in use.

    Places are destinations only, matching what the state and city filters
    match, and only from submitted requests: a state that appears only in
    someone's draft would be a choice that always comes back empty. Each person
    carries their status, so a picker can mark someone who has left.
    """
    projects = db.execute(
        select(Project.id, Project.code, Project.name, Project.status)
        .where(Project.tenant_id == tenant_id)
        .order_by(Project.code)
    ).all()
    people = db.execute(
        select(User.id, User.full_name, User.employee_code, User.is_active, User.status)
        .where(User.tenant_id == tenant_id)
        .order_by(User.full_name)
    ).all()

    used_states: set[str] = set()
    used_cities: set[tuple[str | None, str]] = set()
    for kind, dest_state, dest, drop_city, hotel_state, hotel_city in db.execute(
        select(
            TravelRequest.request_type,
            TravelRequest.destination_state,
            TravelRequest.destination,
            TravelRequest.drop_city,
            TravelRequest.hotel_state,
            TravelRequest.hotel_city,
        )
        .where(TravelRequest.tenant_id == tenant_id, TravelRequest.is_draft.is_(False))
        .distinct()
    ).all():
        if kind is RequestType.HOTEL:
            state, city = hotel_state, hotel_city
        elif kind is RequestType.LOCAL_CAB:
            state, city = dest_state, drop_city
        else:
            state, city = dest_state, dest
        if state:
            used_states.add(state)
        if city:
            used_cities.add((state, city))

    return {
        "projects": [
            {"id": p.id, "code": p.code, "name": p.name, "status": str(p.status)} for p in projects
        ],
        "people": [
            {"id": u.id, "full_name": u.full_name, "employee_code": u.employee_code,
             "is_active": u.is_active, "status": _person_status(u.status, u.is_active)}
            for u in people
        ],
        "states": sorted(used_states),
        "cities": [
            {"state": state, "city": city}
            for state, city in sorted(used_cities, key=lambda sc: (sc[0] or "", sc[1].lower()))
        ],
    }
