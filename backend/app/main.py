"""
FastAPI application entrypoint.

Wires configuration, the database session, CORS for the Vite dev server, the
health probes, and the Phase 1 routers.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.core.hardening import SecurityHeadersMiddleware, check_startup
from app.core.logging import RequestIdMiddleware, configure as configure_logging
from app.database import SessionLocal, engine
from app.routers import analytics as analytics_router
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import id_proofs as id_proofs_router
from app.routers import internal as internal_router
from app.routers import locations as locations_router
from app.routers import notifications as notifications_router
from app.routers import projects as projects_router
from app.routers import requests as requests_router
from app.routers import tickets as tickets_router
from app.routers import users as users_router
from app.services import email as email_service
from app.services import gemini, scheduler
from app.services import locations as location_service
from app.services.seed import ensure_bootstrap_admin, ensure_other_project

settings = get_settings()

configure_logging(json_output=settings.json_logs, level=settings.log_level)
logger = logging.getLogger("travel_ops")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything else: refuse to start in production with the development
    # defaults this repository ships. A misconfigured deploy that boots anyway
    # looks healthy and has a published admin password.
    check_startup(settings)

    settings.upload_path.mkdir(parents=True, exist_ok=True)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    # Schema comes from Alembic, never from create_all. If the tables are not
    # there yet the app should say so loudly rather than invent them.
    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
        ensure_other_project(db, settings.default_tenant)
        location_service.seed(db, settings.default_tenant)
        db.commit()
    except Exception:
        logger.exception("Bootstrap admin check failed - have migrations been run?")
        db.rollback()
    finally:
        db.close()

    scheduler.start()

    yield

    await scheduler.stop()
    engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="0.8.0",
    description="Field logistics, travel requests and accommodation for ground staff.",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware, settings=settings)

# Added last, so it runs first: every other middleware and handler then sees a
# request id already set.
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(projects_router.router)
app.include_router(requests_router.router)
app.include_router(tickets_router.router)
app.include_router(notifications_router.router)
app.include_router(analytics_router.router)
app.include_router(id_proofs_router.router)
app.include_router(audit_router.router)
app.include_router(locations_router.router)
app.include_router(internal_router.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness plus a real database round-trip."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        logger.exception("Database health check failed")
        database = f"error: {type(exc).__name__}"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": database,
    }


@app.get("/health/gemini", tags=["health"])
def health_gemini() -> dict:
    """Confirms the service account actually reaches the extraction model."""
    return gemini.ping()


@app.get("/health/email", tags=["health"])
def health_email() -> dict:
    """Authenticates against SMTP without sending anything.

    A wrong app password should surface on the dashboard, not at the moment a
    traveller is waiting for a booking confirmation.
    """
    return email_service.check()
