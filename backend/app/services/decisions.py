"""
Admin decisions on individual travellers (SOW section 4).

The decision unit is one person on one request, never the request as a whole -
that is addendum B1, and it is what "selective approval" means in practice. A
group of four can come out of this queue as two approved, one rejected and one
still pending, and the request-level status is derived from that rather than set.

Three rules are enforced here rather than in the router, because every entry
point has to obey them identically:

* **Transitions are checked, not assumed.** `ALLOWED_TRAVELLER_TRANSITIONS` is the
  only definition of what may follow what. A decision is not undoable in V1;
  correcting one goes through cancel-and-reraise, exactly like an edit after the
  lock.
* **A rejection needs a reason, and so does approving over a conflict.** The
  second is addendum B6's other half: conflicts warn the requester, but an admin
  who approves anyway has to say why, and that reason lands in the ledger as
  `OVERRIDE_CONFLICT` *before* the approval it justifies.
* **Every decision notifies the traveller.** In app and, from Phase 5, by email -
  through the notification ledger, so "was this person told?" has an answer
  months later. Their manager is copied on the email and gets an in-app copy,
  because the decision is the second of two levels and the manager gave the
  first (`services/recommendations.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.enums import (
    ALLOWED_TRAVELLER_TRANSITIONS,
    AuditAction,
    RequestType,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import audit, conflicts, notifications, recommendations
from app.services.requests import RIDING

#: Which audit action records which decision.
_ACTION = {
    TravellerStatus.APPROVED: AuditAction.APPROVE,
    TravellerStatus.REJECTED: AuditAction.REJECT,
    TravellerStatus.BOOKED: AuditAction.BOOK,
    TravellerStatus.CANCELLED: AuditAction.CANCEL,
}

_VERB = {
    TravellerStatus.APPROVED: "approved",
    TravellerStatus.REJECTED: "rejected",
    TravellerStatus.BOOKED: "booked",
    TravellerStatus.CANCELLED: "cancelled",
}


@dataclass
class Decision:
    """One admin decision, as the batch endpoint receives it."""

    traveller_id: int
    to_status: TravellerStatus
    reason: str | None = None
    booking_reference: str | None = None
    booking_details: dict | None = None
    conflict_override_reason: str | None = None


#: How each booking detail reads in an email, in order.
_DETAIL_LABELS = (
    ("carrier", "Airline / operator"),
    ("service_number", "Flight / train / bus no."),
    ("depart_at", "Departs"),
    ("arrive_at", "Arrives"),
    ("seat", "Seat / berth"),
    ("hotel_name", "Hotel"),
    ("hotel_address", "Address"),
    ("notes", "Notes"),
)


def booking_lines(traveller: RequestTraveller) -> list[str]:
    """The traveller's booking as lines for an email: reference first."""
    lines = []
    if traveller.booking_reference:
        lines.append(f"  Booking reference   {traveller.booking_reference}")
    for key, label in _DETAIL_LABELS:
        value = (traveller.booking_details or {}).get(key)
        if not value:
            continue
        if key in ("depart_at", "arrive_at"):
            try:
                value = clock.time_label(datetime.fromisoformat(value))
            except ValueError:
                pass
        lines.append(f"  {label:<20}{value}")
    return lines


def assert_transition(current: TravellerStatus, target: TravellerStatus) -> None:
    allowed = ALLOWED_TRAVELLER_TRANSITIONS.get(current, set())
    if target in allowed:
        return
    if current is target:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"This traveller is already {_VERB.get(target, str(target).lower())}.",
        )
    # The one refusal that needs its own wording. Booking someone still pending
    # is an ordinary mistake with an ordinary fix, and the generic message below
    # would tell them to cancel the request - actively wrong advice.
    if current is TravellerStatus.PENDING and target is TravellerStatus.BOOKED:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "This traveller has not been approved yet. Approve them first, "
                "then attach the booking."
            ),
        )
    raise HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=(
            f"A traveller who is {str(current).lower()} cannot be "
            f"{_VERB.get(target, str(target).lower())}. "
            "Cancel this traveller and raise a new request instead."
        ),
    )


def live_conflicts_for(
    db: Session, *, tenant_id: str, request: TravelRequest, traveller: RequestTraveller
) -> list[conflicts.Conflict]:
    """What this one person clashes with, right now.

    Recomputed at decision time rather than trusted from the requester's screen:
    the queue may have been open for an hour, and someone else's trip may have
    landed on the same dates since.
    """
    return conflicts.detect(
        db,
        tenant_id=tenant_id,
        candidate=conflicts.Itinerary.from_request(request),
        user_ids=[traveller.user_id],
        exclude_request_id=request.id,
    )


def _notify(
    db: Session,
    *,
    tenant_id: str,
    traveller: RequestTraveller,
    request: TravelRequest,
    target: TravellerStatus,
    reason: str | None,
    actor: User | None = None,
) -> None:
    """Tell the traveller what happened to them, with their manager copied.

    Written per traveller, not per request, because on a group request the four
    people on it may have had four different answers. Goes through the ledger, so
    the email attempt is recorded alongside the in-app row.

    The manager is on the email's Cc line rather than sent a second message, so
    both read the same words - the admin's comment and, if they gave one, the
    manager's own recommendation. They also get an in-app copy for the bell.
    """
    person = traveller.user
    if person is None:
        return
    manager = person.active_manager

    where = (
        request.hotel_city
        if request.request_type is RequestType.HOTEL
        else request.route_label(" to ")
    )
    kind_of_trip = str(request.request_type).replace("_", " ").lower()
    short = f"Your {kind_of_trip} request for {where} was {_VERB[target]}."
    # A cab's booking is its car and driver. When an admin has recorded them,
    # the decision carries them, so the traveller is not left waiting for a
    # second message to find out what to look for at the kerb. Before the
    # reason, which is typed without a full stop.
    is_cab = request.request_type is RequestType.LOCAL_CAB
    car = request.cab_sent_label if is_cab and target in RIDING else None
    if car:
        short += f" Your cab: {car}."
    if reason:
        short += f" Reason: {reason}"

    greeting = person.full_name.split()[0] if person.full_name else "there"
    detail = [f"Hello {greeting},", "", short]
    if is_cab and request.cab_asked_label:
        detail += ["", f"Cab asked for: {request.cab_asked_label}"]
    advice = recommendations.describe(traveller)
    if advice:
        detail += ["", f"Your manager's recommendation: {advice}"]
    if target is TravellerStatus.APPROVED and not car:
        detail += [
            "",
            "The cab's details will follow once it is booked."
            if is_cab
            else "Tickets will follow once they are booked.",
        ]
    if target is TravellerStatus.BOOKED:
        booked = booking_lines(traveller)
        if booked:
            detail += ["", "Your booking:", *booked]
    if request.project:
        detail += ["", f"Campaign: {request.project.code} - {request.project.name}"]
    if manager is not None:
        detail += ["", f"{manager.full_name} is copied on this email."]

    notifications.notify(
        db,
        tenant_id=tenant_id,
        user=person,
        kind=f"REQUEST_{target}",
        title=f"Your request was {_VERB[target]}",
        body=short,
        request_id=request.id,
        email_subject=f"Travel request {_VERB[target]} - {where}",
        email_body="\n".join(detail),
        cc_users=[manager] if manager is not None else None,
    )

    by = f" by {actor.full_name}" if actor is not None else ""
    copy = f"{person.full_name}'s {kind_of_trip} request for {where} was {_VERB[target]}{by}."
    if reason:
        copy += f" Reason: {reason}"
    copy_manager(
        db,
        tenant_id=tenant_id,
        person=person,
        request=request,
        title=f"{person.full_name}'s request was {_VERB[target]}",
        body=copy,
    )


def copy_manager(
    db: Session, *, tenant_id: str, person: User, request: TravelRequest, title: str, body: str
) -> None:
    """The manager's in-app copy of a decision on one of their team.

    In app only: the email itself reached them as a Cc on the traveller's, and
    a second message would say the same thing twice.
    """
    manager = person.active_manager
    if manager is None:
        return
    notifications.notify(
        db,
        tenant_id=tenant_id,
        user=manager,
        kind="DECISION_COPY",
        title=title,
        body=body,
        request_id=request.id,
        send_email=False,
    )


def apply(
    db: Session,
    *,
    tenant_id: str,
    actor: User,
    request: TravelRequest,
    traveller: RequestTraveller,
    decision: Decision,
    http_request=None,
    notify: bool = True,
) -> RequestTraveller:
    """Move one traveller to a new status, with everything that has to go with it.

    The caller owns the commit, so the decision, its audit row, any conflict
    override and the traveller's notification all land together or not at all.
    """
    target = decision.to_status
    previous = traveller.status
    assert_transition(previous, target)

    # Every decision costs a sentence, not just a rejection. An approval with no
    # reason is the one someone asks about six months later, and the answer
    # "because it was approved" is not one.
    if not (decision.reason or "").strip():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{_VERB.get(target, str(target).lower()).capitalize()} needs a "
                "reason - it is shown to the traveller and kept in the ledger."
            ),
        )

    if target is TravellerStatus.BOOKED and not (decision.booking_reference or "").strip():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Marking a traveller booked needs a ticket or booking reference.",
        )

    # Addendum B6, the admin half: approving over a live clash costs a typed
    # reason, and that reason is written to the ledger before the approval is.
    if target is TravellerStatus.APPROVED:
        clashes = live_conflicts_for(
            db, tenant_id=tenant_id, request=request, traveller=traveller
        )
        if clashes:
            override = (decision.conflict_override_reason or "").strip()
            if not override:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=(
                        f"{traveller.user.full_name} has a clash on these dates. "
                        "Approving anyway needs a typed reason, which is recorded."
                    ),
                )
            audit.record(
                db,
                action=AuditAction.OVERRIDE_CONFLICT,
                entity_type="request_traveller",
                entity_id=traveller.id,
                summary=(
                    f"{actor.full_name} approved {traveller.user.full_name} on request "
                    f"{request.id} over {len(clashes)} clash(es)"
                ),
                changes={"conflicts": [c.message for c in clashes]},
                reason=override,
                tenant_id=tenant_id,
                actor=actor,
                request=http_request,
            )

    traveller.status = target
    traveller.decided_by_id = actor.id
    traveller.decided_at = naive_utcnow()
    traveller.decision_reason = (decision.reason or "").strip() or None
    if target is TravellerStatus.BOOKED:
        traveller.booking_reference = decision.booking_reference.strip()
        if decision.booking_details:
            traveller.booking_details = decision.booking_details

    audit.record(
        db,
        action=_ACTION[target],
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=(
            f"{actor.full_name} {_VERB[target]} {traveller.user.full_name} "
            f"on request {request.id}"
        ),
        changes={"status": {"from": str(previous), "to": str(target)}},
        reason=traveller.decision_reason,
        tenant_id=tenant_id,
        actor=actor,
        request=http_request,
    )

    # The ticket-confirmation path sends a richer message of its own, so it
    # turns this one off rather than mailing the traveller twice.
    if notify:
        _notify(
            db,
            tenant_id=tenant_id,
            traveller=traveller,
            request=request,
            target=target,
            reason=traveller.decision_reason,
            actor=actor,
        )
    db.flush()
    return traveller
