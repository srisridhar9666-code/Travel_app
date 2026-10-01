"""
Response headers, and the checks that stop an insecure configuration reaching
production (SOW section 7).

Two separate jobs in one small module because they answer the same question -
"what would make this unsafe to deploy?" - from opposite ends: headers harden
every response, and `production_problems()` refuses to let the process start
with the defaults this repository ships.

That second one matters more than it looks. Every value it checks is a real
default sitting in a file in this project: an example signing key, a bootstrap
admin password written in the README, a CORS list pointing at localhost. Each is
correct for development and each is a breach in production, and the failure mode
is silence - nothing misbehaves, the app just quietly has a known password.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import Settings

logger = logging.getLogger(__name__)

#: Sent on every response.
#:
#: The CSP is deliberately strict and deliberately *not* applied to the API's own
#: docs. This service returns JSON and files; it renders no HTML of its own, so
#: `default-src 'none'` costs nothing and means a stored-XSS payload that somehow
#: reached a response body has nowhere to execute. The SPA is served separately
#: and carries its own policy.
SECURITY_HEADERS = {
    # A downloaded ID scan or ticket must never be sniffed into text/html and
    # rendered. This one is load-bearing, not boilerplate - see storage.py.
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}

#: Paths that render HTML on purpose. FastAPI's docs load Swagger from a CDN, so
#: the blanket policy above would white-screen them.
_HTML_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the headers above, plus HSTS when we are actually on TLS."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            if header == "Content-Security-Policy" and request.url.path.startswith(_HTML_PATHS):
                continue
            # Never clobber a header a handler set deliberately - the ID-proof
            # download sets its own Content-Disposition and nosniff.
            response.headers.setdefault(header, value)

        # Only over TLS. Sending HSTS on a plain-HTTP dev server teaches the
        # browser to refuse http://localhost for the next two years.
        if self.settings.is_production and request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )

        return response


def production_problems(settings: Settings) -> list[str]:
    """Configuration that must not reach production, as a list of plain sentences.

    Returns problems rather than raising, so the caller decides whether to refuse
    to boot or merely shout. Every item is a default that ships in this repo.
    """
    problems: list[str] = []

    if settings.secret_key in ("", "insecure-development-key") or len(settings.secret_key) < 32:
        problems.append(
            "SECRET_KEY is the development default or too short - every session "
            "token this server issues would be forgeable."
        )

    if settings.admin_password in ("", "ChangeMe@123"):
        problems.append(
            "ADMIN_PASSWORD is still the documented bootstrap value, which is "
            "published in the README."
        )

    if not settings.pii_encryption_key:
        problems.append(
            "PII_ENCRYPTION_KEY is unset - identity numbers would be stored "
            "unencrypted."
        )

    if any(origin in ("*", "") for origin in settings.cors_origins):
        problems.append("BACKEND_CORS_ORIGINS allows any origin.")

    if any("localhost" in origin or "127.0.0.1" in origin for origin in settings.cors_origins):
        problems.append(
            "BACKEND_CORS_ORIGINS still points at localhost, so the deployed "
            "frontend will be refused."
        )

    if settings.frontend_base_url.startswith("http://"):
        problems.append(
            "FRONTEND_BASE_URL is plain HTTP - invite and reset links would be "
            "sent over an unencrypted connection."
        )

    if settings.uses_object_storage and not settings.storage_bucket:
        problems.append(
            "STORAGE_BACKEND is 'gcs' but STORAGE_BUCKET is empty - every "
            "upload would fail at the moment a user tries to save one."
        )

    if settings.scheduler_enabled and not settings.scheduler_is_internal and not settings.scheduler_token:
        problems.append(
            "SCHEDULER_MODE is 'external' but SCHEDULER_TOKEN is unset, so "
            "nothing can trigger reminders and none would ever be sent."
        )

    if settings.email_enabled and not settings.allowed_email_recipients:
        # Not a fault in production; this is where the allowlist *should* be
        # empty. Worth one line in the log so nobody is surprised by real mail.
        logger.info("Email delivery is unrestricted (no EMAIL_ALLOWLIST) - correct for production")

    return problems


def check_startup(settings: Settings) -> None:
    """Shout in development, refuse to start in production.

    A misconfigured production deploy that boots anyway is worse than one that
    does not: it looks healthy, serves traffic, and has a known admin password.
    """
    problems = production_problems(settings)
    if not problems:
        return

    if settings.is_production:
        listed = "\n  - ".join(problems)
        raise RuntimeError(
            f"Refusing to start in production with {len(problems)} unsafe "
            f"setting(s):\n  - {listed}"
        )

    logger.warning(
        "%d setting(s) are fine for development but must change before production: %s",
        len(problems),
        "; ".join(problems),
    )
