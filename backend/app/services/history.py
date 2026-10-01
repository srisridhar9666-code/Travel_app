"""
Per-employee travel history (SOW section 5).

> "Each employee profile features a timeline view showing a chronological
> history of their movements. This tracks all travel dates, modes of transport,
> cab companions, and hotel stay locations."

The question this answers is "where has this person been, and with whom" - which
is not the same question the request list answers. A request belongs to whoever
raised it; a *movement* belongs to everyone named on it. Someone tagged onto a
colleague's cab never appears in their own request list, but they were in the
cab, and an admin reconstructing a month needs to see that.

So the timeline is built from `request_travellers` rather than from requests,
and each entry carries the companions who shared that movement.

The SOW asks for "minimum 2+ months lookback". We keep everything and default
the *view* to 90 days - the cap was an artificial limit on stored data, and
widening a filter is cheaper than recovering history that was never kept
(addendum B11).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import clock
from app.core.enums import RequestType, TravellerStatus
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User

#: What the timeline shows unless asked for more. Section 5 asks for at least
#: two months; three is a round number that covers a quarter's reporting.
DEFAULT_LOOKBACK_DAYS = 90

#: Statuses that represent an actual or intended movement. A rejected traveller
#: did not travel, and showing them would misreport where someone was.
TRAVELLED = (
    TravellerStatus.PENDING,
    TravellerStatus.APPROVED,
    TravellerStatus.BOOKED,
)


def _movement_date(request: TravelRequest) -> date | None:
    """The day this movement starts, whichever field carries it for the type."""
    if request.request_type is RequestType.HOTEL:
        return request.check_in
    return request.start_at.date() if request.start_at else None


def _nights(request: TravelRequest) -> int | None:
    if request.request_type is not RequestType.HOTEL:
        return None
    if not (request.check_in and request.check_out):
        return None
    return max((request.check_out - request.check_in).days, 0)


def _where(request: TravelRequest) -> str:
    """One readable line describing the movement, by type."""
    if request.request_type is RequestType.HOTEL:
        return request.hotel_city or "Hotel"
    origin = request.origin_label or "?"
    destination = request.destination_label or "?"
    return f"{origin} → {destination}"


def build(
    db: Session,
    *,
    user_id: int,
    tenant_id: str,
    since: date | None = None,
    until: date | None = None,
    include_costs: bool = False,
) -> dict:
    """The timeline for one employee, newest movement first."""
    window_start = since or (clock.local_today() - timedelta(days=DEFAULT_LOOKBACK_DAYS))

    rows = (
        db.execute(
            select(RequestTraveller)
            .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
            .options(
                selectinload(RequestTraveller.request)
                .selectinload(TravelRequest.travellers)
            )
            .where(
                RequestTraveller.user_id == user_id,
                # The tenant lives on the request, not the traveller row.
                TravelRequest.tenant_id == tenant_id,
                RequestTraveller.status.in_(TRAVELLED),
                TravelRequest.is_draft.is_(False),
                TravelRequest.is_cancelled.is_(False),
                # Which column carries the date depends on the request type, so
                # the window has to branch on it. A flat OR across both columns
                # looks equivalent and is not: every non-hotel request has a
                # NULL check_in, so "check_in IS NULL" matched everything and
                # the window silently excluded nothing at all.
                #
                # A movement with no date is kept rather than dropped, and
                # sorted to the end below.
                or_(
                    and_(
                        TravelRequest.request_type == RequestType.HOTEL,
                        or_(
                            TravelRequest.check_in.is_(None),
                            TravelRequest.check_in >= window_start,
                        ),
                    ),
                    and_(
                        TravelRequest.request_type != RequestType.HOTEL,
                        or_(
                            TravelRequest.start_at.is_(None),
                            TravelRequest.start_at
                            >= datetime.combine(window_start, datetime.min.time()),
                        ),
                    ),
                ),
            )
        )
        .scalars()
        .all()
    )

    # Names for every companion in one query rather than one per movement.
    companion_ids = {
        traveller.user_id
        for row in rows
        for traveller in row.request.travellers
        if traveller.user_id != user_id
    }
    names = {
        uid: (full_name, designation)
        for uid, full_name, designation in db.execute(
            select(User.id, User.full_name, User.designation).where(
                User.id.in_(companion_ids or {0})
            )
        ).all()
    }

    entries = []
    for row in rows:
        request = row.request
        started = _movement_date(request)

        if until and started and started > until:
            continue
        if since and started and started < since:
            continue

        companions = [
            {
                "user_id": other.user_id,
                "full_name": names.get(other.user_id, ("Unknown", None))[0],
                "designation": names.get(other.user_id, (None, None))[1],
                "status": str(other.status),
            }
            for other in request.travellers
            if other.user_id != user_id and other.status in TRAVELLED
        ]

        share_with = None
        if row.share_with_user_id:
            share_with = names.get(row.share_with_user_id, ("Unknown", None))[0]

        entries.append(
            {
                "request_id": request.id,
                "request_type": str(request.request_type),
                "mode": str(request.mode) if request.mode else None,
                "status": str(row.status),
                "where": _where(request),
                "origin": request.origin,
                "destination": request.destination,
                "pickup_city": request.pickup_city,
                "drop_city": request.drop_city,
                "hotel_city": request.hotel_city,
                "started_on": started.isoformat() if started else None,
                "start_at": request.start_at.isoformat() if request.start_at else None,
                "end_at": request.end_at.isoformat() if request.end_at else None,
                "check_in": request.check_in.isoformat() if request.check_in else None,
                "check_out": request.check_out.isoformat() if request.check_out else None,
                "nights": _nights(request),
                "project_id": request.project_id,
                "project_name": request.project.name if request.project else None,
                "project_code": request.project.code if request.project else None,
                "booking_reference": row.booking_reference,
                "companions": companions,
                "room_sharing": str(row.room_sharing),
                "share_with_name": share_with,
                "share_confirmed": row.share_confirmed_at is not None,
                # Cost is an admin concern. A ground staff member reading their
                # own timeline has no business seeing what the company paid.
                "cost_amount": (
                    str(row.cost_amount) if include_costs and row.cost_amount is not None else None
                ),
                "cost_currency": row.cost_currency if include_costs else None,
            }
        )

    # Newest first. Undated movements sort to the end rather than crashing the
    # comparison or pretending to be very old.
    entries.sort(key=lambda e: (e["started_on"] is not None, e["started_on"] or ""), reverse=True)

    return {
        "user_id": user_id,
        "since": window_start.isoformat(),
        "until": until.isoformat() if until else None,
        "entries": entries,
        "summary": _summarise(entries),
    }


def _summarise(entries: list[dict]) -> dict:
    """The headline numbers a profile shows above the timeline."""
    # A cab's drop is a street address; its city is what counts as a place.
    cities = {
        entry["hotel_city"] or entry["drop_city"] or entry["destination"]
        for entry in entries
        if entry["hotel_city"] or entry["drop_city"] or entry["destination"]
    }
    companions = {
        companion["user_id"] for entry in entries for companion in entry["companions"]
    }
    return {
        "movements": len(entries),
        "nights_away": sum(entry["nights"] or 0 for entry in entries),
        "cities": len(cities),
        "travelled_with": len(companions),
        "by_type": {
            kind: sum(1 for entry in entries if entry["request_type"] == kind)
            for kind in ("LONG_DISTANCE", "LOCAL_CAB", "HOTEL")
        },
    }
