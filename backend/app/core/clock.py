"""
The zone people read times in.

Everything the server records is UTC (`models/base.py`), and stays that way:
comparisons, the audit hash chain and the database never see a local time. This
module is only about the two places a zone has to be chosen:

* **Which date is "today".** `date.today()` is the host's date, which is IST on
  a laptop in Hyderabad and UTC in a container - so for five and a half hours
  every night a server in the cloud thought it was still yesterday, and a trip
  leaving at 1 a.m. was "tomorrow".
* **How an instant is written out.** The columns are naive UTC. Sent as a naive
  ISO string, a browser reads "2026-10-01T18:40:00" as *its own* local time, so
  every recorded time showed 5h30 early in India. `utc_iso` marks it as UTC;
  the browser then converts it to India time itself.

Trip times (`start_at`, `check_in`, a ticket's departure) are different: they
are the time on the ticket, as a person typed it, and are never converted.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings

#: India has kept one offset, without daylight saving, since 1945 - so a fixed
#: +05:30 is exact, not an approximation. It is the fallback because Python on
#: Windows ships no time zone database unless the `tzdata` package is installed.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _zone_name() -> str:
    return get_settings().app_timezone.strip() or "Asia/Kolkata"


@lru_cache
def app_tz() -> tzinfo:
    """The configured zone, or IST if this machine cannot resolve it.

    Never logs: the console log formatter calls this for every line, so a
    warning from in here would format itself, call this again, and recurse.
    `zone_problem()` is what start-up reports instead.
    """
    try:
        return ZoneInfo(_zone_name())
    except (ZoneInfoNotFoundError, ValueError):
        return IST


def zone_problem() -> str | None:
    """Why the configured zone is not in use, for the start-up log; None when it is."""
    name = _zone_name()
    if name in ("Asia/Kolkata", "Asia/Calcutta") or app_tz() is not IST:
        return None
    return f"APP_TIMEZONE {name!r} is unknown on this machine; using India time (+05:30)."


def now_local() -> datetime:
    """Aware now, in the app's zone."""
    return datetime.now(timezone.utc).astimezone(app_tz())


def local_today(now: datetime | None = None) -> date:
    """Today's date in the app's zone - not the server's.

    `now` is for tests: an aware datetime is converted; a naive one is taken to
    be UTC, as every naive time in this system is.
    """
    if now is None:
        return now_local().date()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(app_tz()).date()


def to_local(instant: datetime) -> datetime:
    """A recorded instant (naive means UTC) as an aware time in the app's zone."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(app_tz())


def utc_iso(instant: datetime) -> str:
    """ISO 8601 in UTC with a trailing Z, which every browser parses as UTC."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_utc_naive(value: datetime) -> datetime:
    """A filter bound as the columns hold it: naive UTC. Naive input is taken
    to be UTC already; an aware one is converted, not just stripped."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def time_label(value: datetime, date_format: str = "%d %b %Y") -> str:
    """"14 Oct 2026, 6:00 AM" - a time as people read it, 12-hour, like the app.

    Takes the value as given: trip times are stored as India wall-clock time
    already, and a recorded instant should go through `to_local` first.
    """
    hour = value.strftime("%I:%M %p").lstrip("0")
    return f"{value.strftime(date_format)}, {hour}" if date_format else hour
