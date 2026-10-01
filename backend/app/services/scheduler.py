"""
The background loop that runs the reminder jobs.

An asyncio task rather than a cron entry or a second process, because at this
size a separate worker is more operational surface than the problem deserves -
and because the jobs are idempotent, so running them from every replica is
harmless rather than dangerous.

Three deliberate properties:

* **It starts late and never blocks startup.** The first cycle waits, so a
  restart loop cannot turn into a send loop, and the API answers requests while
  the loop is still asleep.
* **It cannot crash the app.** Every cycle is wrapped; a failing job is logged
  and the loop continues. A reminder system that takes the API down with it is
  worse than no reminder system.
* **It is off unless configured on.** `SCHEDULER_ENABLED` defaults to false, so
  a developer running the API against a production database does not start
  mailing people from their laptop.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.database import SessionLocal
from app.services import reminders

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def run_once() -> list[dict]:
    """One cycle of every job. Shared by the internal loop and the HTTP trigger,
    so both drive identical work and neither can quietly drift from the other."""
    settings = get_settings()

    # Sessions are cheap and a long-lived one would hold a connection open
    # across the idle period between cycles.
    db = SessionLocal()
    try:
        # `run_all` is synchronous and touches the database, so it goes to a
        # thread - blocking the event loop here would stall every request the
        # API is serving.
        return await asyncio.to_thread(reminders.run_all, db, settings.default_tenant)
    finally:
        db.close()


async def _loop() -> None:
    settings = get_settings()
    interval = max(settings.scheduler_interval_minutes, 1) * 60

    # Wait before the first cycle. A container that restarts repeatedly would
    # otherwise run the jobs on every boot.
    await asyncio.sleep(min(interval, 60))

    while True:
        try:
            results = await run_once()
            worked = [r for r in results if r.get("notified") or r.get("sent")]
            if worked:
                logger.info("Scheduler: %s", worked)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a bad cycle end the loop.
            logger.exception("Scheduler cycle failed; continuing")

        await asyncio.sleep(interval)


def start() -> bool:
    """Begin the loop if it is enabled, internal, and not already running."""
    global _task
    settings = get_settings()

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED is off)")
        return False

    # An in-process loop assumes a process that is always running. On a platform
    # that scales to zero there may be no instance alive when a reminder is due,
    # so the trigger has to come from outside - see POST /internal/run-reminders.
    if not settings.scheduler_is_internal:
        logger.info(
            "Scheduler is external: this process will not run the loop. "
            "Something must call POST /internal/run-reminders on a schedule."
        )
        return False

    if _task is not None and not _task.done():
        return True

    _task = asyncio.create_task(_loop(), name="travel-ops-reminders")
    logger.info(
        "Scheduler started: every %s minutes", settings.scheduler_interval_minutes
    )
    return True


async def stop() -> None:
    """Cancel the loop and wait for it, so shutdown is not racy."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown is best effort
        pass
    _task = None


def is_running() -> bool:
    return _task is not None and not _task.done()
