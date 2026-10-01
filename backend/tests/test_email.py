"""
The guards between this code and a real inbox.

These are worth testing precisely because the failure is invisible in
development and expensive in production: the smoke scripts invent addresses at
`@designboxed.com`, which is a real domain, and mail to a hundred of them would
bounce off one Gmail account and take its sending reputation with it.

The distinction the ledger has to keep is SUPPRESSED versus FAILED. Suppressed
means nobody tried, on purpose. Failed means we tried and were refused. Treating
them the same would make "did this person get told?" unanswerable.
"""
import pytest
from sqlalchemy import create_engine

from app.config import Settings, get_settings
from app.core.enums import NotificationChannel, NotificationStatus, Role
from app.models.request import Notification
from app.models.user import User
from app.services import email, notifications

TENANT = "designboxed"


@pytest.fixture
def person(db):
    user = User(
        tenant_id=TENANT,
        email="ravi@designboxed.com",
        full_name="Ravi Kumar",
        role=Role.GROUND_STAFF,
        password_hash="x",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def real_transport(monkeypatch):
    """Step out of the session-wide outbox to test the guards themselves.

    Nothing here reaches a socket: every case is stopped by a guard before
    smtplib is touched, which is the point being tested.
    """
    monkeypatch.setattr(email, "_outbox", None)
    return monkeypatch


def configure(monkeypatch, **overrides):
    """Swap in a settings object for the module under test.

    `Settings()` still reads the developer's .env for anything not named here,
    so every field these tests depend on is passed explicitly - otherwise a real
    EMAIL_ALLOWLIST leaks in and the case under test is not the case running.
    """
    defaults = {
        "email_enabled": False,
        "email_allowlist": "",
        "smtp_username": "",
        "smtp_app_password": "",
        "email_from": "",
    }
    settings = Settings(**{**defaults, **overrides})
    monkeypatch.setattr(email, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_delivery_is_off_by_default(real_transport, monkeypatch):
    """A fresh checkout, a test run and CI must be incapable of sending."""
    configure(monkeypatch, email_enabled=False)
    result = email.send("someone@example.com", "Subject", "Body")
    assert not result.ok
    assert result.suppressed
    assert "switched off" in result.detail


def test_an_address_outside_the_allowlist_is_suppressed(real_transport, monkeypatch):
    configure(
        monkeypatch,
        email_enabled=True,
        smtp_username="ops@example.com",
        smtp_app_password="secret",
        email_from="ops@example.com",
        email_allowlist="me@example.com",
    )
    result = email.send("p5.ravi.a1b2c3@designboxed.com", "Subject", "Body")
    assert not result.ok
    assert result.suppressed
    assert "EMAIL_ALLOWLIST" in result.detail


def test_the_allowlist_ignores_case_and_padding(real_transport, monkeypatch):
    settings = configure(
        monkeypatch,
        email_enabled=True,
        email_allowlist="  Me@Example.com , other@example.com ",
    )
    assert settings.allowed_email_recipients == {"me@example.com", "other@example.com"}
    assert email._may_send_to("ME@EXAMPLE.COM") is True
    assert email._may_send_to("stranger@example.com") is False


def test_an_empty_allowlist_means_no_restriction(real_transport, monkeypatch):
    """Which is what production wants - it is the enabled switch that gates it."""
    configure(monkeypatch, email_enabled=True, email_allowlist="")
    assert email._may_send_to("anyone@example.com") is True


def test_enabled_but_unconfigured_is_a_failure_not_a_suppression(
    real_transport, monkeypatch
):
    """Someone turned delivery on and forgot the credentials. That is a fault to
    surface, not a decision to respect."""
    configure(monkeypatch, email_enabled=True, smtp_username="", smtp_app_password="")
    result = email.send("me@example.com", "Subject", "Body")
    assert not result.ok
    assert not result.suppressed
    assert "not configured" in result.detail


def test_a_missing_address_never_reaches_the_transport(real_transport, monkeypatch):
    configure(monkeypatch, email_enabled=True, email_allowlist="")
    for address in ("", "not-an-address"):
        result = email.send(address, "Subject", "Body")
        assert not result.ok
        assert "no usable address" in result.detail


def test_the_outbox_captures_instead_of_sending():
    with email.use_outbox() as outbox:
        assert email.send("anyone@example.com", "Hello", "Body").ok
        assert len(outbox.messages) == 1
        assert outbox.messages[0]["to"] == "anyone@example.com"


def test_the_built_message_carries_a_named_sender(monkeypatch):
    configure(
        monkeypatch,
        email_from="ops@example.com",
        email_from_name="Travel Ops",
    )
    message = email.build("ravi@example.com", "Booked", "Body")
    assert message["To"] == "ravi@example.com"
    assert message["Subject"] == "Booked"
    assert message["From"] == "Travel Ops <ops@example.com>"


# ---------------------------------------------------------------------------
# What the ledger records
# ---------------------------------------------------------------------------


def test_a_sent_message_is_recorded_as_sent(db, person, outbox):
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.SENT
    assert mail.attempts == 1
    assert mail.sent_at is not None
    assert mail.last_error is None
    assert len(outbox.messages) == 1


def test_an_in_app_row_is_always_written_even_without_email(db, person):
    rows = notifications.notify(
        db,
        tenant_id=TENANT,
        user=person,
        kind="TEST",
        title="Hello",
        body="Body",
        send_email=False,
    )
    db.commit()

    assert len(rows) == 1
    assert rows[0].channel is NotificationChannel.IN_APP
    assert rows[0].status is NotificationStatus.SENT


def test_a_suppressed_message_is_distinguished_from_a_failure(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, suppressed=True, detail="off")
    )
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.SUPPRESSED
    assert mail.sent_at is None


def test_a_refused_message_is_recorded_as_failed_with_the_reason(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, detail="SMTPAuthenticationError: nope")
    )
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.FAILED
    assert "SMTPAuthenticationError" in mail.last_error


def test_a_failed_message_never_takes_down_the_caller(db, person, monkeypatch):
    """The booking happened whether or not the email did."""
    def explode(*a, **k):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=False, detail="refused"))
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()   # would raise if the failure had propagated
    assert db.query(Notification).count() == 2


def test_retry_picks_up_failures_and_leaves_suppressions_alone(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, detail="temporary refusal")
    )
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Failed one", body="Body"
    )
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, suppressed=True, detail="off")
    )
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Suppressed one", body="Body"
    )
    db.commit()

    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=True))
    result = notifications.retry_failed(db, TENANT)

    assert result == {"attempted": 1, "sent": 1, "still_failing": 0}
    suppressed = db.query(Notification).filter_by(title="Suppressed one").all()
    assert any(n.status is NotificationStatus.SUPPRESSED for n in suppressed)


def test_retry_gives_up_after_the_attempt_cap(db, person, monkeypatch):
    """An address that has refused three times is not going to start working,
    and retrying forever just hammers the SMTP server."""
    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=False, detail="refused"))
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    for _ in range(5):
        notifications.retry_failed(db, TENANT)

    mail = db.query(Notification).filter_by(channel=NotificationChannel.EMAIL).one()
    assert mail.attempts == notifications.MAX_ATTEMPTS


def test_the_ledger_summarises_by_status(db, person, outbox):
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    summary = notifications.ledger_summary(db, TENANT)
    assert summary["total"] == 2
    assert summary["emails"] == 1
    assert summary["by_status"]["SENT"] == 2


def test_a_user_with_no_address_gets_the_in_app_row_only(db):
    nameless = User(
        tenant_id=TENANT, email="", full_name="No Address", role=Role.GROUND_STAFF,
        password_hash="x",
    )
    db.add(nameless)
    db.commit()

    rows = notifications.notify(
        db, tenant_id=TENANT, user=nameless, kind="TEST", title="Hello", body="Body"
    )
    db.commit()
    assert [r.channel for r in rows] == [NotificationChannel.IN_APP]


def test_the_real_settings_confine_development_to_one_address():
    """A standing check on this machine's .env: delivery is on, so the allowlist
    is the only thing stopping the smoke scripts mailing a real domain."""
    settings = get_settings()
    if settings.email_enabled:
        assert settings.allowed_email_recipients, (
            "EMAIL_ENABLED is on with an empty EMAIL_ALLOWLIST - the smoke "
            "scripts would mail every invented @designboxed.com address"
        )
