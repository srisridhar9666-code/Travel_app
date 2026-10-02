"""
Travel, cab and hotel requests (SOW section 3).

Ground staff raise and amend; admins read everything and confirm room shares.
Approval itself is Phase 4 and deliberately absent here.

Two things shape this module:

* **Drafts are private.** A draft is visible to its owner and to nobody else -
  not to an admin, not to a co-traveller tagged on it. It also never occupies
  anyone's calendar, so it cannot cause a conflict warning for someone who has
  not been told it exists.
* **Conflicts warn.** Every write path returns its warnings in the response body
  rather than rejecting the write (addendum B6). The only place a conflict can
  stop anything is Phase 4, where an admin must type a reason to approve over
  one.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import (
    AuditAction,
    RequestStatus,
    RequestType,
    RoomSharingChoice,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import (
    BatchDecisionPayload,
    CancelPayload,
    ColleagueRead,
    DecisionPayload,
    ConflictCheckRequest,
    ConflictCheckResponse,
    QueueCounts,
    RequestCreate,
    RequestEdit,
    RequestListResponse,
    RequestRead,
    RevisionRead,
    RoomSharingChoicePayload,
)
from app.services import audit, costay, decisions, notifications
from app.services import requests as svc

router = APIRouter(prefix="/requests", tags=["requests"])


def _load(db: Session, request_id: int, user: User) -> TravelRequest:
    """Fetch a request the caller is allowed to see.

    Everything the caller may not see is a 404 rather than a 403 - a 403 would
    confirm that someone else's draft exists.
    """
    row = db.get(TravelRequest, request_id)
    if row is None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

    if row.is_draft and row.requester_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

    on_it = row.requester_id == user.id or any(t.user_id == user.id for t in row.travellers)
    if not on_it and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return row


def _assert_owner(row: TravelRequest, user: User) -> None:
    """Only the person who raised a request may amend it.

    An admin who disagrees with a request rejects it; they do not rewrite it
    under the requester's name.
    """
    if row.requester_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the person who raised this request can change it.",
        )


# ---------------------------------------------------------------------------
# Literal paths first - a /{request_id} route declared above these would match
# "check" as an int and 422.
# ---------------------------------------------------------------------------


@router.post("/check", response_model=ConflictCheckResponse)
def check(payload: ConflictCheckRequest, user: CurrentUser, db: DbSession) -> ConflictCheckResponse:
    """Dry-run the conflict and co-stay checks for a request being typed.

    Saves nothing. The form calls this as dates change so the warning appears
    while there is still time to act on it, and the same checks run again on the
    real write - this endpoint is a convenience, never the enforcement point.
    """
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )
    probe = TravelRequest(
        tenant_id=user.tenant_id,
        requester_id=user.id,
        project_id=payload.project_id,
        request_type=payload.request_type,
    )
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(probe, payload)
    probe.id = payload.request_id  # so an edit does not conflict with itself
    probe.travellers = [RequestTraveller(user_id=p.id) for p in people]

    return ConflictCheckResponse(
        conflicts=svc.check_conflicts(db, tenant_id=user.tenant_id, request=probe),
        costay_matches=svc.check_costay(
            db, tenant_id=user.tenant_id, request=probe, for_user=user
        ),
    )


@router.get("", response_model=RequestListResponse)
def list_requests(
    user: CurrentUser,
    db: DbSession,
    mine: Annotated[bool, Query(description="Only requests this person is on")] = True,
    request_status: Annotated[RequestStatus | None, Query(alias="status")] = None,
    request_type: Annotated[RequestType | None, Query(alias="type")] = None,
    project_id: Annotated[int | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> RequestListResponse:
    """The caller's requests, newest first.

    Ground staff always see only what they are on, whatever `mine` says. Admins
    can widen to the whole tenant - minus everyone else's drafts.
    """
    filters = [TravelRequest.tenant_id == user.tenant_id]

    restrict_to_self = mine or not user.is_admin
    if restrict_to_self:
        on_request = select(RequestTraveller.request_id).where(RequestTraveller.user_id == user.id)
        filters.append(
            or_(TravelRequest.requester_id == user.id, TravelRequest.id.in_(on_request))
        )

    # Someone else's draft does not exist as far as this list is concerned.
    filters.append(
        or_(TravelRequest.is_draft.is_(False), TravelRequest.requester_id == user.id)
    )

    if request_type is not None:
        filters.append(TravelRequest.request_type == request_type)
    if project_id is not None:
        filters.append(TravelRequest.project_id == project_id)
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(
                TravelRequest.origin.like(like),
                TravelRequest.destination.like(like),
                TravelRequest.pickup_city.like(like),
                TravelRequest.drop_city.like(like),
                TravelRequest.hotel_city.like(like),
                TravelRequest.notes.like(like),
            )
        )

    rows = (
        db.execute(select(TravelRequest).where(*filters).order_by(TravelRequest.id.desc()))
        .scalars()
        .unique()
        .all()
    )

    # Status is derived from the traveller rows, so it cannot be filtered in SQL
    # without duplicating the derivation in two places. At roughly a hundred
    # staff the list is small enough that filtering in Python is honest and
    # cheap; if it ever is not, the fix is a materialised column maintained by
    # the same function, not a second copy of the rule.
    if request_status is not None:
        rows = [r for r in rows if svc.status_of(r) is request_status]

    total = len(rows)
    window = rows[(page - 1) * page_size : page * page_size]

    # Conflicts are computed for the page being returned, not the whole result
    # set, so a warning is visible at a glance rather than only after opening a
    # request. Co-stay matches are left out: they are only actionable on one
    # request at a time, and GET /requests/{id} carries them.
    return RequestListResponse(
        items=[
            svc.to_read(db, r, tenant_id=user.tenant_id, with_conflicts=True) for r in window
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _read_and_release(db: Session, row: TravelRequest, user: User) -> RequestRead:
    """The response, built before the request's session is let go.

    For the endpoints that queue emails to send after the response: FastAPI
    closes `get_db`'s session only after background tasks finish, so otherwise
    this connection would stay checked out for as long as those sends take -
    a mail server timeout per admin, when it cannot be reached.
    """
    result = svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)
    db.close()
    return result


@router.post("", response_model=RequestRead, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Raise a request, as a draft or straight into the queue.

    Conflicts are returned alongside the created request rather than preventing
    it: the requester may know something the calendar does not.
    """
    svc.resolve_project(
        db, payload.project_id, user.tenant_id, other_name=payload.other_project_name
    )
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )

    row = TravelRequest(
        tenant_id=user.tenant_id,
        requester_id=user.id,
        request_type=payload.request_type,
        project_id=payload.project_id,
        is_draft=payload.is_draft,
    )
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(row, payload)
    row.travellers = [
        RequestTraveller(user_id=p.id, status=TravellerStatus.PENDING) for p in people
    ]
    db.add(row)
    db.flush()

    if payload.is_draft:
        audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="travel_request",
            entity_id=row.id,
            summary=f"{user.full_name} started a draft {row.request_type} request",
            tenant_id=user.tenant_id,
            actor=user,
            request=http_request,
        )
    else:
        queued = svc.record_submission(
            db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
        )
        background.add_task(notifications.deliver_queued, queued)

    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.get("/queue/counts", response_model=QueueCounts)
def queue_counts(actor: AdminUser, db: DbSession) -> QueueCounts:
    """Headline numbers for the admin queue tabs.

    Counted over the whole tenant rather than the current page, because the tab
    labels have to be true regardless of what is being looked at. Drafts are
    excluded: they are not in the queue and their owners have not asked for them
    to be.
    """
    rows = (
        db.execute(
            select(TravelRequest).where(
                TravelRequest.tenant_id == actor.tenant_id,
                TravelRequest.is_draft.is_(False),
            )
        )
        .scalars()
        .unique()
        .all()
    )

    tally = {s: 0 for s in RequestStatus}
    conflicted = 0
    edited = 0
    for row in rows:
        tally[svc.status_of(row)] += 1
        if svc.edit_count(db, row.id) > 0:
            edited += 1
        # Only requests still awaiting a decision are worth flagging as clashing:
        # a booked trip's clash is history, not a thing to act on.
        if any(t.status is TravellerStatus.PENDING for t in row.travellers) and svc.check_conflicts(
            db, tenant_id=actor.tenant_id, request=row
        ):
            conflicted += 1

    return QueueCounts(
        awaiting=tally[RequestStatus.SUBMITTED],
        partially_approved=tally[RequestStatus.PARTIALLY_APPROVED],
        approved=tally[RequestStatus.APPROVED],
        booked=tally[RequestStatus.BOOKED],
        rejected=tally[RequestStatus.REJECTED],
        cancelled=tally[RequestStatus.CANCELLED],
        expired=tally[RequestStatus.EXPIRED],
        with_conflicts=conflicted,
        edited=edited,
    )


@router.get("/colleagues", response_model=list[ColleagueRead])
def colleagues(user: CurrentUser, db: DbSession) -> list[ColleagueRead]:
    """Who this person can tag onto a request.

    A separate endpoint rather than /users, which is admin-only and returns the
    whole employee record. Tagging a colleague needs a name; it does not need
    their email, phone or account state.
    """
    rows = (
        db.execute(
            select(User)
            .where(
                User.tenant_id == user.tenant_id,
                User.is_active.is_(True),
                User.id != user.id,
            )
            .order_by(User.full_name)
        )
        .scalars()
        .all()
    )
    return [
        ColleagueRead(id=u.id, full_name=u.full_name, designation=u.designation) for u in rows
    ]


# ---------------------------------------------------------------------------
# Per-request routes
# ---------------------------------------------------------------------------


@router.get("/{request_id}", response_model=RequestRead)
def get_request(request_id: int, user: CurrentUser, db: DbSession) -> RequestRead:
    row = _load(db, request_id, user)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)


@router.put("/{request_id}", response_model=RequestRead)
def edit_request(
    request_id: int,
    payload: RequestEdit,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Amend a request while it is still unlocked, recording what changed.

    The body is a full replacement rather than a patch, because a revision has to
    say what the request *was* and what it *became* - and a partial body leaves
    that ambiguous for any field it omits.
    """
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    svc.assert_editable(row)

    svc.resolve_project(
        db, payload.project_id, user.tenant_id, other_name=payload.other_project_name
    )
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )

    was_draft = row.is_draft
    before = svc.snapshot(row)
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(row, payload)
    svc.sync_travellers(db, request=row, people=people)
    after = svc.snapshot(row)

    changes = audit.diff(before, after)
    if changes:
        svc.clear_stale_shares(row, changes)

    if was_draft and not payload.is_draft:
        # Leaving draft is a submission, not an amendment: revision 1 is the
        # version the admin queue first sees, whatever churn preceded it.
        queued = svc.record_submission(
            db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
        )
        background.add_task(notifications.deliver_queued, queued)
    elif changes and not row.is_draft:
        svc.write_revision(
            db,
            request=row,
            editor=user,
            summary=f"Edited {svc.describe_changes(changes)}",
            changes=changes,
        )
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="travel_request",
            entity_id=row.id,
            summary=f"{user.full_name} edited request #{row.id} ({svc.describe_changes(changes)})",
            changes=changes,
            tenant_id=user.tenant_id,
            actor=user,
            request=http_request,
        )

    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.post("/{request_id}/submit", response_model=RequestRead)
def submit_request(
    request_id: int,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Move a draft into the admin queue."""
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    if not row.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This request has already been submitted."
        )

    queued = svc.record_submission(
        db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
    )
    background.add_task(notifications.deliver_queued, queued)
    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.post("/{request_id}/cancel", response_model=RequestRead)
def cancel_request(
    request_id: int,
    payload: CancelPayload,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Withdraw a request, with a reason (addendum B4).

    The requester or an admin may cancel, and a locked request can still be
    cancelled - that is the escape hatch the edit window leaves open, since
    cancel-and-reraise is how a booked plan changes in V1.
    """
    row = _load(db, request_id, user)
    if row.requester_id != user.id and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You cannot cancel this request."
        )
    if row.is_cancelled:
        return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user)

    row.is_cancelled = True
    row.cancel_reason = payload.reason
    row.cancelled_by_id = user.id
    for traveller in row.travellers:
        if traveller.status is not TravellerStatus.REJECTED:
            traveller.status = TravellerStatus.CANCELLED

    audit.record(
        db,
        action=AuditAction.CANCEL,
        entity_type="travel_request",
        entity_id=row.id,
        summary=f"{user.full_name} cancelled request #{row.id}",
        reason=payload.reason,
        tenant_id=user.tenant_id,
        actor=user,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user)


@router.get("/{request_id}/revisions", response_model=list[RevisionRead])
def request_revisions(request_id: int, user: CurrentUser, db: DbSession) -> list[RevisionRead]:
    """The full edit history, newest first. This is what the admin queue expands
    behind "edited N times"."""
    row = _load(db, request_id, user)
    return svc.revisions_of(db, row.id)


@router.post("/{request_id}/room-sharing", response_model=RequestRead)
def set_room_sharing(
    request_id: int,
    payload: RoomSharingChoicePayload,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Record what the requester chose when offered a co-stay (SOW section 3).

    Choosing to share is a *request*, not a booking: it stays unconfirmed until
    an admin signs it off, and the colleague is told straight away. That is the
    C2 assumption - see `services/costay.py`.
    """
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    svc.assert_editable(row)

    if row.request_type is not RequestType.HOTEL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Room sharing only applies to a hotel request.",
        )

    traveller = next((t for t in row.travellers if t.id == payload.traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )

    if payload.choice is RoomSharingChoice.SHARE_EXISTING:
        colleague = db.get(User, payload.share_with_user_id)
        if colleague is None or colleague.tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="That colleague was not found."
            )
        # The gender policy is enforced here as well as in the matcher: the
        # matcher decides what to *offer*, this decides what may be *saved*, and
        # a hand-rolled POST must not slip past the first one.
        if not costay.may_share_room(traveller.user.gender, colleague.gender):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="These two travellers cannot be offered a shared room.",
            )

    before = str(traveller.room_sharing)
    costay.clear_share(traveller)
    traveller.room_sharing = payload.choice
    traveller.share_with_user_id = payload.share_with_user_id

    if payload.choice is RoomSharingChoice.SHARE_EXISTING:
        costay.notify_share_request(
            db,
            tenant_id=user.tenant_id,
            colleague_id=payload.share_with_user_id,
            requester=user,
            request=row,
        )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=(
            f"{user.full_name} set room sharing to {payload.choice} "
            f"for {traveller.user.full_name} on request #{row.id}"
        ),
        changes={"room_sharing": {"from": before, "to": str(payload.choice)}},
        tenant_id=user.tenant_id,
        actor=user,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)


@router.post("/{request_id}/travellers/{traveller_id}/confirm-share", response_model=RequestRead)
def confirm_share(
    request_id: int,
    traveller_id: int,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """An admin signs off a shared room (C2).

    Nothing books two people into one room until this happens. If the client
    decides the colleague must consent as well, their acceptance becomes a second
    precondition here rather than a new flow.
    """
    row = _load(db, request_id, actor)
    traveller = next((t for t in row.travellers if t.id == traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )
    if traveller.room_sharing is not RoomSharingChoice.SHARE_EXISTING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This traveller has not asked to share a room.",
        )

    traveller.share_confirmed_by_id = actor.id
    traveller.share_confirmed_at = naive_utcnow()

    audit.record(
        db,
        action=AuditAction.APPROVE,
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=(
            f"{actor.full_name} confirmed a shared room for {traveller.user.full_name} "
            f"with {traveller.share_with.full_name if traveller.share_with else 'a colleague'}"
        ),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


# ---------------------------------------------------------------------------
# Admin decisions (SOW section 4). The decision unit is one traveller.
# ---------------------------------------------------------------------------


def _traveller_or_404(row: TravelRequest, traveller_id: int) -> RequestTraveller:
    traveller = next((t for t in row.travellers if t.id == traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )
    return traveller


def _decidable(row: TravelRequest) -> None:
    if row.is_draft:
        # Unreachable through _load, which hides other people's drafts, but an
        # admin deciding their own draft would approve something never submitted.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has not been submitted yet.",
        )
    if row.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has been cancelled.",
        )


@router.post("/{request_id}/travellers/{traveller_id}/decide", response_model=RequestRead)
def decide_traveller(
    request_id: int,
    traveller_id: int,
    payload: DecisionPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Approve, reject, book or cancel one person on a request.

    Deciding one traveller locks the request against further edits by the
    requester - that is addendum A1, and it happens as a consequence of the
    status change rather than as a separate flag.
    """
    row = _load(db, request_id, actor)
    _decidable(row)
    traveller = _traveller_or_404(row, traveller_id)

    decisions.apply(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=row,
        traveller=traveller,
        decision=decisions.Decision(
            traveller_id=traveller.id,
            to_status=payload.to_status,
            reason=payload.reason,
            booking_reference=payload.booking_reference,
            conflict_override_reason=payload.conflict_override_reason,
        ),
        http_request=http_request,
        notify=payload.notify_employee,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor, with_conflicts=True)


@router.post("/{request_id}/decide", response_model=RequestRead)
def decide_batch(
    request_id: int,
    payload: BatchDecisionPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Decide several travellers on one request in a single transaction.

    This is what partial approval looks like from the queue: tick three people,
    reject the fourth, press once. One failure rolls the whole set back, so the
    queue can never show a half-applied decision - and the ledger never records
    one either.
    """
    row = _load(db, request_id, actor)
    _decidable(row)

    seen: set[int] = set()
    for item in payload.decisions:
        if item.traveller_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The same traveller appears twice in this batch.",
            )
        seen.add(item.traveller_id)

        traveller = _traveller_or_404(row, item.traveller_id)
        decisions.apply(
            db,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=row,
            traveller=traveller,
            decision=decisions.Decision(
                traveller_id=traveller.id,
                to_status=item.to_status,
                reason=item.reason,
                booking_reference=item.booking_reference,
                conflict_override_reason=item.conflict_override_reason,
            ),
            http_request=http_request,
            notify=item.notify_employee,
        )

    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor, with_conflicts=True)
