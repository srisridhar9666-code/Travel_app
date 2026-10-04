"""
Cancelling a trip that has already been decided.

Before any admin acts, a requester withdraws a request on their own - nothing
has been booked. Once a traveller is approved or booked, a ticket or a cab may
already exist, so withdrawing becomes an ask: the requester gives a reason, and
an admin or the requester's manager approves it (the trip is then cancelled
exactly as an admin cancelling it would) or rejects it with a comment. Everyone
it concerns is told, and every step is in the activity log.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import (
    ADMIN_ROLES,
    AuditAction,
    CancellationStatus,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import TravelRequest
from app.models.user import User
from app.services import audit, notifications
from app.services.requests import queued_emails, trip_summary

#: Once anyone on the trip is here, the requester asks rather than cancels.
DECIDED = frozenset({TravellerStatus.APPROVED, TravellerStatus.BOOKED})


def needs_approval(request: TravelRequest, user: User) -> bool:
    """Whether this person's cancel has to be approved first.

    Admins cancel directly - they are the ones who would approve it. Everyone
    else does too until an admin has approved or booked someone on it.
    """
    if user.is_admin:
        return False
    return any(t.status in DECIDED for t in request.travellers)


def may_decide(request: TravelRequest, user: User) -> bool:
    """An admin, or the requester's own active manager."""
    if user.is_admin:
        return True
    requester = request.requester
    manager = requester.active_manager if requester is not None else None
    return manager is not None and manager.id == user.id


def is_pending(request: TravelRequest) -> bool:
    return request.cancellation_status is CancellationStatus.PENDING and not request.is_cancelled


def cancel(
    db: Session,
    *,
    request: TravelRequest,
    actor: User,
    reason: str,
    http_request=None,
) -> None:
    """Cancel the trip now: every traveller not already rejected is cancelled.
    The caller commits."""
    request.is_cancelled = True
    request.cancel_reason = reason
    request.cancelled_by_id = actor.id
    for traveller in request.travellers:
        if traveller.status is not TravellerStatus.REJECTED:
            traveller.status = TravellerStatus.CANCELLED
    audit.record(
        db,
        action=AuditAction.CANCEL,
        entity_type="travel_request",
        entity_id=request.id,
        summary=f"{actor.full_name} cancelled request {request.id}",
        reason=reason,
        tenant_id=request.tenant_id,
        actor=actor,
        request=http_request,
    )


def _admins(db: Session, request: TravelRequest, *, besides: User) -> list[User]:
    return list(
        db.execute(
            select(User).where(
                User.tenant_id == request.tenant_id,
                User.role.in_(ADMIN_ROLES),
                User.is_active.is_(True),
                User.id != besides.id,
            )
        )
        .scalars()
        .all()
    )


def ask(
    db: Session, *, request: TravelRequest, asker: User, reason: str, http_request=None
) -> list[int]:
    """Record the ask and tell the admins and the requester's manager, who may
    each decide it. Returns the email rows to send after the response."""
    if is_pending(request):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelling this trip is already waiting for approval.",
        )
    request.cancellation_status = CancellationStatus.PENDING
    request.cancellation_reason = reason
    request.cancellation_requested_by_id = asker.id
    request.cancellation_requested_at = naive_utcnow()
    request.cancellation_decided_by_id = None
    request.cancellation_decided_at = None
    request.cancellation_comment = None
    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=f"{asker.full_name} asked to cancel request {request.id}",
        changes={"cancellation_status": {"from": None, "to": str(CancellationStatus.PENDING)}},
        reason=reason,
        tenant_id=request.tenant_id,
        actor=asker,
        request=http_request,
    )
    db.flush()

    summary = trip_summary(request)
    base = get_settings().frontend_base_url.rstrip("/")
    deciders: list[tuple[User, str]] = [(a, f"{base}/approvals") for a in _admins(db, request, besides=asker)]
    manager = request.requester.active_manager if request.requester else None
    if manager is not None and manager.id != asker.id and not manager.is_admin:
        deciders.append((manager, f"{base}/team-approvals"))

    queued: list[int] = []
    for person, link in deciders:
        first = person.full_name.split()[0] if person.full_name else "there"
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=request.tenant_id,
                user=person,
                kind="CANCELLATION_REQUESTED",
                title=f"{asker.full_name} asked to cancel a trip"[:200],
                body=f"{summary}. Reason: {reason}",
                request_id=request.id,
                email_subject=f"Cancellation to approve: {summary}"[:255],
                email_body="\n".join(
                    [
                        f"Hello {first},",
                        "",
                        f"{asker.full_name} asked to cancel a trip that is already approved or booked.",
                        "",
                        summary,
                        f"Their reason: {reason}",
                        "",
                        f"Approve or reject it: {link}",
                    ]
                ),
                deliver_now=False,
            )
        )
    return queued


def decide(
    db: Session,
    *,
    request: TravelRequest,
    decider: User,
    approve: bool,
    comment: str | None,
    http_request=None,
) -> list[int]:
    """Approve (the trip is cancelled) or reject (it stands, with the comment
    the requester reads). The requester is told with their manager copied, and
    when a manager approves, the admins are told so the booking is called off."""
    if not may_decide(request, decider):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin or the requester's manager can decide this.",
        )
    if not is_pending(request):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no cancellation waiting on this trip.",
        )
    if not approve and not (comment or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Say why the trip should go ahead - the requester is shown it.",
        )

    request.cancellation_status = (
        CancellationStatus.APPROVED if approve else CancellationStatus.REJECTED
    )
    request.cancellation_decided_by_id = decider.id
    request.cancellation_decided_at = naive_utcnow()
    request.cancellation_comment = (comment or "").strip() or None
    verdict = "approved" if approve else "rejected"
    audit.record(
        db,
        action=AuditAction.APPROVE if approve else AuditAction.REJECT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=f"{decider.full_name} {verdict} cancelling request {request.id}",
        changes={
            "cancellation_status": {
                "from": str(CancellationStatus.PENDING),
                "to": str(request.cancellation_status),
            }
        },
        reason=request.cancellation_comment,
        tenant_id=request.tenant_id,
        actor=decider,
        request=http_request,
    )
    if approve:
        cancel(
            db,
            request=request,
            actor=decider,
            reason=request.cancellation_reason or "Cancellation approved",
            http_request=http_request,
        )
    db.flush()

    summary = trip_summary(request)
    queued: list[int] = []
    asker = request.cancellation_requested_by or request.requester
    if asker is not None:
        manager = asker.active_manager
        said = f" Comment: {request.cancellation_comment}" if request.cancellation_comment else ""
        outcome = (
            "It is cancelled."
            if approve
            else "The trip goes ahead as booked."
        )
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=request.tenant_id,
                user=asker,
                kind="CANCELLATION_APPROVED" if approve else "CANCELLATION_REJECTED",
                title=f"Cancellation {verdict}",
                body=f"Your ask to cancel {summary} was {verdict} by {decider.full_name}. {outcome}{said}",
                request_id=request.id,
                email_subject=f"Cancellation {verdict}: {summary}"[:255],
                email_body=(
                    f"Hello {asker.full_name.split()[0]},\n\n"
                    f"Your ask to cancel this trip was {verdict} by {decider.full_name}.\n\n"
                    f"{summary}\n{outcome}{said}"
                ),
                cc_users=[manager] if manager is not None and manager.id != decider.id else None,
                deliver_now=False,
            )
        )
    # A manager's yes leaves a booking someone has to call off with the vendor.
    if approve and not decider.is_admin:
        for admin in _admins(db, request, besides=decider):
            notifications.notify(
                db,
                tenant_id=request.tenant_id,
                user=admin,
                kind="CANCELLATION_APPROVED",
                title=f"{decider.full_name} approved cancelling a trip"[:200],
                body=f"{summary} is cancelled. Call off any booking with the vendor.",
                request_id=request.id,
                send_email=False,
            )
    return queued
