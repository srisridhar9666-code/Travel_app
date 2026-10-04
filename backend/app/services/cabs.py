"""
Cabs: the car that was sent, and keeping it one more day.

A cab is raised and decided like any other trip - per traveller, with a booking
reference when they are booked. Two things are particular to it and are held
here rather than in the router, so every path obeys them the same way:

* **The car sent is a fact about the request, not a decision.** Once someone on
  the cab is approved, an admin records which car went, its number plate and
  its driver, and may change them later - vendors swap cars at short notice.
  Every version is in the activity log and everyone riding is told, with their
  manager copied, because a traveller standing on a kerb at 6 a.m. needs the
  plate and the driver's phone, not a booking reference. Recording the car
  moves nobody's status.
* **One more day is asked for, not edited in.** Once an admin has acted the
  request is locked (addendum A1), so a traveller whose work runs over asks an
  admin to keep the cab another day. Approving moves `end_at` a day later, and
  that is all conflict detection needs to see the longer booking. One ask at a
  time; after a decision they may ask again for another day.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import (
    ADMIN_ROLES,
    CAB_TYPE_LABELS,
    AuditAction,
    CabExtensionStatus,
    RequestType,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import CabBookingPayload
from app.services import audit, decisions, invoices, notifications, vendors
from app.services.requests import RIDING, extension_refusal, queued_emails, trip_summary

#: The columns that describe the car sent, as the activity log names them.
_SENT_FIELDS = ("booked_cab_type", "cab_vehicle_number", "cab_driver_name", "cab_driver_phone")


def _when(request: TravelRequest) -> str:
    return request.start_at.strftime("%d %b %Y, %H:%M") if request.start_at else "time to be set"


def _until(request: TravelRequest) -> str:
    return request.end_at.strftime("%d %b %Y, %H:%M") if request.end_at else "no end time"


def riders(request: TravelRequest) -> list[RequestTraveller]:
    """The travellers this cab is carrying: approved or booked."""
    return [t for t in request.travellers if t.status in RIDING and t.user is not None]


def car_lines(request: TravelRequest) -> list[str]:
    """The car, its plate and its driver, laid out for an email read on a phone.
    Empty until an admin has recorded them."""
    if not request.cab_vehicle_number:
        return []
    car = CAB_TYPE_LABELS[request.booked_cab_type] if request.booked_cab_type else "Cab"
    return [
        f"  Cab          {car}",
        f"  Vehicle      {request.cab_vehicle_number}",
        f"  Driver       {request.cab_driver_name}",
        f"  Phone        {request.cab_driver_phone}",
    ]


def _cab_only(request: TravelRequest) -> None:
    if request.request_type is not RequestType.LOCAL_CAB:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Cab details only apply to a cab request.",
        )


def _live(request: TravelRequest) -> None:
    if request.is_draft:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has not been submitted yet.",
        )
    if request.is_cancelled:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has been cancelled.",
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


def _tell_traveller(
    db: Session,
    *,
    request: TravelRequest,
    person: User,
    kind: str,
    title: str,
    short: str,
    lines: list[str],
    subject: str,
    copy_title: str,
    copy_body: str,
) -> list[int]:
    """One traveller's notice, with their manager on the email's Cc line and an
    in-app copy for the manager's bell - the same shape as every decision."""
    manager = person.active_manager
    greeting = person.full_name.split()[0] if person.full_name else "there"
    body = [f"Hello {greeting},", "", *lines]
    if request.project:
        body += ["", f"Campaign: {request.project.code} - {request.project.name}"]
    if manager is not None:
        body += ["", f"{manager.full_name} is copied on this email."]
    rows = notifications.notify(
        db,
        tenant_id=request.tenant_id,
        user=person,
        kind=kind,
        title=title[:200],
        body=short,
        request_id=request.id,
        email_subject=subject[:255],
        email_body="\n".join(body),
        cc_users=[manager] if manager is not None else None,
        deliver_now=False,
    )
    decisions.copy_manager(
        db,
        tenant_id=request.tenant_id,
        person=person,
        request=request,
        title=copy_title[:200],
        body=copy_body,
    )
    return queued_emails(rows)


# ---------------------------------------------------------------------------
# The car sent
# ---------------------------------------------------------------------------


def _record_vendor(
    db: Session, *, request: TravelRequest, admin: User, payload: CabBookingPayload
) -> dict:
    """Record the cab operator as the vendor for everyone riding, when the
    admin named one. Refused for anyone whose cost an approved invoice billed
    to someone else. Returns the change for the activity log, or {}."""
    if "vendor_id" not in payload.model_fields_set:
        return {}
    people = riders(request)
    keeping = {t.vendor_id for t in people if t.vendor_id is not None}
    vendor = vendors.pick(db, request.tenant_id, payload.vendor_id, keeping=keeping)
    new_id = vendor.id if vendor is not None else None
    invoices.guard_cost_change(db, [(t, t.cost_amount, new_id) for t in people])
    moved = {}
    for traveller in people:
        if traveller.vendor_id == new_id:
            continue
        moved[traveller.user.full_name] = {
            "from": vendors.name_of(traveller),
            "to": vendor.name if vendor is not None else None,
        }
        traveller.vendor = vendor
        traveller.vendor_id = new_id
    return {"vendor": moved} if moved else {}


def record_booking(
    db: Session,
    *,
    request: TravelRequest,
    admin: User,
    payload: CabBookingPayload,
    http_request=None,
) -> list[int]:
    """Record, or change, the car sent for a cab, and tell everyone riding.
    When the admin names the cab operator, it is recorded as the vendor paid
    for everyone riding - for the invoice, not for the travellers' eyes.

    Saving the same details twice is a no-op - no log row, no second message.
    The caller owns the commit and returns the queued email ids for sending
    after the response.
    """
    _cab_only(request)
    _live(request)
    if not riders(request):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Approve someone on this cab first, then record the car that was sent.",
        )

    before = {field: getattr(request, field) for field in _SENT_FIELDS}
    first = request.cab_vehicle_number is None
    request.booked_cab_type = payload.booked_cab_type
    request.cab_vehicle_number = payload.vehicle_number
    request.cab_driver_name = payload.driver_name
    request.cab_driver_phone = payload.driver_phone
    car_changes = audit.diff(before, {field: getattr(request, field) for field in _SENT_FIELDS})
    paid = _record_vendor(db, request=request, admin=admin, payload=payload)
    if not car_changes and not paid:
        return []

    if car_changes:
        request.cab_booked_by_id = admin.id
        request.cab_booked_at = naive_utcnow()
        summary = (
            f"{admin.full_name} {'recorded' if first else 'changed'} the cab for request "
            f"#{request.id}: {request.cab_sent_label}"
        )
    else:
        summary = f"{admin.full_name} recorded who was paid for the cab on request #{request.id}"
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="travel_request",
        entity_id=request.id,
        summary=summary,
        changes={**car_changes, **paid},
        tenant_id=request.tenant_id,
        actor=admin,
        request=http_request,
    )
    db.flush()
    queued = (
        invoices.follow_costs(db, riders(request), actor=admin, http_request=http_request)
        if paid
        else []
    )
    # Who was paid is the admins' business; the travellers hear only about the car.
    if not payload.notify or not car_changes:
        return queued

    where = request.route_label(" to ")
    car = CAB_TYPE_LABELS[request.booked_cab_type]
    headline = "Your cab is arranged" if first else "Your cab has changed"
    for traveller in riders(request):
        person = traveller.user
        short = (
            f"{headline}: {car} {request.cab_vehicle_number}. Driver {request.cab_driver_name}, "
            f"{request.cab_driver_phone}. Pickup {_when(request)}."
        )
        queued += _tell_traveller(
            db,
            request=request,
            person=person,
            kind="CAB_DETAILS",
            title=f"{headline}: {car} {request.cab_vehicle_number}",
            short=short,
            lines=[
                f"{headline} for {where}."
                + ("" if first else " Please use these details instead of the earlier ones."),
                "",
                *car_lines(request),
                f"  Pickup       {_when(request)}",
                f"  Until        {_until(request)}",
            ],
            subject=f"{headline} - {where}",
            copy_title=f"{person.full_name}'s cab: {car} {request.cab_vehicle_number}",
            copy_body=f"{person.full_name}'s cab for {where}: {request.cab_sent_label}.",
        )
    return queued


# ---------------------------------------------------------------------------
# One more day
# ---------------------------------------------------------------------------


def ask_extension(
    db: Session,
    *,
    request: TravelRequest,
    asker: User,
    reason: str,
    http_request=None,
) -> list[int]:
    """Ask for the cab one more day. Admins are told in app and by email, and
    the requester's manager in app; nothing about the booking changes until an
    admin approves."""
    refusal = extension_refusal(request, asker)
    if refusal is not None:
        code, detail = refusal
        raise HTTPException(status_code=code, detail=detail)

    previous = request.cab_extension_status
    request.cab_extension_status = CabExtensionStatus.PENDING
    request.cab_extension_reason = reason
    request.cab_extension_requested_by_id = asker.id
    request.cab_extension_requested_at = naive_utcnow()
    # The last answer belonged to the last ask; it stays in the activity log.
    request.cab_extension_decided_by_id = None
    request.cab_extension_decided_at = None
    request.cab_extension_comment = None

    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=f"{asker.full_name} asked to extend cab request #{request.id} by a day",
        changes={
            "cab_extension_status": {
                "from": str(previous) if previous else None,
                "to": str(CabExtensionStatus.PENDING),
            },
            "end_at": {
                "from": request.end_at,
                "to": request.end_at + timedelta(days=1),
            },
        },
        reason=reason,
        tenant_id=request.tenant_id,
        actor=asker,
        request=http_request,
    )
    db.flush()

    summary = trip_summary(request)
    link = f"{get_settings().frontend_base_url.rstrip('/')}/approvals"
    title = f"{asker.full_name} asked to keep a cab one more day"
    later = (request.end_at + timedelta(days=1)).strftime("%d %b %Y, %H:%M")
    queued: list[int] = []
    for admin in _admins(db, request, besides=asker):
        lines = [
            f"Hello {admin.full_name.split()[0] if admin.full_name else 'there'},",
            "",
            f"{asker.full_name} asked to keep their cab one more day.",
            "",
            summary,
            f"Booked until {_until(request)}; approving moves it to {later}.",
            f"Their reason: {reason}",
        ]
        if request.cab_sent_label:
            lines.append(f"Cab sent: {request.cab_sent_label}")
        lines += ["", f"Approve or reject it on Approvals: {link}"]
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=request.tenant_id,
                user=admin,
                kind="CAB_EXTENSION_REQUESTED",
                title=title[:200],
                body=f"{summary}. Until {later} if approved. Reason: {reason}",
                request_id=request.id,
                email_subject=f"Cab extension asked: {summary}"[:255],
                email_body="\n".join(lines),
                deliver_now=False,
            )
        )

    # The requester's manager hears of it in app: it is their team's booking
    # growing, but the decision is an admin's, so no email asks them to act.
    manager = request.requester.active_manager if request.requester else None
    if manager is not None and manager.id != asker.id:
        notifications.notify(
            db,
            tenant_id=request.tenant_id,
            user=manager,
            kind="CAB_EXTENSION_REQUESTED",
            title=title[:200],
            body=f"{summary}. Until {later} if an admin approves. Reason: {reason}",
            request_id=request.id,
            send_email=False,
        )
    return queued


def decide_extension(
    db: Session,
    *,
    request: TravelRequest,
    admin: User,
    approve: bool,
    comment: str | None,
    http_request=None,
) -> list[int]:
    """Approve - the cab is kept one more day, `end_at` moves with it - or
    reject, with the comment the travellers read. Everyone riding, and whoever
    asked, is told; their managers are copied."""
    _cab_only(request)
    _live(request)
    if request.cab_extension_status is not CabExtensionStatus.PENDING:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="There is no extension waiting for a decision on this cab.",
        )
    if approve and request.end_at is None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This cab has no end time to extend.",
        )

    outcome = CabExtensionStatus.APPROVED if approve else CabExtensionStatus.REJECTED
    changes: dict = {
        "cab_extension_status": {"from": str(CabExtensionStatus.PENDING), "to": str(outcome)}
    }
    if approve:
        before_end, before_days = request.end_at, request.cab_extended_days or 0
        request.end_at = before_end + timedelta(days=1)
        request.cab_extended_days = before_days + 1
        changes["end_at"] = {"from": before_end, "to": request.end_at}
        changes["cab_extended_days"] = {"from": before_days, "to": request.cab_extended_days}

    request.cab_extension_status = outcome
    request.cab_extension_decided_by_id = admin.id
    request.cab_extension_decided_at = naive_utcnow()
    request.cab_extension_comment = comment

    asker = request.cab_extension_requested_by
    whose = f"{asker.full_name}'s" if asker is not None else "the"
    verdict = "approved" if approve else "rejected"
    audit.record(
        db,
        action=AuditAction.APPROVE if approve else AuditAction.REJECT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=(
            f"{admin.full_name} {verdict} {whose} ask to extend cab request #{request.id} by a day"
            + (f"; it now runs until {_until(request)}" if approve else "")
        ),
        changes=changes,
        reason=comment,
        tenant_id=request.tenant_id,
        actor=admin,
        request=http_request,
    )
    db.flush()

    where = request.route_label(" to ")
    if approve:
        short = f"Your cab for {where} is kept one more day, until {_until(request)}."
    else:
        short = f"Your ask to keep the cab for {where} one more day was not approved."
    if comment:
        short += f" Comment: {comment}"
    told = [t.user for t in riders(request)]
    if asker is not None and all(person.id != asker.id for person in told):
        # They asked, so they hear the answer, even if they are not riding.
        told.append(asker)
    queued: list[int] = []
    for person in told:
        lines = [short]
        if approve and request.cab_sent_label:
            lines += ["", "The same cab stays with you:", *car_lines(request)]
        queued += _tell_traveller(
            db,
            request=request,
            person=person,
            kind="CAB_EXTENSION_APPROVED" if approve else "CAB_EXTENSION_REJECTED",
            title=f"Cab extension {verdict}",
            short=short,
            lines=lines,
            subject=f"Cab extension {verdict} - {where}",
            copy_title=f"{person.full_name}'s cab extension was {verdict}",
            copy_body=(
                f"{person.full_name}'s cab for {where}: one more day {verdict} "
                f"by {admin.full_name}."
                + (f" Comment: {comment}" if comment else "")
            ),
        )
    return queued
