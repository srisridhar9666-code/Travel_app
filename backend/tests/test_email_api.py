"""
The admin's "is email working?" screen: what the server is running with, and a
test send that reports how far it got.

The password must never come back in a response, and only an admin may press
the button - it sends mail from the company account.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.core import ratelimit
from app.core.enums import AuditAction, Role
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.user import User

TENANT = "designboxed"


@pytest.fixture
def client(db):
    def same_session():
        yield db

    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        # No lifespan: it would start the scheduler and probe the database.
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def people(db):
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, password_hash="x")
    db.add_all([admin, ravi])
    db.commit()
    return admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings()
    for name, value in dict(
        email_enabled=True, smtp_username="travel@designboxed.com",
        smtp_app_password="abcdefghijklmnop", email_from="travel@designboxed.com",
        email_allowlist="",
    ).items():
        monkeypatch.setattr(settings, name, value)
    return settings


class TestWhoMayLook:
    @pytest.mark.parametrize("method, path", [
        ("get", "/notifications/email/status"),
        ("post", "/notifications/email/test"),
    ])
    def test_ground_staff_are_refused(self, client, people, method, path):
        _, ravi = people
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(path, headers=auth(ravi), **kwargs)
        assert response.status_code == 403

    def test_signed_out_is_refused(self, client):
        assert client.get("/notifications/email/status").status_code == 401


class TestStatus:
    def test_the_password_is_described_never_shown(self, client, people, configured):
        admin, _ = people
        response = client.get("/notifications/email/status", headers=auth(admin))
        assert response.status_code == 200
        assert "abcdefghijklmnop" not in response.text
        body = response.json()
        assert body["problem"] is None
        assert body["settings"]["password"] == "set (16 characters)"
        assert body["settings"]["security"] == "STARTTLS"

    def test_a_switched_off_server_says_so(self, client, people, monkeypatch):
        admin, _ = people
        monkeypatch.setattr(get_settings(), "email_enabled", False)
        body = client.get("/notifications/email/status", headers=auth(admin)).json()
        assert "EMAIL_ENABLED" in body["problem"]


class TestTestSend:
    def test_it_goes_to_the_admin_by_default_and_is_audited(self, client, db, people,
                                                            configured, outbox):
        admin, _ = people
        response = client.post("/notifications/email/test", json={}, headers=auth(admin))

        assert response.status_code == 200
        result = response.json()
        assert (result["ok"], result["stage"], result["to"]) == (True, "done", admin.email)
        assert [m["to"] for m in outbox.messages] == [admin.email]
        assert "abcdefghijklmnop" not in response.text

        row = db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.NOTIFY)
            .order_by(AuditLog.id.desc())
        ).scalars().first()
        assert row is not None and "test email" in row.summary

    def test_another_address_can_be_typed(self, client, people, configured, outbox):
        admin, _ = people
        response = client.post("/notifications/email/test",
                               json={"to": "ops@designboxed.com"}, headers=auth(admin))
        assert response.json()["to"] == "ops@designboxed.com"

    def test_a_missing_setting_is_reported_not_raised(self, client, people, configured,
                                                      monkeypatch, outbox):
        admin, _ = people
        monkeypatch.setattr(configured, "smtp_app_password", "")
        result = client.post("/notifications/email/test", json={},
                             headers=auth(admin)).json()
        assert (result["ok"], result["stage"]) == (False, "config")
        assert "SMTP_APP_PASSWORD" in result["error"]
        assert outbox.messages == []

    def test_pressing_it_in_a_loop_is_stopped(self, client, people, configured, outbox):
        admin, _ = people
        codes = [
            client.post("/notifications/email/test", json={}, headers=auth(admin)).status_code
            for _ in range(ratelimit.EMAIL_TEST.limit + 1)
        ]
        assert codes[:-1] == [200] * ratelimit.EMAIL_TEST.limit
        assert codes[-1] == 429

    def test_a_forged_forwarding_header_does_not_reset_the_limit(self, client, people,
                                                                  configured, outbox):
        admin, _ = people
        codes = [
            client.post("/notifications/email/test", json={},
                        headers={**auth(admin), "X-Forwarded-For": f"10.0.0.{n}"}).status_code
            for n in range(ratelimit.EMAIL_TEST.limit + 1)
        ]
        assert codes[-1] == 429
