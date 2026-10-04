"""
A manager's recommendation on their team's trips: the first of two levels.

The product owner's rule is that a traveller's manager gives a view, with a
comment, and an admin then makes the final decision, with a comment of their
own, seeing what the manager said. Three things follow from that and are held
here rather than in the router:

* **Advice, never a gate.** Nothing in `decisions.apply` waits for a
  recommendation. An admin may decide before the manager answers, or against
  them; the queue shows which, so it is done knowingly. A manager on leave must
  not be able to stall someone's travel.
* **One manager, one level, looked up live.** The traveller's manager is
  whoever they report to now (`User.active_manager`), not whoever they reported
  to when the request was raised - the person answerable today is the one asked.
* **Changeable until decided.** While the traveller is still pending the
  manager may change their mind; each version is a RECOMMEND row in the
  activity log, so the ledger shows the history the traveller row does not.
"""
from __future__ import annotations

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import (
    ADMIN_ROLES,
    AuditAction,
    ManagerRecommendation,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import audit, notifications
from app.services.requests import queued_emails, trip_summary

LABELS = {
    ManagerRecommendation.RECOMMENDED: "Recommended",
    ManagerRecommendation.NOT_RECOMMENDED: "Not recommended",
}


def describe(traveller: RequestTraveller) -> str | None:
    """"Recommended - needed for the Pune audit", or None if nothing was said."""
    if traveller.manager_recommendation is None:
        return None
    label = LABELS[traveller.manager_recommendation]
    return f"{label} - {traveller.manager_comment}" if traveller.manager_comment else label


def _reports_to(traveller: RequestTraveller, manager: User) -> bool:
    return traveller.user is not None and traveller.user.manager_id == manager.id


def _answers_for(traveller: RequestTraveller, reader: User) -> bool:
    """Whose recommendations this reader works with: every traveller's, for an
    admin; their own team's, for a manager."""
    return traveller.user is not None and (reader.is_admin or _reports_to(traveller, reader))


def waits_on(request: TravelRequest, reader: User) -> bool:
    """Someone on this request is still waiting for their manager's view - for
    a manager, someone on their own team."""
    return any(t.awaits_manager and _answers_for(t, reader) for t in request.travellers)


def reviewed_by(request: TravelRequest, reader: User) -> bool:
    """A manager has already given their view on someone here - for a
    manager, given it themself."""
    return any(
        t.manager_recommendation is not None
        and (reader.is_admin or t.manager_reviewed_by_id == reader.id)
        for t in request.travellers
    )


def _choose(
    request: TravelRequest, manager: User, traveller_ids: list[int] | None
) -> list[RequestTraveller]:
    """The travellers this recommendation covers, or a refusal that says why not."""
    if request.is_draft:
        # Unreachable through _load, which hides other people's drafts, but a
        # manager's own draft is not in front of an admin yet either.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has not been submitted yet.",
        )
    if request.is_cancelled:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has been cancelled, so there is nothing to recommend.",
        )

    if traveller_ids is None:
        chosen = [
            t for t in request.travellers
            if _reports_to(t, manager) and t.status is TravellerStatus.PENDING
        ]
        if not chosen:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    "Nobody from your team on this request is still waiting for a "
                    "decision. An admin has already decided."
                ),
            )
        return chosen

    if not traveller_ids:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose at least one of your team members on this request.",
        )
    by_id = {t.id: t for t in request.travellers}
    wanted = set(traveller_ids)
    for traveller_id in wanted:
        traveller = by_id.get(traveller_id)
        if traveller is None or not _reports_to(traveller, manager):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="You can only recommend your own team members on this request.",
            )
        if traveller.status is not TravellerStatus.PENDING:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    f"An admin has already decided {traveller.user.full_name}, so a "
                    "recommendation would change nothing."
                ),
            )
    # In the request's own order, so notices and the log list people the way
    # every screen does.
    return [t for t in request.travellers if t.id in wanted]


def record(
    db: Session,
    *,
    request: TravelRequest,
    manager: User,
    recommendation: ManagerRecommendation,
    comment: str,
    traveller_ids: list[int] | None = None,
    http_request=None,
) -> list[int]:
    """Save a manager's view on their team members' part of a request, log it,
    and tell the admins.

    The caller owns the commit. Returns the ids of the admin emails queued, for
    the caller to send once the response is on its way.
    """
    chosen = _choose(request, manager, traveller_ids)
    verb = "recommended" if recommendation is ManagerRecommendation.RECOMMENDED else "did not recommend"

    for traveller in chosen:
        before = traveller.manager_recommendation
        changes = {
            "recommendation": {
                "from": str(before) if before else None,
                "to": str(recommendation),
            },
        }
        if before is not None and traveller.manager_comment != comment:
            changes["comment"] = {"from": traveller.manager_comment, "to": comment}

        traveller.manager_recommendation = recommendation
        traveller.manager_comment = comment
        traveller.manager_reviewed_by_id = manager.id
        traveller.manager_reviewed_at = naive_utcnow()

        audit.record(
            db,
            action=AuditAction.RECOMMEND,
            entity_type="request_traveller",
            entity_id=traveller.id,
            summary=(
                f"{manager.full_name} {verb} {traveller.user.full_name} on request "
                f"#{request.id}" + (" (changed their recommendation)" if before else "")
            ),
            changes=changes,
            reason=comment,
            tenant_id=request.tenant_id,
            actor=manager,
            request=http_request,
        )

    db.flush()
    return _tell_admins(
        db, request=request, manager=manager, travellers=chosen,
        recommendation=recommendation, comment=comment,
    )


def _tell_admins(
    db: Session,
    *,
    request: TravelRequest,
    manager: User,
    travellers: list[RequestTraveller],
    recommendation: ManagerRecommendation,
    comment: str,
) -> list[int]:
    """Every active admin hears the manager's view, comment included.

    In app always; by email unless they switched "New requests" off - the same
    switch as a new submission, because both say "a request is ready for you".
    """
    admins = (
        db.execute(
            select(User).where(
                User.tenant_id == request.tenant_id,
                User.role.in_(ADMIN_ROLES),
                User.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    if not admins:
        return []

    label = LABELS[recommendation]
    names = ", ".join(t.user.full_name for t in travellers)
    summary = trip_summary(request)
    link = f"{get_settings().frontend_base_url.rstrip('/')}/approvals"
    title = f"{manager.full_name}: {label.lower()} for {names}"

    queued: list[int] = []
    for admin in admins:
        lines = [
            f"Hello {admin.full_name.split()[0] if admin.full_name else 'there'},",
            "",
            f"{manager.full_name} gave their recommendation on a request for {names}.",
            "",
            summary,
            f"Manager's recommendation: {label}",
            f"Their comment: {comment}",
            "",
            "The final decision is yours.",
            f"Decide it on Approvals: {link}",
        ]
        rows = notifications.notify(
            db,
            tenant_id=request.tenant_id,
            user=admin,
            kind="MANAGER_RECOMMENDED",
            title=title[:200],
            body=f"{summary}. {label} - {comment}",
            request_id=request.id,
            email_subject=f"{label} by {manager.full_name}: {summary}"[:255],
            email_body="\n".join(lines),
            deliver_now=False,
        )
        queued += queued_emails(rows)
    return queued
