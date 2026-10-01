"""
Sending mail, and deciding when not to.

Three guards stand between this module and a real inbox, because the cost of
getting it wrong is not a failed test - it is a hundred invented addresses at a
real domain bouncing off one Gmail account and taking its sending reputation
with them.

1. **`EMAIL_ENABLED` is off by default.** A fresh checkout, a test run and CI
   cannot send. Turning it on is a deliberate act in `.env`.
2. **`EMAIL_ALLOWLIST` confines development.** When it is set, only those
   addresses actually leave the building; everything else is recorded as
   SUPPRESSED. The smoke scripts create accounts like
   `p5.ravi.a1b2c3@designboxed.com`, and that is a real domain.
3. **An outbox replaces the wire in tests.** `use_outbox()` swaps the transport
   for a list, so the delivery path itself is exercised without a socket.

Nothing here raises on a delivery failure. A refused message is a recorded fact
on the notification row, not an exception that rolls back the approval that
caused it - the booking happened whether or not the email did.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Sent:
    """What happened to one message."""

    ok: bool
    suppressed: bool = False
    detail: str | None = None


@dataclass
class Outbox:
    """Captured messages, for tests and for the dev console."""

    messages: list[dict] = field(default_factory=list)

    def clear(self) -> None:
        self.messages.clear()


#: When set, `send` writes here instead of opening a socket.
_outbox: Outbox | None = None


@contextmanager
def use_outbox():
    """Redirect every send into a list for the duration of the block."""
    global _outbox
    previous = _outbox
    _outbox = Outbox()
    try:
        yield _outbox
    finally:
        _outbox = previous


def _may_send_to(address: str) -> bool:
    """Whether this address is allowed a real message right now."""
    allowed = get_settings().allowed_email_recipients
    if not allowed:
        return True   # unrestricted, which is what production wants
    return address.strip().lower() in allowed


def build(to_address: str, subject: str, body: str) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    message["To"] = to_address
    message.set_content(body)
    return message


def send(to_address: str, subject: str, body: str) -> Sent:
    """Deliver one message, or record precisely why it was not delivered."""
    settings = get_settings()

    if not to_address or "@" not in to_address:
        return Sent(ok=False, detail="no usable address on this account")

    if _outbox is not None:
        _outbox.messages.append({"to": to_address, "subject": subject, "body": body})
        return Sent(ok=True)

    if not settings.email_enabled:
        return Sent(ok=False, suppressed=True, detail="email delivery is switched off")

    if not _may_send_to(to_address):
        # Not an error. Someone chose to confine this environment.
        return Sent(
            ok=False,
            suppressed=True,
            detail="address is outside EMAIL_ALLOWLIST for this environment",
        )

    if not (settings.smtp_username and settings.smtp_app_password and settings.email_from):
        return Sent(ok=False, detail="SMTP is enabled but not configured")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_app_password)
            smtp.send_message(build(to_address, subject, body))
        logger.info("Mail sent to %s: %s", to_address, subject)
        return Sent(ok=True)
    except Exception as exc:
        # Deliberately broad: DNS, TLS, auth and refusal are all the same
        # outcome here, and none of them may take down the caller.
        # The message body is never logged - it carries a PNR and a full name.
        logger.warning("Mail to %s failed: %s", to_address, type(exc).__name__)
        return Sent(ok=False, detail=f"{type(exc).__name__}: {exc}"[:400])


def check() -> dict:
    """Prove the SMTP credentials work, without sending anything.

    Used by /health/email. Connects, negotiates TLS and authenticates, then hangs
    up - so a broken app password is visible on the dashboard rather than at the
    moment someone is waiting for a booking confirmation.
    """
    settings = get_settings()
    if not settings.email_enabled:
        return {"ok": False, "detail": "email delivery is switched off"}
    if not (settings.smtp_username and settings.smtp_app_password):
        return {"ok": False, "detail": "SMTP is enabled but not configured"}

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_app_password)
        return {
            "ok": True,
            "host": settings.smtp_host,
            "from": settings.email_from,
            "restricted_to": sorted(settings.allowed_email_recipients) or None,
        }
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300]}
