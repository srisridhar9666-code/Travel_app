"""
The notification ledger (addendum B11, C3).

Every notice to a person is a row, and delivery is a separate recorded act
against that row. The SOW says "via email integration" and stops there; what a
fulfilment team actually needs is to be able to answer "was this person told?"
six weeks later, and the only honest way to answer that is a ledger.

Four things this module is responsible for:

* **Channels are a registry, not a branch.** `SENDERS` maps a channel to the
  function that delivers it. Adding SMS is an entry here and a value on the
  enum - that is what C3 means by channel-agnostic, and it is why there is no
  `if channel == EMAIL` anywhere below.
* **Statuses stay distinct.** `SENT`, `SUPPRESSED` (nobody tried, on purpose)
  and `FAILED` (we tried, we were refused) are three different facts. Collapsing
  them makes the ledger worthless for the one question it exists to answer.
* **Preferences are respected on the way out**, not filtered on the way in: a
  notice someone has opted out of is still written in app, because the in-app row
  *is* the record and hiding it would hide the trail from the person it is about.
* **Reminders cannot repeat.** Anything a scheduled job sends carries a
  `dedupe_key`, and the database - not this code - holds the guarantee.

Nothing here raises. A booking that could not be emailed is still a booking; the
failure belongs on the notification row, not in the caller's transaction.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    OPTIONAL_CATEGORIES,
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    category_of,
)
from app.models.base import naive_utcnow
from app.models.preference import NotificationPreference
from app.models.request import Notification
from app.models.user import User
from app.services import email

logger = logging.getLogger(__name__)

#: Beyond this many tries an address is not going to start working, and retrying
#: forever just hammers the SMTP server.
MAX_ATTEMPTS = 3


def _send_email(to_address: str, subject: str, body: str) -> email.Sent:
    """Late-bound on purpose.

    Looking `email.send` up at call time rather than storing the function object
    means a test - or a future fake transport - can replace it on the module and
    have the registry honour that. A registry holding the original reference
    would silently ignore the swap, which is exactly the kind of bug that lets a
    test suite send real mail.
    """
    return email.send(to_address, subject, body)


#: Channel -> transport. The one place to add SMS.
SENDERS: dict[NotificationChannel, Callable[[str, str, str], email.Sent]] = {
    NotificationChannel.EMAIL: _send_email,
}


def _signature() -> str:
    return "\n\n— Travel Ops\nThis is an automated message; replies are not monitored."


def wants(db: Session, user: User, category: NotificationCategory, channel: NotificationChannel) -> bool:
    """Whether this person still wants this kind of notice on this channel.

    Absence of a row means yes: a new joiner gets everything, and an opt-out is
    something someone actively did. Categories outside `OPTIONAL_CATEGORIES`
    cannot be switched off however the table is edited - being told your own
    travel was rejected is not a subscription.
    """
    if category not in OPTIONAL_CATEGORIES:
        return True

    row = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.category == category,
            NotificationPreference.channel == channel,
        )
    ).scalar_one_or_none()
    return True if row is None else row.enabled


def notify(
    db: Session,
    *,
    tenant_id: str,
    user: User,
    kind: str,
    title: str,
    body: str,
    request_id: int | None = None,
    email_subject: str | None = None,
    email_body: str | None = None,
    send_email: bool = True,
    dedupe_key: str | None = None,
) -> list[Notification]:
    """Record a notice and try to deliver it.

    The in-app row is written first and is always SENT - it exists the moment it
    is committed. The email row is written QUEUED and then attempted, so a crash
    between the two leaves evidence that delivery was owed.

    With a `dedupe_key`, a second call for the same event and person is a no-op
    and returns nothing. That is what makes the reminder jobs safe to run on a
    loop.
    """
    category = category_of(kind)

    if dedupe_key and _already_sent(db, user_id=user.id, dedupe_key=dedupe_key):
        return []

    rows: list[Notification] = []

    in_app = Notification(
        tenant_id=tenant_id,
        user_id=user.id,
        kind=kind,
        category=category,
        title=title,
        body=body,
        request_id=request_id,
        channel=NotificationChannel.IN_APP,
        status=NotificationStatus.SENT,
        sent_at=naive_utcnow(),
        dedupe_key=dedupe_key,
    )
    db.add(in_app)
    rows.append(in_app)

    wants_email = send_email and bool(user.email) and wants(
        db, user, category, NotificationChannel.EMAIL
    )
    if wants_email:
        mail = Notification(
            tenant_id=tenant_id,
            user_id=user.id,
            kind=kind,
            category=category,
            title=title,
            body=email_body or body,
            request_id=request_id,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.QUEUED,
            to_address=user.email,
            subject=(email_subject or title)[:255],
            dedupe_key=dedupe_key,
        )
        db.add(mail)
        db.flush()
        deliver(mail)
        rows.append(mail)

    db.flush()
    return rows


def _already_sent(db: Session, *, user_id: int, dedupe_key: str) -> bool:
    return (
        db.execute(
            select(Notification.id)
            .where(
                Notification.user_id == user_id,
                Notification.dedupe_key == dedupe_key,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def deliver(notification: Notification) -> Notification:
    """Attempt one row on its channel and record the outcome on it.

    Mutates the row; the caller's commit persists it. Called inline today because
    a hundred users generate a handful of messages a day. When that stops being
    true the same function runs from a worker against QUEUED rows - the ledger is
    already shaped for it.
    """
    sender = SENDERS.get(notification.channel)
    if sender is None:
        return notification   # in-app, or a channel with no transport yet

    notification.attempts += 1
    result = sender(
        notification.to_address or "",
        notification.subject or notification.title,
        f"{notification.body}{_signature()}",
    )

    if result.ok:
        notification.status = NotificationStatus.SENT
        notification.sent_at = naive_utcnow()
        notification.last_error = None
    elif result.suppressed:
        notification.status = NotificationStatus.SUPPRESSED
        notification.last_error = (result.detail or "")[:500]
    else:
        notification.status = NotificationStatus.FAILED
        notification.last_error = (result.detail or "")[:500]

    return notification


def retry_failed(db: Session, tenant_id: str, *, limit: int = 50) -> dict:
    """Re-attempt refused messages.

    Only FAILED rows under the attempt cap: SUPPRESSED was a decision, not a
    fault, and retrying it would defeat the guard that produced it.
    """
    rows = (
        db.execute(
            select(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.channel.in_(list(SENDERS)),
                Notification.status == NotificationStatus.FAILED,
                Notification.attempts < MAX_ATTEMPTS,
            )
            .order_by(Notification.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    sent = 0
    for row in rows:
        deliver(row)
        if row.status is NotificationStatus.SENT:
            sent += 1

    db.commit()
    return {"attempted": len(rows), "sent": sent, "still_failing": len(rows) - sent}


def mark_read(db: Session, *, user: User, notification_id: int | None = None) -> int:
    """Mark one in-app notice read, or all of them.

    Only in-app rows: "read" is a fact about the bell in this application, and
    nothing here can know whether an email was opened.
    """
    filters = [
        Notification.user_id == user.id,
        Notification.channel == NotificationChannel.IN_APP,
        Notification.read_at.is_(None),
    ]
    if notification_id is not None:
        filters.append(Notification.id == notification_id)

    rows = db.execute(select(Notification).where(*filters)).scalars().all()
    now = naive_utcnow()
    for row in rows:
        row.read_at = now
    db.commit()
    return len(rows)


def unread_count(db: Session, user: User) -> int:
    return len(
        db.execute(
            select(Notification.id).where(
                Notification.user_id == user.id,
                Notification.channel == NotificationChannel.IN_APP,
                Notification.read_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


def preferences_for(db: Session, user: User) -> dict[str, bool]:
    """This person's email preferences, one entry per switchable category."""
    rows = (
        db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user.id,
                NotificationPreference.channel == NotificationChannel.EMAIL,
            )
        )
        .scalars()
        .all()
    )
    stored = {str(r.category): r.enabled for r in rows}
    return {str(c): stored.get(str(c), True) for c in sorted(OPTIONAL_CATEGORIES, key=str)}


def set_preference(
    db: Session, *, user: User, category: NotificationCategory, enabled: bool
) -> None:
    """Turn one category of email on or off for one person.

    Upsert rather than insert: a preference is a single fact per person and
    category, and toggling it twice should not leave two rows disagreeing.
    """
    if category not in OPTIONAL_CATEGORIES:
        raise ValueError(f"{category} cannot be switched off")

    row = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.category == category,
            NotificationPreference.channel == NotificationChannel.EMAIL,
        )
    ).scalar_one_or_none()

    if row is None:
        row = NotificationPreference(
            user_id=user.id,
            category=category,
            channel=NotificationChannel.EMAIL,
            enabled=enabled,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Two tabs, one person, same moment. The other write is as good as
            # this one; take it and apply the value on top.
            db.rollback()
            row = db.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user.id,
                    NotificationPreference.category == category,
                    NotificationPreference.channel == NotificationChannel.EMAIL,
                )
            ).scalar_one()
            row.enabled = enabled
    else:
        row.enabled = enabled


def ledger_summary(db: Session, tenant_id: str) -> dict:
    """Counts by status, for the dashboard and for answering "did it go out?"."""
    rows = db.execute(
        select(Notification.status, Notification.channel).where(
            Notification.tenant_id == tenant_id
        )
    ).all()

    summary = {str(s): 0 for s in NotificationStatus}
    emails = 0
    for status, channel in rows:
        summary[str(status)] += 1
        if channel is not NotificationChannel.IN_APP:
            emails += 1
    return {"total": len(rows), "emails": emails, "by_status": summary}
