"""
Booking details beside the reference: what a traveller needs on the day.

Typed when an admin marks someone booked (or taken from a confirmed ticket),
shown back on the request, and listed in the booking email.
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import Gender, RequestType, Role, TravelMode, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
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


@dataclass
class Ref:
    id: int
    role: Role


def auth(ref):
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def booked_flight(db):
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, gender=Gender.FEMALE, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, gender=Gender.MALE, password_hash="x")
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
    db.add_all([admin, ravi, project])
    db.commit()
    day = clock.now_local().replace(tzinfo=None, microsecond=0) + timedelta(days=9)
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.LONG_DISTANCE, mode=TravelMode.FLIGHT,
        project_id=project.id, requester_id=ravi.id, origin="Hyderabad", destination="Pune",
        start_at=day, travel_reason="Store audit", submitted_at=naive_utcnow(),
    )
    row.travellers = [RequestTraveller(user_id=ravi.id, status=TravellerStatus.APPROVED)]
    db.add(row)
    db.commit()
    return dict(admin=Ref(admin.id, admin.role), ravi=Ref(ravi.id, ravi.role),
                request=row.id, traveller=row.travellers[0].id)


def book(client, w, **details):
    return client.post(
        f"/requests/{w['request']}/travellers/{w['traveller']}/decide",
        headers=auth(w["admin"]),
        json={"to_status": "BOOKED", "reason": "Booked with the travel desk",
              "booking_reference": "QK8T2M", "booking_details": details or None},
    )


def test_the_details_are_kept_and_shown_to_the_traveller(client, db, booked_flight):
    r = book(client, booked_flight, carrier="IndiGo", service_number="6E 4412",
             depart_at="2026-10-14T06:10:00", arrive_at="2026-10-14T07:40:00", seat="14C")
    assert r.status_code == 200, r.text
    mine = client.get(f"/requests/{booked_flight['request']}",
                      headers=auth(booked_flight["ravi"])).json()
    details = mine["travellers"][0]["booking_details"]
    assert details == {"carrier": "IndiGo", "service_number": "6E 4412",
                       "depart_at": "2026-10-14T06:10:00", "arrive_at": "2026-10-14T07:40:00",
                       "seat": "14C"}
    mail = db.execute(select(Notification).where(
        Notification.kind == "REQUEST_BOOKED", Notification.channel == "EMAIL"
    )).scalars().first()
    assert "IndiGo" in mail.body and "6E 4412" in mail.body
    assert "6:10 AM" in mail.body and "14C" in mail.body


def test_arrival_before_departure_is_refused(client, booked_flight):
    r = book(client, booked_flight, depart_at="2026-10-14T09:00:00", arrive_at="2026-10-14T07:00:00")
    assert r.status_code == 422


def test_details_are_optional(client, booked_flight):
    r = book(client, booked_flight)
    assert r.status_code == 200, r.text
    assert r.json()["travellers"][0]["booking_details"] is None
