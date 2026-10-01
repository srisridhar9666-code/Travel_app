"""
Application configuration.

Every setting is read from the .env sitting next to this package, never from the
process CWD, so the API behaves the same whether it is started by uvicorn, by a
test runner, or by a background worker.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "Field Logistics & Travel Management"
    environment: str = "development"

    #: "text" is readable in a terminal; "json" is one object per line, which is
    #: what Cloud Logging and Loki parse. Defaults to json in production.
    log_format: str = ""
    log_level: str = "INFO"

    database_url: str = "mysql+pymysql://root@127.0.0.1:3306/travel_ops?charset=utf8mb4"

    secret_key: str = "insecure-development-key"
    access_token_expire_minutes: int = 480

    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Where invite and reset links point. Must match the deployed frontend.
    frontend_base_url: str = "http://localhost:5173"

    # Encrypts identity document numbers at rest. Losing this loses the numbers:
    # back it up separately from the database, or the pairing defeats the point.
    pii_encryption_key: str = ""

    # ID proofs are purged this long after an employee's exit date (addendum C4).
    id_proof_retention_days: int = 90

    max_upload_mb: int = 10

    admin_email: str = "admin@designboxed.com"
    admin_password: str = "ChangeMe@123"
    admin_name: str = "System Admin"

    # The product ships single-tenant, but every core table carries a tenant so
    # onboarding a second company is a config change rather than a migration.
    default_tenant: str = "designboxed"

    gemini_credentials_path: str = "../gemini_credentials.json"
    #: The service account as inline JSON, which is the shape a secret manager
    #: hands you. Takes precedence over the path. Leave both empty on Cloud Run
    #: and the runtime service account is used instead - no key to leak.
    gemini_credentials_json: str = ""
    gemini_project_id: str = "db-data-team"
    gemini_location: str = "global"
    gemini_extraction_model: str = "gemini-3.1-pro-preview"

    # --- file storage -----------------------------------------------------
    #: "local" writes to UPLOAD_DIR; "gcs" writes to STORAGE_BUCKET.
    #:
    #: Local disk is correct on one long-lived machine and wrong on Cloud Run,
    #: where the filesystem is per-instance and vanishes with the revision - an
    #: uploaded passport scan would survive until the next deploy and no longer.
    #: The switch exists so that move is configuration, not a rewrite.
    storage_backend: str = "local"
    storage_bucket: str = ""
    upload_dir: str = "./uploads"

    # --- email delivery ---------------------------------------------------
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_app_password: str = ""
    email_from: str = ""
    email_from_name: str = "Travel Ops"

    #: Master switch. Off by default so a fresh checkout, a test run and CI can
    #: never send real mail by accident - it has to be turned on deliberately.
    email_enabled: bool = False

    # --- the reminder scheduler -------------------------------------------
    #: Off by default so a developer running the API against a production
    #: database does not start mailing people from their laptop.
    scheduler_enabled: bool = False
    scheduler_interval_minutes: int = 30

    #: "internal" runs the loop inside the API process - correct on exactly one
    #: instance. "external" leaves the loop stopped and expects something else to
    #: call POST /internal/run-reminders on a schedule, which is what Cloud
    #: Scheduler does. Two instances both running an internal loop means every
    #: traveller gets two reminder emails.
    scheduler_mode: str = "internal"
    #: Shared secret for that endpoint. Required whenever mode is "external".
    scheduler_token: str = ""

    #: Addresses that may actually receive mail, comma separated. Empty means no
    #: restriction, which is what production wants. In development it is the
    #: difference between proving delivery works and mailing a hundred invented
    #: addresses at a real domain.
    email_allowlist: str = ""

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    @field_validator("smtp_app_password")
    @classmethod
    def _drop_spaces(cls, value: str) -> str:
        # Google shows an App Password as "abcd efgh ijkl mnop" and people paste
        # it that way. The password itself has no spaces, and SMTP sign-in with
        # them fails with a message that never mentions spaces.
        return "".join(value.split())

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def credentials_file(self) -> Path:
        """Absolute path to the Gemini service account, resolved against the backend dir."""
        raw = Path(self.gemini_credentials_path)
        return raw if raw.is_absolute() else (BACKEND_DIR / raw).resolve()

    @property
    def upload_path(self) -> Path:
        raw = Path(self.upload_dir)
        return raw if raw.is_absolute() else (BACKEND_DIR / raw).resolve()

    @property
    def allowed_email_recipients(self) -> set[str]:
        return {
            address.strip().lower()
            for address in self.email_allowlist.split(",")
            if address.strip()
        }

    @property
    def json_logs(self) -> bool:
        """Structured by default in production, readable by default elsewhere."""
        choice = self.log_format.strip().lower()
        if choice:
            return choice == "json"
        return self.is_production

    @property
    def uses_object_storage(self) -> bool:
        return self.storage_backend.strip().lower() == "gcs"

    @property
    def scheduler_is_internal(self) -> bool:
        """Whether this process should run the reminder loop itself."""
        return self.scheduler_mode.strip().lower() != "external"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
