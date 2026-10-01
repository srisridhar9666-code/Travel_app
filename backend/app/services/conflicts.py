"""
Conflict detection against a person's existing itinerary (addendum B6).

The SOW says only that the system "validates the dates against existing
itineraries". Read literally that hard-blocks, and on day one it would reject the
most ordinary pairing in the product - the cab to the airport for your own
flight. So the rules are written out here explicitly, and every one of them
**warns**. Nothing in this module can stop a submission.

The rules, in full:

* **Which rows occupy a calendar.** A traveller row counts only if its status is
  in `ACTIVE_TRAVELLER_STATUSES` (PENDING, APPROVED, BOOKED) and its request is
  neither a draft nor cancelled. A rejected person, a cancelled trip and an
  unsubmitted draft are all invisible to this check.

* **Hotels overlap by night, not by timestamp.** A stay occupies the nights
  `[check_in, check_out)`. Checking out on the 5th and into another hotel on the
  5th is not a conflict - that is a normal move. Sharing any night is, whatever
  the cities, because a person sleeps in one bed.

* **Travel overlaps by datetime window.** `[start_at, end_at]`, closed at both
  ends, with a missing `end_at` collapsing the window to the instant of
  departure. Closed rather than half-open because two journeys that merely touch
  still put one person in two places.

* **A cab on the same day as that person's own long-distance leg is not a
  conflict.** This is the airport run, and it is the single most common thing
  ground staff raise. Exempted by calendar day, in both directions.

* **Hotels and journeys never conflict with each other.** You fly somewhere in
  order to sleep there.

* **A same-shape request is reported as a duplicate**, not as an overlap - same
  city and identical dates for a stay, or same route on the same departure day
  for a journey. It is nearly always a double submission rather than a clash, and
  saying so is more useful to the admin than "overlapping travel".

Everything above the database line is a pure function over `Itinerary`, so the
rules can be tested without a session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    ACTIVE_TRAVELLER_STATUSES,
    ConflictKind,
    ConflictSeverity,
    RequestType,
)
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User


@dataclass(frozen=True)
class Itinerary:
    """The shape of one trip, detached from the ORM so the rules stay testable.

    `request_id` is None for the trip currently being raised - it does not exist
    yet, and a candidate must never be compared against itself.
    """

    request_type: RequestType
    request_id: int | None = None
    origin: str | None = None
    destination: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    hotel_city: str | None = None
    check_in: date | None = None
    check_out: date | None = None

    @classmethod
    def from_request(cls, request: TravelRequest) -> "Itinerary":
        return cls(
            request_type=request.request_type,
            request_id=request.id,
            origin=request.origin,
            destination=request.destination,
            start_at=request.start_at,
            end_at=request.end_at,
            hotel_city=request.hotel_city,
            check_in=request.check_in,
            check_out=request.check_out,
        )

    @property
    def is_stay(self) -> bool:
        return self.request_type is RequestType.HOTEL

    @property
    def window(self) -> tuple[datetime, datetime] | None:
        """The closed datetime window a journey occupies, or None if undated."""
        if self.is_stay or self.start_at is None:
            return None
        return self.start_at, (self.end_at or self.start_at)

    @property
    def nights(self) -> tuple[date, date] | None:
        """Half-open `[check_in, check_out)`.

        A stay with no check-out, or one that claims to end on the day it starts,
        still occupies the night it begins - otherwise the interval is empty and
        the stay would silently clash with nothing at all.
        """
        if not self.is_stay or self.check_in is None:
            return None
        out = self.check_out or self.check_in
        if out <= self.check_in:
            out = date.fromordinal(self.check_in.toordinal() + 1)
        return self.check_in, out

    @property
    def days(self) -> set[date]:
        """Every calendar day this trip touches. Used only by the cab exemption."""
        if self.is_stay:
            span = self.nights
            if span is None:
                return set()
            start, end = span
            return {
                date.fromordinal(start.toordinal() + i) for i in range((end - start).days)
            }

        window = self.window
        if window is None:
            return set()
        start, end = window
        count = max((end.date() - start.date()).days, 0)
        return {date.fromordinal(start.date().toordinal() + i) for i in range(count + 1)}


@dataclass(frozen=True)
class Conflict:
    """One warning about one person. Never blocking - see the module docstring."""

    user_id: int
    user_name: str
    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    other_request_id: int | None = None
    other_request_type: RequestType | None = None
    other_summary: str | None = None


def _same_place(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.strip().casefold() == b.strip().casefold()


def _cab_and_own_long_haul(a: Itinerary, b: Itinerary) -> bool:
    """The airport run: a local cab sharing a calendar day with that same
    person's own flight, train or bus. Explicitly not a conflict."""
    kinds = {a.request_type, b.request_type}
    if kinds != {RequestType.LOCAL_CAB, RequestType.LONG_DISTANCE}:
        return False
    return bool(a.days & b.days)


def describe(trip: Itinerary) -> str:
    """A short human label for the *other* side of a conflict."""
    if trip.is_stay:
        span = trip.nights
        where = trip.hotel_city or "hotel"
        if span is None:
            return where
        start, end = span
        return f"{where}, {start.isoformat()} to {end.isoformat()}"

    route = " to ".join(part for part in (trip.origin, trip.destination) if part) or "journey"
    if trip.start_at is None:
        return route
    return f"{route}, {trip.start_at.strftime('%Y-%m-%d %H:%M')}"


def compare(candidate: Itinerary, existing: Itinerary) -> tuple[ConflictKind, str] | None:
    """The whole rule set for one pair of trips, or None if they sit happily
    together. Order matters only for the message wording."""
    if existing.request_id is not None and existing.request_id == candidate.request_id:
        return None

    # Different shapes of trip do not compete for the same resource: a hotel is
    # where you sleep at the end of the journey, not an alternative to it.
    if candidate.is_stay != existing.is_stay:
        return None

    if candidate.is_stay:
        a, b = candidate.nights, existing.nights
        if a is None or b is None:
            return None
        if not (a[0] < b[1] and b[0] < a[1]):
            return None
        if _same_place(candidate.hotel_city, existing.hotel_city) and a == b:
            return (
                ConflictKind.DUPLICATE_REQUEST,
                f"already has a stay in {existing.hotel_city} for exactly these dates",
            )
        return (
            ConflictKind.OVERLAPPING_STAY,
            f"is already booked into {describe(existing)} on one or more of these nights",
        )

    if _cab_and_own_long_haul(candidate, existing):
        return None

    a, b = candidate.window, existing.window
    if a is None or b is None:
        return None
    if not (a[0] <= b[1] and b[0] <= a[1]):
        return None

    if (
        candidate.request_type is existing.request_type
        and _same_place(candidate.origin, existing.origin)
        and _same_place(candidate.destination, existing.destination)
        and a[0].date() == b[0].date()
    ):
        return (
            ConflictKind.DUPLICATE_REQUEST,
            f"already has the same journey that day - {describe(existing)}",
        )

    return (
        ConflictKind.OVERLAPPING_TRAVEL,
        f"is already travelling then - {describe(existing)}",
    )


def existing_itineraries(
    db: Session,
    *,
    tenant_id: str,
    user_ids: list[int],
    exclude_request_id: int | None = None,
) -> dict[int, list[Itinerary]]:
    """Everything already on these people's calendars, keyed by user id.

    Only live rows count: an active traveller status on a request that is neither
    a draft nor cancelled.
    """
    if not user_ids:
        return {}

    rows = db.execute(
        select(RequestTraveller.user_id, TravelRequest)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .where(
            RequestTraveller.user_id.in_(user_ids),
            RequestTraveller.status.in_(ACTIVE_TRAVELLER_STATUSES),
            TravelRequest.tenant_id == tenant_id,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
        )
    ).all()

    out: dict[int, list[Itinerary]] = {uid: [] for uid in user_ids}
    for user_id, request in rows:
        if exclude_request_id is not None and request.id == exclude_request_id:
            continue
        out.setdefault(user_id, []).append(Itinerary.from_request(request))
    return out


def detect(
    db: Session,
    *,
    tenant_id: str,
    candidate: Itinerary,
    user_ids: list[int],
    exclude_request_id: int | None = None,
) -> list[Conflict]:
    """Check one proposed trip against every traveller's existing itinerary.

    Returns warnings, in traveller order. An empty list means the calendar is
    clear; a non-empty one never prevents the request being saved.
    """
    calendars = existing_itineraries(
        db, tenant_id=tenant_id, user_ids=user_ids, exclude_request_id=exclude_request_id
    )
    names = {
        u.id: u.full_name
        for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars()
    } if user_ids else {}

    found: list[Conflict] = []
    for user_id in user_ids:
        for existing in calendars.get(user_id, []):
            verdict = compare(candidate, existing)
            if verdict is None:
                continue
            kind, message = verdict
            found.append(
                Conflict(
                    user_id=user_id,
                    user_name=names.get(user_id, f"User {user_id}"),
                    kind=kind,
                    severity=ConflictSeverity.WARNING,
                    message=f"{names.get(user_id, 'This traveller')} {message}.",
                    other_request_id=existing.request_id,
                    other_request_type=existing.request_type,
                    other_summary=describe(existing),
                )
            )
    return found
