"""
Endpoints for the platform rather than for people.

These are called by infrastructure - a scheduler, a health prober - not by the
SPA and not by a signed-in user. They sit behind a shared secret rather than the
normal session, because the caller has no account and never will.

Kept out of the OpenAPI schema: they are not part of the product's API surface,
and an endpoint that triggers mail is not something to advertise on a public
docs page.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.services import scheduler

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def _authorise(token: str | None) -> None:
    """Compare against SCHEDULER_TOKEN in constant time.

    Refusing when the token is unset is deliberate. The alternative - an open
    endpoint whenever someone forgets to configure it - is exactly the failure
    that gets found by a crawler rather than by us.
    """
    expected = (settings.scheduler_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SCHEDULER_TOKEN is not configured, so this endpoint is closed.",
        )

    supplied = (token or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()

    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scheduler token."
        )


@router.post("/run-reminders")
async def run_reminders(
    authorization: str | None = Header(default=None),
    x_scheduler_token: str | None = Header(default=None),
) -> dict:
    """Run one reminder cycle now.

    For platforms where the API process is not guaranteed to be alive when a
    reminder falls due - anything that scales to zero. Cloud Scheduler calls
    this on a cron and the work happens on whatever instance answers.

    Safe to call as often as you like: every notice carries a dedupe key naming
    the event rather than the run, and a unique index enforces it, so a traveller
    cannot be reminded twice about the same trip however many times this fires.

    Accepts the token as either `Authorization: Bearer <token>` (what Cloud
    Scheduler sends) or `X-Scheduler-Token: <token>` (easier to curl).
    """
    _authorise(authorization or x_scheduler_token)

    results = await scheduler.run_once()
    worked = [r for r in results if r.get("notified") or r.get("sent")]
    if worked:
        logger.info("Reminder run (external trigger): %s", worked)

    return {"ok": True, "jobs": results}
