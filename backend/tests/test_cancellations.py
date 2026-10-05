"""
Cancelling a trip after it has been decided.

Until an admin acts, a requester withdraws a request on their own. Once someone
on it is approved or booked, a ticket may exist, so the requester asks and an
admin or their manager decides: approving cancels the trip, rejecting keeps it,
with a comment the requester reads.
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import Gender, RequestType, Role, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications

TENANT = "designboxed"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


def person(db, name, email, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email, full_name=name, role=role,
                gender=Gender.MALE, password_hash="x", **extra)
    db.add(user)
    db.commit()
    return user


@dataclass
class Ref:
    """Who someone is, without the ORM row: the request endpoints close the
    test's session, which detaches every row in it."""

    id: int
    role: Role


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(db):
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    lead = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER)
    other_lead = person(db, "Divya Nair", "divya@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=lead.id)
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
    db.add(project)
    db.commit()
    people = dict(admin=admin, lead=lead, other_lead=other_lead, ravi=ravi)
    refs = {key: Ref(u.id, u.role) for key, u in people.items()}
    refs["project_id"] = project.id
    return refs


def trip(db, org, status=TravellerStatus.BOOKED):
    day = clock.local_today() + timedelta(days=7)
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=org["project_id"],
        requester_id=org["ravi"].id, hotel_city="Pune", hotel_state="Maharashtra",
        check_in=day, check_out=day + timedelta(days=1), travel_reason="Store audit",
        submitted_at=naive_utcnow(),
    )
    row.travellers = [RequestTraveller(user_id=org["ravi"].id, status=status)]
    db.add(row)
    db.commit()
    return Ref(row.id, Role.GROUND_STAFF)


def reread(db, ref):
    """The request as it is now, from a fresh read."""
    db.expire_all()
    return db.get(TravelRequest, ref.id)


def cancel(client, user, row, reason="Client moved the audit to next month"):
    return client.post(f"/requests/{row.id}/cancel", headers=auth(user), json={"reason": reason})


def decide(client, user, row, approve, comment=None):
    return client.post(f"/requests/{row.id}/cancellation/decide", headers=auth(user),
                       json={"approve": approve, "comment": comment})


class TestAskingFirst:
    def test_an_undecided_request_is_withdrawn_at_once(self, client, db, org):
        row = trip(db, org, status=TravellerStatus.PENDING)
        r = cancel(client, org["ravi"], row)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"
        assert r.json()["cancellation_status"] is None

    @pytest.mark.parametrize("status", [TravellerStatus.APPROVED, TravellerStatus.BOOKED])
    def test_a_decided_trip_becomes_an_ask(self, client, db, org, status):
        row = trip(db, org, status=status)
        r = cancel(client, org["ravi"], row)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] != "CANCELLED"
        assert body["cancellation_status"] == "PENDING"
        assert body["cancellation_reason"] == "Client moved the audit to next month"
        now = reread(db, row)
        assert now.is_cancelled is False
        assert now.travellers[0].status is status

        told = {n.user_id for n in db.execute(select(Notification).where(
            Notification.kind == "CANCELLATION_REQUESTED", Notification.channel == "IN_APP"
        )).scalars()}
        assert told == {org["admin"].id, org["lead"].id}

        # One ask at a time.
        assert cancel(client, org["ravi"], row).status_code == 409

    def test_an_admin_still_cancels_outright(self, client, db, org):
        row = trip(db, org)
        r = cancel(client, org["admin"], row)
        assert r.json()["status"] == "CANCELLED"


class TestDeciding:
    def test_the_manager_approves_and_the_trip_is_cancelled(self, client, db, org):
        row = trip(db, org)
        cancel(client, org["ravi"], row)
        r = decide(client, org["lead"], row, True)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"
        assert r.json()["cancellation_status"] == "APPROVED"
        assert r.json()["cancellation_decided_by_name"] == "Anil Mehta"
        now = reread(db, row)
        assert now.travellers[0].status is TravellerStatus.CANCELLED
        assert now.cancel_reason == "Client moved the audit to next month"
        # The admins hear a manager's yes, so the booking is called off.
        admin_told = db.execute(select(Notification).where(
            Notification.user_id == org["admin"].id, Notification.kind == "CANCELLATION_APPROVED"
        )).scalars().first()
        assert admin_told is not None

    def test_a_rejection_needs_a_comment_and_keeps_the_trip(self, client, db, org):
        row = trip(db, org)
        cancel(client, org["ravi"], row)
        assert decide(client, org["admin"], row, False).status_code == 422
        r = decide(client, org["admin"], row, False, "Tickets are non-refundable")
        assert r.status_code == 200, r.text
        assert r.json()["cancellation_status"] == "REJECTED"
        assert r.json()["status"] == "BOOKED"
        told = db.execute(select(Notification).where(
            Notification.user_id == org["ravi"].id, Notification.kind == "CANCELLATION_REJECTED",
            Notification.channel == "IN_APP",
        )).scalars().first()
        assert "Tickets are non-refundable" in told.body
        # After an answer the requester may ask again.
        assert cancel(client, org["ravi"], row).json()["cancellation_status"] == "PENDING"

    def test_only_an_admin_or_their_own_manager(self, client, db, org):
        row = trip(db, org)
        cancel(client, org["ravi"], row)
        assert decide(client, org["ravi"], row, True).status_code == 403
        # Another manager cannot even see the trip.
        assert decide(client, org["other_lead"], row, True).status_code == 404

    def test_nothing_to_decide(self, client, db, org):
        row = trip(db, org)
        assert decide(client, org["admin"], row, True).status_code == 409

    def test_the_queue_counts_and_lists_them(self, client, db, org):
        row = trip(db, org)
        cancel(client, org["ravi"], row)
        counts = client.get("/requests/queue/counts", headers=auth(org["admin"])).json()
        assert counts["cancellations"] == 1
        listed = client.get("/requests", headers=auth(org["lead"]),
                            params={"mine": "false", "cancellation": "pending"}).json()
        assert [i["id"] for i in listed["items"]] == [row.id]
        assert listed["items"][0]["can_decide_cancellation"] is True

    def test_every_step_is_in_the_activity_log(self, client, db, org):
        row = trip(db, org)
        cancel(client, org["ravi"], row)
        decide(client, org["lead"], row, True)
        actions = [str(a.action) for a in db.execute(select(AuditLog).where(
            AuditLog.entity_type == "travel_request", AuditLog.entity_id == row.id
        ).order_by(AuditLog.id)).scalars()]
        assert actions == ["SUBMIT", "APPROVE", "CANCEL"]
