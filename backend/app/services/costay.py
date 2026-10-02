"""
Room sharing: who could share, and who may (SOW section 3, addendum B7 / C2).

Two rules, deliberately separate.

**Who could share** is a calendar question. Someone is a candidate if they hold a
live hotel traveller row - `ACTIVE_TRAVELLER_STATUSES` on a request that is
neither a draft nor cancelled - in the same city, on at least one of the same
nights. Pending counts as well as booked: the point of the prompt is to catch the
pairing before two rooms are reserved, and by the time both are BOOKED the saving
has already been missed.

**Who may share** is a policy question, and the policy is narrow. A shared room
is offered only on an **exact** `Gender` match between two people who have stated
a binary value. `OTHER` and `UNDISCLOSED` never share - not because the pairing
would be wrong, but because this system has not asked them, and a room is not
something to infer consent for. They are offered a separate room, silently; the
requester is never told a candidate was filtered out on gender grounds.

Open question **C2** is answered here by assumption, pending the client:

* the prompt shows the colleague's **name and designation** - this is an internal
  ops tool, and "1 colleague available" is not enough to make a decision on;
* the requester's choice is a *request*. `SHARE_EXISTING` sets
  `room_sharing` but leaves `share_confirmed_at` null until an **admin**
  confirms it in Phase 4;
* the colleague is **notified** when someone asks to share with them.

The colleague's own consent is not required. If the client wants it, the shape to
change is `confirm_share` plus one more column on `request_travellers` - the
matching and the disclosure rules above do not move.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    ACTIVE_TRAVELLER_STATUSES,
    Gender,
    RequestType,
    RoomSharingChoice,
)
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications

#: Genders that may be paired into one room, and only with themselves.
_SHAREABLE = {Gender.MALE, Gender.FEMALE}


def may_share_room(a: Gender | None, b: Gender | None) -> bool:
    """An exact match between two stated binary values, and nothing else.

    `OTHER` with `OTHER` is deliberately false: identical labels are not consent,
    and the fallback (a separate room) costs money rather than dignity.
    """
    if a is None or b is None:
        return False
    return a is b and a in _SHAREABLE


@dataclass(frozen=True)
class CoStayMatch:
    """A colleague who could share, as shown to the requester."""

    user_id: int
    full_name: str
    designation: str | None
    request_id: int
    hotel_city: str
    check_in: date
    check_out: date | None
    overlapping_nights: int
    status: str


def _overlap_nights(a_in: date, a_out: date, b_in: date, b_out: date) -> int:
    start = max(a_in, b_in)
    end = min(a_out, b_out)
    return max((end - start).days, 0)


def find_matches(
    db: Session,
    *,
    tenant_id: str,
    for_user: User,
    city: str,
    check_in: date,
    check_out: date | None,
    exclude_request_id: int | None = None,
) -> list[CoStayMatch]:
    """Colleagues this person could be offered a shared room with.

    Already filtered by the gender policy, so the caller never sees a candidate
    it must then reject - and never learns that one existed.
    """
    if not city or check_in is None:
        return []

    out = check_out or date.fromordinal(check_in.toordinal() + 1)
    if out <= check_in:
        out = date.fromordinal(check_in.toordinal() + 1)

    rows = db.execute(
        select(RequestTraveller, TravelRequest, User)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .join(User, User.id == RequestTraveller.user_id)
        .where(
            TravelRequest.tenant_id == tenant_id,
            TravelRequest.request_type == RequestType.HOTEL,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
            RequestTraveller.status.in_(ACTIVE_TRAVELLER_STATUSES),
            User.id != for_user.id,
            User.is_active.is_(True),
        )
    ).all()

    matches: list[CoStayMatch] = []
    seen: set[int] = set()

    for traveller, request, colleague in rows:
        if exclude_request_id is not None and request.id == exclude_request_id:
            continue
        if colleague.id in seen:
            continue
        if not request.hotel_city or request.hotel_city.strip().casefold() != city.strip().casefold():
            continue
        if not may_share_room(for_user.gender, colleague.gender):
            continue

        their_in = request.check_in
        if their_in is None:
            continue
        their_out = request.check_out or date.fromordinal(their_in.toordinal() + 1)
        if their_out <= their_in:
            their_out = date.fromordinal(their_in.toordinal() + 1)

        nights = _overlap_nights(check_in, out, their_in, their_out)
        if nights <= 0:
            continue

        seen.add(colleague.id)
        matches.append(
            CoStayMatch(
                user_id=colleague.id,
                full_name=colleague.full_name,
                designation=str(colleague.designation) if colleague.designation else None,
                request_id=request.id,
                hotel_city=request.hotel_city,
                check_in=their_in,
                check_out=request.check_out,
                overlapping_nights=nights,
                status=str(traveller.status),
            )
        )

    # Most nights in common first: that is the pairing worth the admin's time.
    matches.sort(key=lambda m: (-m.overlapping_nights, m.full_name))
    return matches


def notify_share_request(
    db: Session,
    *,
    tenant_id: str,
    colleague_id: int,
    requester: User,
    request: TravelRequest,
) -> None:
    """Tell the colleague that someone has asked to share their room (C2).

    Sent when the choice is made rather than when an admin confirms it, so the
    other person hears it from the system before they hear it in a corridor.
    Goes through the ledger, so the email attempt is recorded like any other.
    """
    colleague = db.get(User, colleague_id)
    if colleague is None:
        return

    where = request.hotel_city or "the same city"
    short = (
        f"{requester.full_name} has requested to share your accommodation in "
        f"{where} from {request.check_in} to {request.check_out}. "
        "An admin will confirm this before anything is booked."
    )
    greeting = colleague.full_name.split()[0] if colleague.full_name else "there"

    notifications.notify(
        db,
        tenant_id=tenant_id,
        user=colleague,
        kind="COSTAY_REQUESTED",
        title="A colleague has asked to share your room",
        body=short,
        request_id=request.id,
        email_subject=f"Room sharing request - {where}",
        email_body="\n".join(
            [
                f"Hello {greeting},",
                "",
                short,
                "",
                "If this does not suit you, tell your admin before it is confirmed.",
            ]
        ),
    )


def confirm_refusal(
    db: Session, *, request: TravelRequest, traveller: RequestTraveller
) -> str | None:
    """Why an admin may not confirm this traveller's shared room yet, or None.

    The ask was checked when it was saved, but the confirmation can come days
    later: a profile may have changed, and either stay may have been rejected,
    cancelled or moved since. So both people are checked again here, against
    the same rules the matcher used to offer the pairing in the first place.
    """
    colleague = traveller.share_with
    if colleague is None:
        return "The colleague this traveller asked to share with was not found."
    if traveller.status not in ACTIVE_TRAVELLER_STATUSES:
        return f"{traveller.user.full_name} is no longer travelling on this request."
    if not may_share_room(traveller.user.gender, colleague.gender):
        return "These two travellers cannot share a room."
    # Not excluding this request: a colleague tagged onto the same stay is on
    # the trip just as much as one with a stay of their own.
    still_there = find_matches(
        db,
        tenant_id=request.tenant_id,
        for_user=traveller.user,
        city=request.hotel_city or "",
        check_in=request.check_in,
        check_out=request.check_out,
    )
    if not any(match.user_id == colleague.id for match in still_there):
        return (
            f"{colleague.full_name} no longer has a live hotel stay in "
            f"{request.hotel_city} on any of these nights."
        )
    return None


def clear_share(traveller: RequestTraveller) -> None:
    """Drop any share this traveller held, including an admin confirmation.

    Called when an edit moves the stay, because a confirmation granted against
    one set of dates says nothing about another.
    """
    traveller.room_sharing = RoomSharingChoice.NOT_OFFERED
    traveller.share_with_user_id = None
    traveller.share_confirmed_by_id = None
    traveller.share_confirmed_at = None
