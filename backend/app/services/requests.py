"""
Building, editing and describing requests.

The router stays thin because two things here are easy to get wrong and must
happen identically on every path:

* **the edit window** - a request is editable only while every traveller is still
  PENDING (addendum A1), and the check has to run before anything is mutated, not
  after; and
* **the revision trail** - every accepted amendment writes one append-only row
  carrying a field-level diff, so an admin can see what changed between the
  version they read and the version in front of them.

Revisions start at submission. Revision 1 is the request as first submitted and
carries no diff; each later number is an amendment. Edits made while a request is
still a private draft do not write revisions - there is no admin to disclose them
to yet, and draft churn would bury the amendments that matter.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.enums import (
    AuditAction,
    NotificationChannel,
    NotificationStatus,
    RequestPriority,
    RequestStatus,
    RequestType,
    Role,
    RoomSharingChoice,
    TicketStatus,
    TravellerStatus,
    derive_request_status,
    is_editable,
)
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import RequestRevision, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.schemas.request import (
    EDITABLE_FIELDS,
    ConflictRead,
    CoStayMatchRead,
    RequestBody,
    RequestRead,
    RevisionRead,
    TravellerRead,
)
from app.services import audit, conflicts, costay, locations, notifications
from app.services.seed import OTHER_PROJECT_CODE

#: The most rows one queue export returns. Far above a real tab today; there so
#: a runaway export cannot hold a worker for minutes.
MAX_EXPORT_ROWS = 5000

# Human labels for the diff, so a revision reads as English rather than as column
# names. Anything not listed falls back to the column name itself.
FIELD_LABELS = {
    "project_id": "campaign",
    "other_project_name": "campaign name",
    "travel_reason": "reason for travel",
    "priority": "priority",
    "mode": "mode",
    "origin": "origin",
    "origin_state": "origin state",
    "destination_state": "destination state",
    "hotel_state": "hotel state",
    "destination": "destination",
    "pickup_city": "pickup city",
    "drop_city": "drop city",
    "start_at": "departure",
    "end_at": "arrival",
    "hotel_city": "city",
    "check_in": "check-in",
    "check_out": "check-out",
    "notes": "notes",
    "travellers": "travellers",
}


def snapshot(request: TravelRequest) -> dict[str, Any]:
    """The comparable state of a request, for the revision diff.

    Travellers are captured as a sorted list of ids so that adding or dropping
    someone shows up as a change like any other field.
    """
    state: dict[str, Any] = {field: getattr(request, field) for field in EDITABLE_FIELDS}
    state["travellers"] = sorted(t.user_id for t in request.travellers)
    return state


def next_revision_number(db: Session, request_id: int) -> int:
    highest = db.execute(
        select(func.max(RequestRevision.revision_number)).where(
            RequestRevision.request_id == request_id
        )
    ).scalar()
    return (highest or 0) + 1


def write_revision(
    db: Session,
    *,
    request: TravelRequest,
    editor: User,
    summary: str,
    changes: dict | None,
) -> RequestRevision:
    """Append one revision row. Nothing here ever updates an existing one."""
    revision = RequestRevision(
        request_id=request.id,
        revision_number=next_revision_number(db, request.id),
        editor_id=editor.id,
        summary=summary[:300],
        # Same hazard as the audit ledger: a raw date or enum in here raises at
        # INSERT and takes the edit down with it.
        changes=audit.jsonable(changes) if changes else None,
    )
    db.add(revision)
    db.flush()
    return revision


def edit_count(db: Session, request_id: int) -> int:
    """Amendments since submission. Revision 1 is the submission itself."""
    highest = db.execute(
        select(func.max(RequestRevision.revision_number)).where(
            RequestRevision.request_id == request_id
        )
    ).scalar()
    return max((highest or 0) - 1, 0)


def request_is_editable(request: TravelRequest) -> bool:
    return is_editable(
        request.traveller_statuses,
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
    )


def travel_date_passed(request: TravelRequest, *, today: date | None = None) -> bool:
    """Whether the trip itself is in the past.

    Only meaningful alongside the traveller statuses: a request nobody decided
    before the date went by is EXPIRED, one that was decided is simply history.
    """
    ends = request.travel_ends_on
    return ends is not None and ends < (today or clock.local_today())


def status_of(request: TravelRequest) -> RequestStatus:
    """The one place a request-level status is produced."""
    return derive_request_status(
        request.traveller_statuses,
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
        travel_date_passed=travel_date_passed(request),
    )


def assert_editable(request: TravelRequest) -> None:
    """Refuse an amendment once an admin has acted.

    409 rather than 403: the caller has every right to edit this request, the
    request has simply moved past the point where editing means anything.
    """
    if request_is_editable(request):
        return
    if request.is_cancelled:
        detail = "This request has been cancelled and can no longer be edited."
    else:
        detail = (
            "An admin has already acted on this request, so it is locked. "
            "Cancel it and raise a new one if the plan has changed."
        )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def resolve_project(
    db: Session, project_id: int, tenant_id: str, *, other_name: str | None = None
) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    if not project.accepts_requests:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign {project.code} is not accepting new requests.",
        )

    # Picking "Other" without saying what it is would leave the request in the
    # fallback campaign with nothing to triage it by - worse than not offering
    # the option at all.
    if project.code == OTHER_PROJECT_CODE and not (other_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name the campaign this trip is for.",
        )

    return project


def resolve_travellers(
    db: Session, *, requester: User, traveller_ids: list[int], tenant_id: str
) -> list[User]:
    """The full set of people on this request.

    `traveller_ids` names the colleagues being tagged on; the requester is always
    one of the travellers on their own request. Raising a request on someone
    else's behalf is not a Phase 3 flow.
    """
    wanted = {uid for uid in traveller_ids if uid != requester.id}
    people = [requester]

    if wanted:
        found = (
            db.execute(select(User).where(User.id.in_(wanted), User.tenant_id == tenant_id))
            .scalars()
            .all()
        )
        missing = wanted - {u.id for u in found}
        if missing:
            names = ", ".join(str(m) for m in sorted(missing))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown colleague id(s): {names}.",
            )
        # is_active follows status (deactivated, left or deleted all clear it),
        # so this one check covers every way a colleague stops being taggable.
        inactive = [u.full_name for u in found if not u.is_active]
        if inactive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{', '.join(inactive)} has left or been deactivated - "
                    "remove them from this request."
                ),
            )
        people.extend(sorted(found, key=lambda u: u.full_name))

    return people


def canonicalise_places(db: Session, tenant_id: str, body: RequestBody) -> None:
    """Settle every picked or typed place on the body to the stored spelling.

    The form lets someone type a place under "Other" when the list does not
    have it. Conflict detection and co-stay matching compare places exactly, so
    a typed "hyd" has to become the listed "Hyderabad" before anything is saved
    or checked. A cab's pickup and drop are street addresses, not places on the
    list, so those are only tidied - but the city each is in is canonicalised
    like any other.
    """
    if body.request_type is RequestType.LOCAL_CAB:
        body.origin = locations.tidy(body.origin)
        body.destination = locations.tidy(body.destination)
        pairs = (("origin_state", "pickup_city"), ("destination_state", "drop_city"))
    else:
        pairs = (
            ("origin_state", "origin"),
            ("destination_state", "destination"),
            ("hotel_state", "hotel_city"),
        )

    for state_field, place_field in pairs:
        state, place = locations.canonical(
            db, tenant_id, getattr(body, state_field), getattr(body, place_field)
        )
        setattr(body, state_field, state)
        setattr(body, place_field, place)


def apply_body(request: TravelRequest, body: RequestBody) -> None:
    """Copy the validated body onto the row.

    Fields the body has already nulled out for this type are copied as nulls,
    which is how a request that changes shape stops carrying its old route.
    """
    request.request_type = body.request_type
    for field in EDITABLE_FIELDS:
        setattr(request, field, getattr(body, field))


def sync_travellers(
    db: Session, *, request: TravelRequest, people: list[User]
) -> tuple[list[str], list[str]]:
    """Reconcile the traveller rows to `people`, returning who was added and
    dropped. Only ever reached while the request is still editable, so this
    cannot destroy a row an admin has already decided."""
    wanted = {u.id: u for u in people}
    existing = {t.user_id: t for t in request.travellers}

    added = [u.full_name for uid, u in wanted.items() if uid not in existing]
    dropped = [
        t.user.full_name if t.user else str(uid)
        for uid, t in existing.items()
        if uid not in wanted
    ]

    for uid, traveller in list(existing.items()):
        if uid not in wanted:
            request.travellers.remove(traveller)

    for uid in wanted:
        if uid not in existing:
            request.travellers.append(
                RequestTraveller(user_id=uid, status=TravellerStatus.PENDING)
            )

    db.flush()
    return added, dropped


def check_conflicts(db: Session, *, tenant_id: str, request: TravelRequest) -> list[ConflictRead]:
    """Warnings for every person on this request. Never blocks - addendum B6."""
    found = conflicts.detect(
        db,
        tenant_id=tenant_id,
        candidate=conflicts.Itinerary.from_request(request),
        user_ids=[t.user_id for t in request.travellers],
        exclude_request_id=request.id,
    )
    return [ConflictRead(**vars(c)) for c in found]


def check_costay(
    db: Session, *, tenant_id: str, request: TravelRequest, for_user: User
) -> list[CoStayMatchRead]:
    """Colleagues the requester could share a room with, already gender-filtered."""
    if request.request_type is not RequestType.HOTEL or request.check_in is None:
        return []
    matches = costay.find_matches(
        db,
        tenant_id=tenant_id,
        for_user=for_user,
        city=request.hotel_city or "",
        check_in=request.check_in,
        check_out=request.check_out,
        exclude_request_id=request.id,
    )
    return [CoStayMatchRead(**vars(m)) for m in matches]


def ticket_per_traveller(db: Session, request_id: int) -> dict[int, int]:
    """The ticket document to show beside each traveller on a request.

    The confirmed one when there is one - that is what was booked - else the
    newest still being reviewed. A discarded ticket has no file left to show.
    """
    rows = db.execute(
        select(TicketDocument.id, TicketDocument.traveller_id, TicketDocument.status)
        .where(
            TicketDocument.request_id == request_id,
            TicketDocument.status != TicketStatus.DISCARDED,
            TicketDocument.file_path.is_not(None),
        )
        .order_by(TicketDocument.id.desc())
    ).all()
    chosen: dict[int, tuple[bool, int]] = {}
    for ticket_id, traveller_id, ticket_status in rows:
        confirmed = ticket_status is TicketStatus.CONFIRMED
        held = chosen.get(traveller_id)
        if held is None or (confirmed and not held[0]):
            chosen[traveller_id] = (confirmed, ticket_id)
    return {traveller_id: ticket_id for traveller_id, (_, ticket_id) in chosen.items()}


def to_read(
    db: Session,
    request: TravelRequest,
    *,
    tenant_id: str,
    viewer: User | None = None,
    with_conflicts: bool = False,
    with_costay: bool = True,
    with_tickets: bool = False,
) -> RequestRead:
    show_cost = viewer is not None and viewer.is_admin
    # Only the admin queue asks: ticket files are admin-only to fetch, and the
    # export and single reads have no use for the extra query per request.
    tickets = ticket_per_traveller(db, request.id) if with_tickets else {}
    travellers = [
        TravellerRead(
            id=t.id,
            user_id=t.user_id,
            full_name=t.user.full_name if t.user else "",
            email=t.user.email if t.user else "",
            designation=t.user.designation if t.user else None,
            status=t.status,
            is_requester=t.user_id == request.requester_id,
            room_sharing=t.room_sharing,
            share_with_user_id=t.share_with_user_id,
            share_with_name=t.share_with.full_name if t.share_with else None,
            share_confirmed=t.share_confirmed_at is not None,
            decided_by_name=t.decided_by.full_name if t.decided_by else None,
            decided_at=t.decided_at,
            decision_reason=t.decision_reason,
            booking_reference=t.booking_reference,
            ticket_id=tickets.get(t.id),
            # Cost is admin-only. Ground staff seeing what a colleague's flight
            # cost is a personnel problem nobody asked for, and nothing in
            # section 6 needs it.
            cost_amount=t.cost_amount if show_cost else None,
            cost_currency=t.cost_currency if show_cost and t.cost_amount else None,
            cost_note=t.cost_note if show_cost else None,
            cost_entered_by_name=(
                t.cost_entered_by.full_name if show_cost and t.cost_entered_by else None
            ),
        )
        for t in request.travellers
    ]

    read = RequestRead(
        id=request.id,
        request_type=request.request_type,
        status=status_of(request),
        is_editable=request_is_editable(request),
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
        cancel_reason=request.cancel_reason,
        project_id=request.project_id,
        project_name=request.project.name if request.project else "",
        project_code=request.project.code if request.project else "",
        requester_id=request.requester_id,
        requester_name=request.requester.full_name if request.requester else "",
        mode=request.mode,
        origin=request.origin,
        destination=request.destination,
        pickup_city=request.pickup_city,
        drop_city=request.drop_city,
        start_at=request.start_at,
        end_at=request.end_at,
        hotel_city=request.hotel_city,
        check_in=request.check_in,
        check_out=request.check_out,
        travel_reason=request.travel_reason,
        priority=request.priority or RequestPriority.MEDIUM,
        origin_state=request.origin_state,
        destination_state=request.destination_state,
        hotel_state=request.hotel_state,
        other_project_name=request.other_project_name,
        notes=request.notes,
        submitted_at=request.submitted_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
        travellers=travellers,
        edit_count=edit_count(db, request.id),
        is_decided=bool(request.travellers)
        and all(t.status is not TravellerStatus.PENDING for t in request.travellers),
    )

    if with_conflicts:
        read.conflicts = check_conflicts(db, tenant_id=tenant_id, request=request)
        # Co-stay matches are about the viewer, so an export (one admin, many
        # requests) skips them rather than run the matcher once per row.
        if with_costay and viewer is not None:
            read.costay_matches = check_costay(
                db, tenant_id=tenant_id, request=request, for_user=viewer
            )
    return read


def revisions_of(db: Session, request_id: int) -> list[RevisionRead]:
    rows = (
        db.execute(
            select(RequestRevision)
            .where(RequestRevision.request_id == request_id)
            .order_by(RequestRevision.revision_number.desc())
        )
        .scalars()
        .all()
    )
    return [
        RevisionRead(
            revision_number=r.revision_number,
            editor_name=r.editor.full_name if r.editor else None,
            created_at=r.created_at,
            summary=r.summary,
            changes=r.changes,
        )
        for r in rows
    ]


def describe_changes(changes: dict) -> str:
    """What the revision row says it touched, in words rather than column names."""
    labels = [FIELD_LABELS.get(key, key) for key in sorted(changes)]
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{', '.join(labels[:3])} and {len(labels) - 3} more"


def clear_stale_shares(request: TravelRequest, changes: dict) -> None:
    """Drop room shares when the stay itself moves.

    An admin confirmed a share against particular dates in a particular city.
    Carrying that confirmation silently onto different dates would put two people
    in a room neither of them agreed to.
    """
    if not {"hotel_city", "check_in", "check_out"} & changes.keys():
        return
    for traveller in request.travellers:
        if traveller.room_sharing is not RoomSharingChoice.NOT_OFFERED:
            costay.clear_share(traveller)


def record_submission(
    db: Session, *, request: TravelRequest, actor: User, tenant_id: str, http_request=None
) -> list[int]:
    """Mark a request submitted, open its revision trail at 1, and tell the admins.

    Returns the ids of the admin emails it queued, for the caller to send once
    the response is on its way (`notifications.deliver_queued`).
    """
    request.is_draft = False
    request.submitted_at = naive_utcnow()
    db.flush()

    write_revision(db, request=request, editor=actor, summary="Raised", changes=None)
    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=(
            f"{actor.full_name} submitted a {request.request_type} request "
            f"for {len(request.travellers)} traveller(s)"
        ),
        changes=snapshot(request),
        tenant_id=tenant_id,
        actor=actor,
        request=http_request,
    )
    return notify_admins_of_submission(db, request=request, actor=actor, tenant_id=tenant_id)


def trip_summary(request: TravelRequest) -> str:
    """One line a person can act on: what, where, when."""
    if request.request_type is RequestType.HOTEL:
        where = ", ".join(p for p in (request.hotel_city, request.hotel_state) if p)
        when = request.check_in.strftime("%d %b %Y") if request.check_in else "dates to be set"
        if request.check_out:
            when += f" to {request.check_out.strftime('%d %b %Y')}"
        return f"Hotel in {where}, {when}"
    kind = "Cab" if request.request_type is RequestType.LOCAL_CAB else str(request.mode or "Travel").title()
    when = request.start_at.strftime("%d %b %Y, %H:%M") if request.start_at else "time to be set"
    return f"{kind}: {request.route_label(' to ')}, {when}"


def notify_admins_of_submission(
    db: Session, *, request: TravelRequest, actor: User, tenant_id: str
) -> list[int]:
    """Tell every admin a request is waiting for a decision.

    In app always; by email unless the admin has switched "New requests" off.
    The admin who raised it is not told about their own request.
    """
    admins = (
        db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role.in_((Role.ADMIN, Role.SYSTEM_ADMIN)),
                User.is_active.is_(True),
                User.id != actor.id,
            )
        )
        .scalars()
        .all()
    )
    if not admins:
        return []

    summary = trip_summary(request)
    names = ", ".join(t.user.full_name for t in request.travellers if t.user) or actor.full_name
    campaign = f"{request.project.code} - {request.project.name}" if request.project else None
    link = f"{get_settings().frontend_base_url.rstrip('/')}/approvals"
    priority = request.priority or RequestPriority.MEDIUM
    label = priority.value.title()
    # Only HIGH changes the subject and title: urgent mail should stand out in
    # an inbox, and marking every request would make the marker meaningless.
    urgent = priority is RequestPriority.HIGH

    queued: list[int] = []
    for admin in admins:
        greeting = admin.full_name.split()[0] if admin.full_name else "there"
        lines = [
            f"Hello {greeting},",
            "",
            f"{actor.full_name} raised a travel request that needs a decision.",
            "",
            summary,
            f"Travellers: {names}",
        ]
        if campaign:
            lines.append(f"Campaign: {campaign}")
        if request.travel_reason:
            lines.append(f"Reason: {request.travel_reason}")
        lines.append(f"Priority: {label}")
        lines += ["", f"Review it: {link}"]

        rows = notifications.notify(
            db,
            tenant_id=tenant_id,
            user=admin,
            kind="REQUEST_SUBMITTED",
            title=(
                f"High-priority request from {actor.full_name}"
                if urgent
                else f"New request from {actor.full_name}"
            ),
            body=f"{summary} - for {names}. Priority: {label}.",
            request_id=request.id,
            email_subject=(
                ("High priority - " if urgent else "") + f"New travel request: {summary}"
            )[:255],
            email_body="\n".join(lines),
            deliver_now=False,
        )
        queued += [
            r.id for r in rows
            if r.channel == NotificationChannel.EMAIL and r.status == NotificationStatus.QUEUED
        ]
    return queued
