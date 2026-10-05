"""
The Activity log page lists what people did, not when they signed in.

Sign-ins, sign-outs and failed sign-ins are still written to the ledger - the
tamper check walks every row - but the page's list, counts and CSV leave them
out.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import ratelimit
from app.core.enums import AuditAction, Gender, Role
from app.core.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.user import User

TENANT = "designboxed"
PASSWORD = "Monsoon-Ledger-42"
SIGN_INS = {"LOGIN", "LOGIN_FAILED", "LOGOUT"}


@pytest.fixture
def client(db):
    def same_session():
        yield db

    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def people(db):
    hashed = hash_password(PASSWORD)
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, gender=Gender.FEMALE, password_hash=hashed)
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, gender=Gender.MALE, password_hash=hashed)
    db.add_all([admin, ravi])
    db.commit()
    return admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ledger(client, db, people):
    """A sign-in, a failed one, a sign-out, and one real change."""
    admin, ravi = people
    assert client.post("/auth/login", json={"email": ravi.email, "password": PASSWORD}).status_code == 200
    assert client.post("/auth/login", json={"email": ravi.email, "password": "wrong-guess-1"}).status_code == 401
    assert client.post("/auth/logout", headers=auth(ravi)).status_code == 200
    made = client.post("/departments", headers=auth(admin), json={"name": "Field Ops"})
    assert made.status_code == 201, made.text
    return admin


def test_the_list_leaves_sign_ins_out(client, db, ledger):
    body = client.get("/audit", headers=auth(ledger), params={"page_size": 200}).json()
    actions = {row["action"] for row in body["items"]}
    assert not actions & SIGN_INS
    assert "CREATE" in actions
    assert body["total"] == len(body["items"])

    # Asking for them by name finds nothing either.
    assert client.get("/audit", headers=auth(ledger), params={"action": "LOGIN"}).json()["total"] == 0


def test_counts_and_export_match_the_list(client, ledger):
    summary = client.get("/audit/summary", headers=auth(ledger)).json()
    assert not set(summary["by_action"]) & SIGN_INS
    listed = client.get("/audit", headers=auth(ledger)).json()["total"]
    assert summary["total"] == listed

    csv = client.get("/audit/export", headers=auth(ledger)).text
    assert ",LOGIN," not in csv and ",LOGIN_FAILED," not in csv and ",LOGOUT," not in csv
    assert ",CREATE," in csv


def test_sign_ins_are_still_recorded_and_the_chain_holds(client, db, ledger):
    recorded = db.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action.in_([AuditAction.LOGIN, AuditAction.LOGIN_FAILED, AuditAction.LOGOUT])
        )
    ).scalar_one()
    assert recorded == 3
    assert client.get("/audit/verify", headers=auth(ledger)).json()["ok"] is True
