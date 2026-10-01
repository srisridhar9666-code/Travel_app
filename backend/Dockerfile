# ---------------------------------------------------------------------------
# API image.
#
# Two stages so the build tools and the lockfile resolution do not ship: the
# runtime layer gets the virtualenv and the source, nothing else.
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS builder

# uv resolves and installs from the lockfile, so an image built today and one
# built in six months contain the same dependency tree.
#
# Pinned to the version that wrote uv.lock. An older uv may not understand a
# newer lock format, and the failure arrives as an opaque build error rather
# than anything that names the real cause.
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies before source. Editing a router then rebuilds one layer rather
# than re-resolving every package.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime

# libmagic and the fonts PyMuPDF needs to rasterise a PDF page for extraction.
# curl is for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# A compromised dependency should not be running as root.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser scripts ./scripts

# Only used when STORAGE_BACKEND=local. On a platform with an ephemeral
# filesystem this must be a mounted volume, or STORAGE_BACKEND must be "gcs".
RUN mkdir -p /app/uploads && chown appuser:appuser /app/uploads
VOLUME ["/app/uploads"]

USER appuser

# Cloud Run injects PORT and expects the server to honour it. Default for
# everywhere else.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Shell form so ${PORT} expands. One worker: the reminder loop is in-process and
# the rate limiter counts in memory, so a second worker in the same container
# would duplicate both. Scale with instances, not workers.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'
