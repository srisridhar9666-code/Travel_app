"""
Room sharing from the form to the admin's allotment, and employees fetching
their own confirmed ticket.

* The hotel form hears who of the same gender is staying in a city as soon as
  the city is picked - with dates, only overlapping stays.
* The requester can ask to share right in the form; it stays an ask.
* An admin sees each hotel traveller's possible roommates and allots a room:
  both rows point at each other, confirmed, and both people are told - or gives
  a room of their own, undoing the pairing on both sides.
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    Gender,
    RequestType,
    Role,
    RoomSharingChoice,
    TicketStatus,
    TravellerStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.services import notifications, storage

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


def day(n):
    return clock.local_today() + timedelta(days=n)


@pytest.fixture
def org(db):
    def person(name, email, gender, role=Role.GROUND_STAFF):
        user = User(tenant_id=TENANT, email=email, full_name=name, role=role,
                    gender=gender, password_hash="x")
        db.add(user)
        return user

    admin = person("Priya Shah", "priya@designboxed.com", Gender.FEMALE, Role.ADMIN)
    ravi = person("Ravi Kumar", "ravi@designboxed.com", Gender.MALE)
    arjun = person("Arjun Nair", "arjun@designboxed.com", Gender.MALE)
    vikram = person("Vikram Rao", "vikram@designboxed.com", Gender.MALE)
    meena = person("Meena Iyer", "meena@designboxed.com", Gender.FEMALE)
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
    db.add(project)
    db.commit()
    refs = {k: Ref(u.id, u.role) for k, u in
            dict(admin=admin, ravi=ravi, arjun=arjun, vikram=vikram, meena=meena).items()}
    refs["project"] = project.id
    return refs


def stay(db, org, who, *, city="Pune", start=10, nights=3, status=TravellerStatus.PENDING):
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=org["project"],
        requester_id=org[who].id, hotel_city=city, hotel_state="Maharashtra",
        check_in=day(start), check_out=day(start + nights), travel_reason="Store audit",
        submitted_at=naive_utcnow(),
    )
    row.travellers = [RequestTraveller(user_id=org[who].id, status=status)]
    db.add(row)
    db.commit()
    return row.id, row.travellers[0].id


def hotel_body(org, **extra):
    body = dict(
        request_type="HOTEL", project_id=org["project"], hotel_city="Pune",
        hotel_state="Maharashtra", check_in=day(11).isoformat(), check_out=day(13).isoformat(),
        travel_reason="Store audits in Pune",
    )
    body.update(extra)
    return body


class TestTheFormHearsByCity:
    def test_a_city_alone_lists_upcoming_same_gender_stays(self, client, db, org):
        stay(db, org, "arjun", start=10)
        stay(db, org, "meena", start=10)                 # other gender: never listed
        stay(db, org, "vikram", city="Mumbai", start=10) # other city
        r = client.get("/requests/room-matches", headers=auth(org["ravi"]), params={"city": "pune"})
        assert r.status_code == 200, r.text
        assert [m["full_name"] for m in r.json()] == ["Arjun Nair"]
        assert r.json()[0]["overlapping_nights"] == 0

    def test_with_dates_only_overlapping_stays(self, client, db, org):
        stay(db, org, "arjun", start=10, nights=3)
        stay(db, org, "vikram", start=30, nights=2)
        r = client.get("/requests/room-matches", headers=auth(org["ravi"]),
                       params={"city": "Pune", "check_in": day(11).isoformat(),
                               "check_out": day(13).isoformat()})
        assert [(m["full_name"], m["overlapping_nights"]) for m in r.json()] == [("Arjun Nair", 2)]

    def test_a_past_stay_is_not_listed(self, client, db, org):
        stay(db, org, "arjun", start=-10, nights=2)
        r = client.get("/requests/room-matches", headers=auth(org["ravi"]), params={"city": "Pune"})
        assert r.json() == []


class TestChoosingInTheForm:
    def test_asking_to_share_is_saved_and_the_colleague_told(self, client, db, org):
        stay(db, org, "arjun", start=10)
        r = client.post("/requests", headers=auth(org["ravi"]),
                        json=hotel_body(org, room_sharing="SHARE_EXISTING",
                                        share_with_user_id=org["arjun"].id))
        assert r.status_code == 201, r.text
        mine = r.json()["travellers"][0]
        assert (mine["room_sharing"], mine["share_with_user_id"], mine["share_confirmed"]) == (
            "SHARE_EXISTING", org["arjun"].id, False)
        told = db.execute(select(Notification).where(
            Notification.user_id == org["arjun"].id, Notification.kind == "COSTAY_REQUESTED"
        )).scalars().first()
        assert told is not None

    def test_only_a_colleague_actually_offered(self, client, db, org):
        stay(db, org, "meena", start=10)  # there, but another gender
        r = client.post("/requests", headers=auth(org["ravi"]),
                        json=hotel_body(org, room_sharing="SHARE_EXISTING",
                                        share_with_user_id=org["meena"].id))
        assert r.status_code == 422

    def test_a_room_choice_on_a_flight_is_refused(self, client, db, org):
        body = dict(request_type="LONG_DISTANCE", mode="FLIGHT", project_id=org["project"],
                    origin="Hyderabad", origin_state="Telangana", destination="Pune",
                    destination_state="Maharashtra",
                    start_at=f"{day(11).isoformat()}T07:00:00", travel_reason="Audit",
                    room_sharing="SEPARATE_ROOM")
        assert client.post("/requests", headers=auth(org["ravi"]), json=body).status_code == 422

    def test_a_draft_tells_the_colleague_only_when_submitted(self, client, db, org):
        stay(db, org, "arjun", start=10)
        r = client.post("/requests", headers=auth(org["ravi"]),
                        json=hotel_body(org, is_draft=True, room_sharing="SHARE_EXISTING",
                                        share_with_user_id=org["arjun"].id))
        assert r.status_code == 201, r.text
        def told():
            return db.execute(select(Notification).where(
                Notification.user_id == org["arjun"].id, Notification.kind == "COSTAY_REQUESTED"
            )).scalars().all()
        assert told() == []
        assert client.post(f"/requests/{r.json()['id']}/submit", headers=auth(org["ravi"])).status_code == 200
        assert len(told()) >= 1


class TestTheAdminAllots:
    def test_the_queue_shows_who_could_share(self, client, db, org):
        stay(db, org, "arjun", start=10)
        mine, _ = stay(db, org, "ravi", start=11, nights=2)
        r = client.get(f"/requests/{mine}", headers=auth(org["admin"]))
        matches = r.json()["travellers"][0]["room_matches"]
        assert [m["full_name"] for m in matches] == ["Arjun Nair"]
        # Not for the employee.
        r = client.get(f"/requests/{mine}", headers=auth(org["ravi"]))
        assert r.json()["travellers"][0]["room_matches"] == []

    def test_allotting_pairs_both_sides_confirmed_and_tells_both(self, client, db, org):
        theirs, their_row = stay(db, org, "arjun", start=10)
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                        json={"share_with_user_id": org["arjun"].id})
        assert r.status_code == 200, r.text
        me = r.json()["travellers"][0]
        assert (me["room_sharing"], me["share_with_name"], me["share_confirmed"]) == (
            "SHARE_EXISTING", "Arjun Nair", True)
        db.expire_all()
        other = db.get(RequestTraveller, their_row)
        assert other.room_sharing is RoomSharingChoice.SHARE_EXISTING
        assert other.share_with_user_id == org["ravi"].id and other.share_confirmed_at is not None
        told = {n.user_id for n in db.execute(select(Notification).where(
            Notification.kind == "COSTAY_CONFIRMED", Notification.channel == "IN_APP"
        )).scalars()}
        assert told == {org["ravi"].id, org["arjun"].id}

    def test_a_room_of_their_own_undoes_both_sides(self, client, db, org):
        theirs, their_row = stay(db, org, "arjun", start=10)
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                    json={"share_with_user_id": org["arjun"].id})
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                        json={"share_with_user_id": None})
        assert r.status_code == 200, r.text
        assert r.json()["travellers"][0]["room_sharing"] == "SEPARATE_ROOM"
        db.expire_all()
        other = db.get(RequestTraveller, their_row)
        assert other.room_sharing is RoomSharingChoice.SEPARATE_ROOM
        assert other.share_with_user_id is None

    def test_different_genders_are_refused(self, client, db, org):
        stay(db, org, "meena", start=10)
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                        json={"share_with_user_id": org["meena"].id})
        assert r.status_code == 400

    def test_no_overlapping_nights_is_refused(self, client, db, org):
        stay(db, org, "arjun", start=30)
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                        json={"share_with_user_id": org["arjun"].id})
        assert r.status_code == 409

    def test_someone_already_sharing_with_another_is_refused(self, client, db, org):
        theirs, their_row = stay(db, org, "arjun", start=10)
        vikram, vikram_row = stay(db, org, "vikram", start=10)
        client.post(f"/requests/{vikram}/travellers/{vikram_row}/room", headers=auth(org["admin"]),
                    json={"share_with_user_id": org["arjun"].id})
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["admin"]),
                        json={"share_with_user_id": org["arjun"].id})
        assert r.status_code == 409
        assert "Vikram Rao" in r.json()["detail"]

    def test_only_admins_allot(self, client, db, org):
        stay(db, org, "arjun", start=10)
        mine, my_row = stay(db, org, "ravi", start=11, nights=2)
        r = client.post(f"/requests/{mine}/travellers/{my_row}/room", headers=auth(org["ravi"]),
                        json={"share_with_user_id": org["arjun"].id})
        assert r.status_code == 403


class TestMyTicket:
    def ticket(self, db, request_id, traveller_id, status, monkeypatch):
        monkeypatch.setattr(storage, "read", lambda path: b"%PDF-1.4 ticket")
        db.add(TicketDocument(tenant_id=TENANT, request_id=request_id, traveller_id=traveller_id,
                              status=status, file_path="tickets/x.pdf", file_name="ticket.pdf",
                              content_type="application/pdf"))
        db.commit()

    def test_the_traveller_downloads_their_confirmed_ticket(self, client, db, org, monkeypatch):
        mine, my_row = stay(db, org, "ravi", start=11, status=TravellerStatus.BOOKED)
        self.ticket(db, mine, my_row, TicketStatus.CONFIRMED, monkeypatch)
        r = client.get(f"/requests/{mine}", headers=auth(org["ravi"]))
        assert r.json()["travellers"][0]["ticket_ready"] is True
        r = client.get(f"/requests/{mine}/travellers/{my_row}/ticket", headers=auth(org["ravi"]))
        assert r.status_code == 200 and r.content.startswith(b"%PDF")

    def test_not_before_it_is_confirmed_and_not_for_others(self, client, db, org, monkeypatch):
        mine, my_row = stay(db, org, "ravi", start=11, status=TravellerStatus.BOOKED)
        self.ticket(db, mine, my_row, TicketStatus.EXTRACTED, monkeypatch)
        assert client.get(f"/requests/{mine}/travellers/{my_row}/ticket",
                          headers=auth(org["ravi"])).status_code == 404
        self.ticket(db, mine, my_row, TicketStatus.CONFIRMED, monkeypatch)
        assert client.get(f"/requests/{mine}/travellers/{my_row}/ticket",
                          headers=auth(org["arjun"])).status_code == 404
        assert client.get(f"/requests/{mine}/travellers/{my_row}/ticket",
                          headers=auth(org["admin"])).status_code == 200
