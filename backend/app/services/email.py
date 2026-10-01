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

from app.config import ENV_FILE, get_settings

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
        return Sent(
            ok=False,
            suppressed=True,
            detail="email delivery is switched off (EMAIL_ENABLED is not true in backend/.env)",
        )

    if not _may_send_to(to_address):
        # Not an error. Someone chose to confine this environment.
        return Sent(
            ok=False,
            suppressed=True,
            detail="address is outside EMAIL_ALLOWLIST for this environment",
        )

    missing = missing_settings()
    if missing:
        return Sent(
            ok=False,
            detail=f"SMTP is enabled but not configured: {', '.join(missing)} is not set",
        )

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


def missing_settings() -> list[str]:
    """The SMTP settings a send needs that are still blank."""
    settings = get_settings()
    return [
        name
        for name, value in (
            ("SMTP_USERNAME", settings.smtp_username),
            ("SMTP_APP_PASSWORD", settings.smtp_app_password),
            ("EMAIL_FROM", settings.email_from),
        )
        if not value.strip()
    ]


def configuration_problem() -> str | None:
    """Why mail would not leave this server right now, in words a person can act on.

    None when it is configured to send. Says nothing about whether the
    credentials are right - `check()` proves that against the server.
    """
    settings = get_settings()
    if not ENV_FILE.exists():
        return (
            f"there is no {ENV_FILE} - the API reads its settings from backend/.env, "
            "not from the .env beside docker-compose.yml"
        )
    if not settings.email_enabled:
        return "EMAIL_ENABLED is not true in backend/.env"
    missing = missing_settings()
    if missing:
        return f"{', '.join(missing)} is not set in backend/.env"
    return None


def log_configuration() -> None:
    """Say once, at startup, whether this server will send mail.

    "The emails are not arriving" otherwise has to be debugged from the
    outside; this puts the answer in the first screen of the server log.
    """
    settings = get_settings()
    problem = configuration_problem()
    if problem:
        logger.warning("Email will NOT be sent: %s. GET /health/email re-checks.", problem)
        return
    allowlist = settings.allowed_email_recipients
    logger.info(
        "Email delivery on: %s:%s as %s%s",
        settings.smtp_host,
        settings.smtp_port,
        settings.email_from,
        f" (only to EMAIL_ALLOWLIST: {', '.join(sorted(allowlist))})" if allowlist else "",
    )


def send_account_link(
    to_address: str, full_name: str, url: str, *, purpose: str, valid_hours: int
) -> Sent:
    """Email an invite or password-reset link to the person it is for."""
    settings = get_settings()
    first_name = (full_name or "").split()[0] if (full_name or "").strip() else "there"
    if purpose == "invite":
        subject = f"You're invited to {settings.app_name}"
        lead = (
            "An administrator has created an account for you. "
            "Choose a password to sign in:"
        )
    else:
        subject = f"Reset your {settings.app_name} password"
        lead = "Someone asked to reset the password on your account. Choose a new one here:"

    body = (
        f"Hi {first_name},\n\n"
        f"{lead}\n\n{url}\n\n"
        f"The link works once and expires in {valid_hours} hours. "
        "If you were not expecting this, you can ignore it.\n\n"
        f"- {settings.email_from_name}"
    )
    return send(to_address, subject, body)


def check() -> dict:
    """Prove the SMTP credentials work, without sending anything.

    Used by /health/email. Connects, negotiates TLS and authenticates, then hangs
    up - so a broken app password is visible on the dashboard rather than at the
    moment someone is waiting for a booking confirmation.
    """
    settings = get_settings()
    problem = configuration_problem()
    if problem:
        return {"ok": False, "detail": problem}

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
