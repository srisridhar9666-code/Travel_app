"""
Account state: who may sign in, and the rules for changing that.

Kept out of the routers so the sign-in check (core/deps), the login endpoint
and the status endpoint all read the same messages and the same rules. Nothing
here imports core/deps, which imports this.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.enums import (
    ACCOUNT_ROLES,
    ACTIVE_TRAVELLER_STATUSES,
    ROLE_RANK,
    AuditAction,
    RequestType,
    Role,
    TravellerStatus,
    UserStatus,
)
from app.models.auth_token import AuthToken
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import audit

#: What a blocked person is told, on the sign-in screen and when a session they
#: already had is ended. Says what to do next, because "forbidden" does not.
BLOCKED_MESSAGES: dict[UserStatus, str] = {
    UserStatus.DEACTIVATED: "Your account is deactivated. Contact your admin to reactivate it.",
    UserStatus.LEFT: (
        "Your account is marked as left the organisation. "
        "Contact your admin if this is a mistake."
    ),
    UserStatus.DELETED: "This account has been removed. Contact your admin.",
}

ADMIN_TIER_MESSAGE = "Only someone at the same level or above can change this person's account."
LAST_ADMIN_MESSAGE = "At least one active system administrator or super admin is needed."


def blocked_message(user: User) -> str | None:
    """Why this person may not sign in, or None if they may.

    A row with `is_active` False but status ACTIVE is treated as deactivated:
    older code and tests set the flag directly, and a switched-off account must
    never slip through on a technicality.
    """
    if user.status is UserStatus.ACTIVE and user.is_active:
        return None
    return BLOCKED_MESSAGES.get(user.status, BLOCKED_MESSAGES[UserStatus.DEACTIVATED])


def burn_outstanding_tokens(db: Session, user: User) -> int:
    """Spend every unused invite or reset link, so nobody can set a password on
    an account that has just been switched off. Burned rather than deleted, so
    the ledger still shows they existed."""
    now = naive_utcnow()
    burned = 0
    for token in db.execute(
        select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None))
    ).scalars():
        token.used_at = now
        burned += 1
    return burned


def outranks(role: Role, other: Role) -> bool:
    return ROLE_RANK[role] > ROLE_RANK[other]


def assert_may_manage(actor: User, target: User) -> None:
    """Nobody may change the account of someone above them.

    Without this, an ordinary admin could deactivate a system admin, demote
    them, or - worst - issue a reset link for their account, which the reinvite
    endpoint hands back, and sign in as them.
    """
    if target.id != actor.id and outranks(target.role, actor.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_TIER_MESSAGE)


def assert_keeps_a_system_admin(
    db: Session,
    target: User,
    *,
    new_role: Role | None = None,
    new_status: UserStatus | None = None,
) -> None:
    """Refuse a change that would leave the organisation without an active
    system admin or super admin - nobody could then manage the top accounts.

    The other guards make this hard to reach (nobody changes their own role or
    status), so this is the backstop, not the rule people meet day to day.
    """
    role = new_role if new_role is not None else target.role
    state = new_status if new_status is not None else target.status
    if not (target.role in ACCOUNT_ROLES and target.status is UserStatus.ACTIVE):
        return  # they are not one of the active top admins now
    if role in ACCOUNT_ROLES and state is UserStatus.ACTIVE:
        return  # and still will be

    others = db.execute(
        select(func.count(User.id)).where(
            User.tenant_id == target.tenant_id,
            User.id != target.id,
            User.role.in_(ACCOUNT_ROLES),
            User.status == UserStatus.ACTIVE,
            User.is_active.is_(True),
        )
    ).scalar_one()
    if others == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN_MESSAGE)


def set_status(
    db: Session,
    *,
    user: User,
    new_status: UserStatus,
    actor: User,
    exited_on: date | None = None,
    reason: str | None = None,
    request: Request | None = None,
) -> dict:
    """Move someone to a new status and record it. Returns the change set
    (empty when nothing changed). The caller commits.

    * LEFT needs an exit date: the one given, else the one already recorded,
      else today in India. It starts the 90-day clock on their ID documents.
    * DELETED keeps any exit date and fills today's if there is none, so their
      documents are still purged on time.
    * ACTIVE clears it - they are back, and the purge must not run.
    * DEACTIVATED leaves it alone: a suspension is not a departure.
    """
    if exited_on is not None and new_status not in (UserStatus.LEFT, UserStatus.DELETED):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An exit date only goes with Left or Deleted.",
        )
    if exited_on is not None and exited_on > clock.local_today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The exit date cannot be in the future.",
        )

    before = {"status": user.status, "exited_on": user.exited_on}

    if new_status is UserStatus.ACTIVE:
        next_exit = None
    elif new_status in (UserStatus.LEFT, UserStatus.DELETED):
        next_exit = exited_on or user.exited_on or clock.local_today()
    else:
        next_exit = user.exited_on

    user.status = new_status
    user.exited_on = next_exit
    changes = audit.diff(before, {"status": user.status, "exited_on": user.exited_on})
    if not changes:
        return {}

    user.status_changed_at = naive_utcnow()
    if new_status is not UserStatus.ACTIVE:
        burn_outstanding_tokens(db, user)

    old, new = before["status"], user.status
    summary = (
        f"{actor.full_name} changed {user.full_name}'s status from "
        f"{_label(old)} to {_label(new)}"
        if old is not new
        else f"{actor.full_name} changed {user.full_name}'s exit date"
    )
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=summary,
        changes=changes,
        reason=reason,
        tenant_id=user.tenant_id,
        actor=actor,
        request=request,
    )
    return changes


def assert_phone_free(
    db: Session,
    tenant_id: str,
    phone: str | None,
    *,
    other_than: int | None = None,
    say_who: bool = True,
) -> None:
    """409 when another employee already has this mobile number.

    Numbers identify people to drivers, hotels and the travel desk, so two
    employees may not share one. Deleted accounts do not hold on to theirs.
    `say_who` names the holder - right for an admin, not for someone editing
    their own profile.
    """
    if not phone:
        return
    stmt = select(User).where(
        User.tenant_id == tenant_id,
        User.phone == phone,
        User.status != UserStatus.DELETED,
    )
    if other_than is not None:
        stmt = stmt.where(User.id != other_than)
    holder = db.execute(stmt.limit(1)).scalar_one_or_none()
    if holder is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"{phone} is already {holder.full_name}'s phone number."
            if say_who
            else "That phone number is already used by another employee."
        ),
    )


def release_team(
    db: Session, *, manager: User, actor: User, request: Request | None = None
) -> int:
    """Their team stops reporting to them - when a manager is demoted or no
    longer active. The people stay, with no manager, for an admin to reassign.
    Returns how many were released. The caller commits."""
    members = db.execute(
        select(User).where(User.tenant_id == manager.tenant_id, User.manager_id == manager.id)
    ).scalars().all()
    for member in members:
        member.manager_id = None
    if members:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=manager.id,
            summary=(
                f"{len(members)} team member(s) of {manager.full_name} no longer report to them: "
                + ", ".join(m.full_name for m in members)
            ),
            changes={"team": {"from": [m.id for m in members], "to": []}},
            tenant_id=manager.tenant_id,
            actor=actor,
            request=request,
        )
    return len(members)


def _label(value: UserStatus) -> str:
    return value.value.lower()


def _trip_end(trip: TravelRequest) -> date | None:
    """The last day, in India, that a trip touches - or None if it has no date."""
    if trip.request_type is RequestType.HOTEL:
        return trip.check_out or trip.check_in
    latest: datetime | None = trip.end_at or trip.start_at
    return clock.local_today(latest) if latest else None


def open_trips(db: Session, user: User) -> dict[str, int]:
    """Trips this person is on that are still live and not yet over.

    Shown before a status change, so the admin knows what to cancel. Nothing is
    cancelled automatically: a booked ticket may need a refund from the vendor,
    which only a person can judge.
    """
    today = clock.local_today()
    rows = db.execute(
        select(RequestTraveller.status, TravelRequest)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .where(
            RequestTraveller.user_id == user.id,
            RequestTraveller.status.in_(ACTIVE_TRAVELLER_STATUSES),
            TravelRequest.tenant_id == user.tenant_id,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
        )
    ).all()

    counts = {"pending": 0, "approved": 0, "booked": 0}
    keys = {
        TravellerStatus.PENDING: "pending",
        TravellerStatus.APPROVED: "approved",
        TravellerStatus.BOOKED: "booked",
    }
    for traveller_status, trip in rows:
        ends = _trip_end(trip)
        if ends is None or ends >= today:
            counts[keys[traveller_status]] += 1
    return {**counts, "total": sum(counts.values())}
