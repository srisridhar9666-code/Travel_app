"""
Log formatting and request correlation.

Two things production needs that a development console does not:

* **One line per event, as JSON.** Cloud Logging, Loki and friends parse
  structured records; they do not parse a human-readable format string, and a
  stack trace spread over forty lines becomes forty unrelated log entries.
* **A request id on every line.** "It failed for one user around three o'clock"
  is only actionable if the lines belonging to that one request can be pulled
  out of everything else happening at the same time.

Text formatting stays the default, because reading JSON by eye during
development is miserable. Set ``LOG_FORMAT=json`` to switch.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

#: The id of the request being handled on this task. A context variable rather
#: than a thread local, because the request may hop threads via `to_thread`.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

#: Record attributes that are logging's own. Anything else a caller attached via
#: `extra=` is interesting and gets carried through.
_STANDARD = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName
    relativeCreated stack_info thread threadName taskName""".split()
)

#: Python level names to the severities Cloud Logging understands.
_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, shaped for Cloud Logging but useful anywhere."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            # Cloud Logging promotes "severity" to the entry's level; everything
            # else treats it as an ordinary field.
            "severity": _SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
            "request_id": request_id_var.get(),
        }

        if record.exc_info:
            # Inline rather than appended, so a traceback stays one log entry.
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value

        # default=str so a stray date or Decimal in `extra=` cannot turn a log
        # call into an exception.
        return json.dumps(payload, default=str, ensure_ascii=False)


class RequestIdFilter(logging.Filter):
    """Make the request id available to the text formatter too."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign an id to every request and echo it back.

    An inbound ``X-Request-ID`` is honoured so a trace survives the hop from a
    load balancer or another service. It is length-capped and sanitised, because
    it ends up in log lines and a header is attacker-controlled - an unbounded
    value is a log-injection vector.
    """

    def __init__(self, app: ASGIApp, *, header: str = "X-Request-ID") -> None:
        super().__init__(app)
        self.header = header

    async def dispatch(self, request, call_next):
        supplied = request.headers.get(self.header, "")
        clean = "".join(c for c in supplied if c.isalnum() or c in "-_")[:64]
        request_id = clean or uuid.uuid4().hex

        token = request_id_var.set(request_id)
        # Handlers can read it off the request without importing this module.
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers[self.header] = request_id
            return response
        finally:
            request_id_var.reset(token)


def configure(*, json_output: bool, level: str = "INFO") -> None:
    """Install the root handler. Replaces anything already attached, so calling
    this after `basicConfig` wins rather than doubling every line."""
    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())

    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] | %(message)s"
            )
        )

    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; let them propagate to ours instead so
    # access logs are formatted the same way as everything else.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers.clear()
        target.propagate = True
