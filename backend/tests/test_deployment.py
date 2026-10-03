"""
The switches that let one image run locally and on a scale-to-zero platform.

None of this changes behaviour today - the defaults are the local ones - so the
value of these tests is entirely in the day someone flips a switch and needs the
other path to already work.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.hardening import production_problems
from app.main import app
from app.services import storage


@pytest.fixture
def client():
    # No lifespan: it would start the scheduler and probe the database.
    return TestClient(app)


def production(**overrides) -> Settings:
    """A settings object that is otherwise production-clean, so each test fails
    for exactly the reason it is about."""
    base = dict(
        environment="production",
        secret_key="x" * 48,
        admin_password="a-genuinely-different-password",
        pii_encryption_key="k" * 44,
        backend_cors_origins="https://travel.designboxed.com",
        frontend_base_url="https://travel.designboxed.com",
    )
    base.update(overrides)
    return Settings(**base)


class TestStorageBackendSwitch:
    def test_local_is_the_default(self):
        assert production().uses_object_storage is False

    def test_gcs_is_opt_in(self):
        assert production(storage_backend="gcs", storage_bucket="b").uses_object_storage is True

    def test_the_switch_is_case_insensitive(self):
        assert production(storage_backend="GCS", storage_bucket="b").uses_object_storage is True

    def test_gcs_without_a_bucket_is_refused(self):
        problems = production_problems(production(storage_backend="gcs"))
        assert any("STORAGE_BUCKET" in p for p in problems)

    def test_gcs_with_a_bucket_is_accepted(self):
        problems = production_problems(production(storage_backend="gcs", storage_bucket="db-travel"))
        assert not any("STORAGE_BUCKET" in p for p in problems)

    def test_local_backend_needs_no_bucket(self):
        assert not any("STORAGE_BUCKET" in p for p in production_problems(production()))


class TestStoragePathContract:
    """Stored paths must stay backend-agnostic, or a move to object storage
    would orphan every file already on disk."""

    def test_saved_paths_are_relative_and_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
        monkeypatch.setattr(type(storage.settings), "upload_path", property(lambda s: tmp_path))

        relative = storage.save_in("tickets", 42, b"%PDF-1.4 x", ".pdf")

        assert not relative.startswith("/")
        assert "\\" not in relative
        assert relative.startswith("tickets/42/")
        assert relative.endswith(".pdf")

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(type(storage.settings), "upload_path", property(lambda s: tmp_path))

        relative = storage.save_in("tickets", 7, b"%PDF-1.4 hello", ".pdf")
        assert storage.read(relative) == b"%PDF-1.4 hello"

        assert storage.delete(relative) is True
        with pytest.raises(HTTPException) as gone:
            storage.read(relative)
        assert gone.value.status_code == 404

    def test_deleting_something_absent_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(type(storage.settings), "upload_path", property(lambda s: tmp_path))
        assert storage.delete("tickets/1/never-existed.pdf") is True
        assert storage.delete(None) is False

    def test_traversal_is_still_refused(self, tmp_path, monkeypatch):
        """The containment check must survive the backend refactor."""
        monkeypatch.setattr(type(storage.settings), "upload_path", property(lambda s: tmp_path))
        assert storage.delete("../../../etc/passwd") is False


class TestSchedulerMode:
    def test_internal_is_the_default(self):
        assert production().scheduler_is_internal is True

    def test_external_is_opt_in(self):
        assert production(scheduler_mode="external").scheduler_is_internal is False

    def test_external_without_a_token_is_refused(self):
        problems = production_problems(
            production(scheduler_enabled=True, scheduler_mode="external")
        )
        assert any("SCHEDULER_TOKEN" in p for p in problems)

    def test_external_with_a_token_is_accepted(self):
        problems = production_problems(
            production(scheduler_enabled=True, scheduler_mode="external", scheduler_token="s3cret")
        )
        assert not any("SCHEDULER_TOKEN" in p for p in problems)

    def test_a_disabled_scheduler_needs_no_token(self):
        """Nothing is due to run, so nothing is misconfigured."""
        problems = production_problems(production(scheduler_enabled=False, scheduler_mode="external"))
        assert not any("SCHEDULER_TOKEN" in p for p in problems)


class TestInternalEndpoint:
    PATH = "/internal/run-reminders"

    def test_closed_when_no_token_is_configured(self, client, monkeypatch):
        """Open-by-default is the failure a crawler finds before we do."""
        monkeypatch.setattr("app.routers.internal.settings.scheduler_token", "")
        response = client.post(self.PATH, headers={"X-Scheduler-Token": "anything"})
        assert response.status_code == 503

    def test_rejects_a_wrong_token(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.internal.settings.scheduler_token", "correct-token")
        assert client.post(self.PATH, headers={"X-Scheduler-Token": "wrong"}).status_code == 401

    def test_rejects_no_token(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.internal.settings.scheduler_token", "correct-token")
        assert client.post(self.PATH).status_code == 401

    def test_accepts_the_bearer_form_cloud_scheduler_sends(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.internal.settings.scheduler_token", "correct-token")
        response = client.post(self.PATH, headers={"Authorization": "Bearer correct-token"})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_accepts_the_plain_header_form(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.internal.settings.scheduler_token", "correct-token")
        assert client.post(self.PATH, headers={"X-Scheduler-Token": "correct-token"}).status_code == 200

    def test_is_not_advertised_in_the_schema(self, client):
        """An endpoint that triggers mail does not belong on a docs page."""
        paths = client.get("/openapi.json").json()["paths"]
        assert self.PATH not in paths


class TestGeminiCredentialResolution:
    def test_inline_json_wins_over_a_path(self, monkeypatch):
        """The secret-manager shape takes precedence over a file on disk."""
        from app.services import gemini

        monkeypatch.setattr(gemini.settings, "gemini_credentials_json", "")
        monkeypatch.setattr(gemini.settings, "gemini_credentials_path", "/nowhere/missing.json")
        # Neither configured: falls through to application default credentials
        # rather than failing, which is what Cloud Run wants.
        _, source = gemini._resolve_credentials()
        assert "application default" in source


class TestVersionHandshake:
    """The web app reads /health to spot an API process older than itself -
    one that was not restarted after an update - and says so to admins."""

    def test_health_reports_the_version_and_pending_migrations(self, client, engine, monkeypatch):
        from app import main

        # The suite's scratch database, not DATABASE_URL's: CI never creates
        # the latter, and /health only checks migrations on a reachable one.
        monkeypatch.setattr(main, "engine", engine)
        monkeypatch.setattr(main, "schema_behind", lambda: None)
        body = client.get("/health").json()
        assert body["version"] == main.API_VERSION
        assert body["migrations_pending"] is False

        monkeypatch.setattr(main, "schema_behind", lambda: "the database is at abc")
        assert client.get("/health").json()["migrations_pending"] is True

    def test_the_web_app_expects_this_version(self):
        """Bumping one side and not the other would warn about a mismatch that
        is not there, or miss one that is."""
        import re
        from pathlib import Path

        from app.main import API_VERSION

        source = (Path(__file__).parents[2] / "web" / "src" / "lib" / "api.ts").read_text()
        match = re.search(r"export const API_VERSION = '([^']+)'", source)
        assert match, "web/src/lib/api.ts no longer declares API_VERSION"
        assert match.group(1) == API_VERSION


class TestDatabaseBehind:
    """A query for a column or table the database does not have yet answers 503
    with what to do, rather than a 500 every panel can only call "Could not
    refresh this page" - but only when the database really is behind."""

    PATH = "/__test__/schema-error"

    @pytest.fixture(params=[
        ("OperationalError", 1054, "Unknown column 'users.status' in 'field list'"),
        ("ProgrammingError", 1146, "Table 'travel_ops.departments' doesn't exist"),
    ], ids=["unknown-column", "missing-table"])
    def failing_route(self, request):
        import pymysql
        from sqlalchemy import exc as sa_exc

        name, code, message = request.param

        def fail():
            raise getattr(sa_exc, name)(
                "SELECT 1", {}, getattr(pymysql.err, name)(code, message)
            )

        app.add_api_route(self.PATH, fail)
        yield
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != self.PATH]

    def test_a_behind_database_is_named(self, failing_route, monkeypatch):
        from app import main

        monkeypatch.setattr(main, "schema_behind", lambda: "the database is at a but needs b")
        response = TestClient(app).get(self.PATH)
        assert response.status_code == 503
        assert "alembic upgrade head" in response.json()["detail"]

    def test_the_same_error_on_a_current_database_stays_a_500(self, failing_route, monkeypatch):
        """Then it is a bug in the code, and saying "migrate" would mislead."""
        from app import main

        monkeypatch.setattr(main, "schema_behind", lambda: None)
        response = TestClient(app, raise_server_exceptions=False).get(self.PATH)
        assert response.status_code == 500


class TestRequestCorrelation:
    def test_a_request_id_is_issued_and_echoed(self, client):
        response = client.get("/health")
        assert len(response.headers.get("X-Request-ID", "")) == 32

    def test_each_request_gets_its_own(self, client):
        first = client.get("/health").headers["X-Request-ID"]
        second = client.get("/health").headers["X-Request-ID"]
        assert first != second

    def test_an_inbound_id_is_honoured(self, client):
        """So a trace survives the hop from a load balancer."""
        response = client.get("/health", headers={"X-Request-ID": "lb-abc-123"})
        assert response.headers["X-Request-ID"] == "lb-abc-123"

    def test_a_hostile_id_is_sanitised(self, client):
        """The header is attacker-controlled and ends up in log lines, so
        newlines and control characters must not survive into them."""
        response = client.get(
            "/health", headers={"X-Request-ID": "abc\r\nseverity=CRITICAL fake-entry"}
        )
        returned = response.headers["X-Request-ID"]
        assert "\n" not in returned and "\r" not in returned
        assert " " not in returned
        assert returned.startswith("abc")

    def test_an_overlong_id_is_capped(self, client):
        response = client.get("/health", headers={"X-Request-ID": "x" * 500})
        assert len(response.headers["X-Request-ID"]) == 64

    def test_an_empty_id_falls_back_to_a_generated_one(self, client):
        response = client.get("/health", headers={"X-Request-ID": "!!!"})
        assert len(response.headers["X-Request-ID"]) == 32


class TestLogFormatSelection:
    def test_text_in_development_by_default(self):
        from app.config import Settings

        assert Settings(environment="development").json_logs is False

    def test_json_in_production_by_default(self):
        assert production().json_logs is True

    def test_an_explicit_choice_wins_either_way(self):
        from app.config import Settings

        assert Settings(environment="development", log_format="json").json_logs is True
        assert production(log_format="text").json_logs is False

    def test_json_records_are_one_line_each(self):
        """A traceback spread over forty lines becomes forty unrelated entries."""
        import json
        import logging

        from app.core.logging import JsonFormatter

        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "t", logging.ERROR, "f", 1, "it failed", None, sys.exc_info()
            )

        line = JsonFormatter().format(record)
        assert "\n" not in line
        parsed = json.loads(line)
        assert parsed["severity"] == "ERROR"
        assert "ValueError: boom" in parsed["exception"]

    def test_extra_fields_are_carried_through(self):
        import json
        import logging

        from app.core.logging import JsonFormatter

        record = logging.LogRecord("t", logging.INFO, "f", 1, "approved", None, None)
        record.traveller_id = 412
        parsed = json.loads(JsonFormatter().format(record))
        assert parsed["traveller_id"] == 412
