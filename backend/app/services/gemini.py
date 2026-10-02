"""
Gemini client, wired to Vertex AI.

Credentials are resolved three ways, in this order, so the same image runs on a
laptop and on Cloud Run without conditional code:

1. ``GEMINI_CREDENTIALS_JSON`` - the service account as inline JSON, which is the
   shape a secret manager hands you as an environment variable.
2. ``GEMINI_CREDENTIALS_PATH`` - a file on disk. The local development path.
3. **Application Default Credentials** - nothing configured at all. On Cloud Run
   this picks up the runtime service account attached to the revision, which is
   the best option available: there is no key to leak, rotate or accidentally
   bake into an image.

The client is built once and reused; unusable credentials degrade to ``None`` so
the rest of the API still boots and only extraction reports unavailable.
"""
from __future__ import annotations

import json
import logging
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_client = None
_init_lock = threading.Lock()
_init_attempted = False
#: How the live client authenticated, for /health/gemini to report.
_credential_source: str = "none"


def _resolve_credentials():
    """Return (credentials, description). Credentials may be None for ADC."""
    from google.oauth2 import service_account

    inline = (settings.gemini_credentials_json or "").strip()
    if inline:
        info = json.loads(inline)
        return (
            service_account.Credentials.from_service_account_info(info, scopes=SCOPES),
            f"inline JSON ({info.get('client_email', 'unknown')})",
        )

    path = settings.credentials_file
    if path.exists():
        return (
            service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES),
            f"file {path.name}",
        )

    # None tells google-genai to fall back to Application Default Credentials.
    return None, "application default credentials"


def get_client():
    """Return a shared genai Client, or None when credentials are unusable."""
    global _client, _init_attempted, _credential_source

    if _init_attempted:
        return _client

    with _init_lock:
        if _init_attempted:      # another thread won the race
            return _client
        _init_attempted = True

        try:
            from google import genai

            credentials, source = _resolve_credentials()
            _client = genai.Client(
                vertexai=True,
                project=settings.gemini_project_id,
                location=settings.gemini_location,
                credentials=credentials,
            )
            _credential_source = source
            logger.info(
                "Gemini ready: project=%s location=%s model=%s via %s",
                settings.gemini_project_id,
                settings.gemini_location,
                settings.gemini_extraction_model,
                source,
            )
        except Exception:
            logger.exception("Gemini disabled: client could not be constructed")
            _client = None

    return _client


def ping() -> dict:
    """Round-trip the configured model once. Used by /health/gemini and by the
    Phase 0 spike to prove the credentials reach the model before we build on it."""
    client = get_client()
    if client is None:
        return {"ok": False, "detail": "client unavailable - check credentials"}

    try:
        response = client.models.generate_content(
            model=settings.gemini_extraction_model,
            contents="Reply with the single word: ready",
        )
        return {
            "ok": True,
            "model": settings.gemini_extraction_model,
            "credentials": _credential_source,
            "reply": (response.text or "").strip()[:80],
        }
    except Exception as exc:
        logger.exception("Gemini ping failed")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:400]}
