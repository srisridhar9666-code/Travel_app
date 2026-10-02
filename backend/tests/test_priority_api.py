"""
Request priority and the admin queue export.

The requester says how soon they need a decision; admins see high-priority work
first and can pull a whole queue tab into a spreadsheet. Priority rides on the
same edit window and revision trail as every other field, so it is tested
through the real routes rather than poked onto rows.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    RequestPriority,
    RequestType,
    Role,
    TicketStatus,
    TravellerStatus,
    UserStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import RequestRevision, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.services import notifications
from app.services import requests as svc

TENANT = "designboxed"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    # The after-response email send opens its own session on the real database;
    # what it sends is covered in test_submission_notices.
    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, password_hash="x")
    meena = User(tenant_id=TENANT, email="meena@designboxed.com", full_name="Meena Iyer",
                 role=Role.GROUND_STAFF, password_hash="x")
    db.add_all([project, admin, ravi, meena])
    db.commit()
    return project, admin, ravi, meena


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def hotel_body(project, **overrides):
    check_in = clock.local_today() + timedelta(days=10)
    body = dict(
        request_type="HOTEL",
        project_id=project.id,
        hotel_city="Mumbai",
        hotel_state="Maharashtra",
        check_in=check_in.isoformat(),
        check_out=(check_in + timedelta(days=2)).isoformat(),
        travel_reason="Store audit for the monsoon range",
    )
    body.update(overrides)
    return body


def make_row(db, project, requester, *, priority=RequestPriority.MEDIUM,
             traveller_status=TravellerStatus.PENDING, days_ahead=10, cost=None):
    """A submitted hotel request straight on the table, for the list tests."""
    check_in = clock.local_today() + timedelta(days=days_ahead)
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=project.id,
        requester_id=requester.id, hotel_city="Pune", hotel_state="Maharashtra",
        check_in=check_in, check_out=check_in + timedelta(days=1),
        travel_reason="Field visit", priority=priority,
        submitted_at=naive_utcnow(),
    )
    row.travellers = [RequestTraveller(user_id=requester.id, status=traveller_status,
                                       cost_amount=cost)]
    db.add(row)
    db.flush()
    return row


class TestTheRequesterSetsIt:
    def test_a_new_request_defaults_to_medium(self, client, world):
        project, _, ravi, _ = world
        res = client.post("/requests", json=hotel_body(project), headers=auth(ravi))
        assert res.status_code == 201, res.text
        assert res.json()["priority"] == "MEDIUM"

    def test_a_chosen_priority_is_stored_and_read_back(self, client, world):
        project, _, ravi, _ = world
        res = client.post("/requests", json=hotel_body(project, priority="HIGH"),
                          headers=auth(ravi))
        assert res.status_code == 201, res.text
        rid = res.json()["id"]
        assert client.get(f"/requests/{rid}", headers=auth(ravi)).json()["priority"] == "HIGH"

    def test_an_unknown_priority_is_a_validation_error(self, client, world):
        project, _, ravi, _ = world
        res = client.post("/requests", json=hotel_body(project, priority="URGENT"),
                          headers=auth(ravi))
        assert res.status_code == 422

    def test_changing_only_priority_writes_one_revision(self, client, db, world):
        project, _, ravi, _ = world
        rid = client.post("/requests", json=hotel_body(project), headers=auth(ravi)).json()["id"]

        res = client.put(f"/requests/{rid}", json=hotel_body(project, priority="HIGH"),
                         headers=auth(ravi))
        assert res.status_code == 200, res.text
        assert res.json()["priority"] == "HIGH"

        latest = db.execute(
            select(RequestRevision)
            .where(RequestRevision.request_id == rid)
            .order_by(RequestRevision.revision_number.desc())
        ).scalars().first()
        assert latest.revision_number == 2
        assert latest.summary == "Edited priority"
        assert latest.changes == {"priority": {"from": "MEDIUM", "to": "HIGH"}}

    def test_an_admin_cannot_change_someone_elses_priority(self, client, world):
        project, admin, ravi, _ = world
        # Headers first: the create closes the session, detaching these users.
        as_admin, body = auth(admin), hotel_body(project, priority="LOW")
        rid = client.post("/requests", json=hotel_body(project), headers=auth(ravi)).json()["id"]
        res = client.put(f"/requests/{rid}", json=body, headers=as_admin)
        assert res.status_code == 403

    def test_priority_is_locked_once_an_admin_has_acted(self, client, db, world):
        project, _, ravi, _ = world
        rid = client.post("/requests", json=hotel_body(project), headers=auth(ravi)).json()["id"]
        row = db.get(TravelRequest, rid)
        row.travellers[0].status = TravellerStatus.APPROVED
        db.commit()

        res = client.put(f"/requests/{rid}", json=hotel_body(project, priority="HIGH"),
                         headers=auth(ravi))
        assert res.status_code == 409
        db.refresh(row)
        assert row.priority is RequestPriority.MEDIUM


class TestTaggingSomeoneWhoHasLeft:
    def test_the_message_says_to_remove_them(self, client, db, world):
        project, _, ravi, meena = world
        meena.status = UserStatus.LEFT
        db.commit()
        res = client.post("/requests", json=hotel_body(project, traveller_ids=[meena.id]),
                          headers=auth(ravi))
        assert res.status_code == 400
        assert res.json()["detail"] == (
            "Meena Iyer has left or been deactivated - remove them from this request."
        )


class TestTheQueue:
    def test_sort_by_priority_puts_high_first_then_newest(self, client, db, world):
        project, admin, ravi, _ = world
        low = make_row(db, project, ravi, priority=RequestPriority.LOW)
        high_old = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        medium = make_row(db, project, ravi, priority=RequestPriority.MEDIUM)
        high_new = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        db.commit()

        res = client.get("/requests", params={"mine": "false", "sort": "priority"},
                         headers=auth(admin))
        assert res.status_code == 200, res.text
        assert [r["id"] for r in res.json()["items"]] == [
            high_new.id, high_old.id, medium.id, low.id,
        ]

        # The default order is untouched: newest first, whatever the priority.
        res = client.get("/requests", params={"mine": "false"}, headers=auth(admin))
        assert [r["id"] for r in res.json()["items"]] == [
            high_new.id, medium.id, high_old.id, low.id,
        ]

    def test_filter_by_priority(self, client, db, world):
        project, admin, ravi, _ = world
        make_row(db, project, ravi, priority=RequestPriority.LOW)
        high = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        db.commit()
        res = client.get("/requests", params={"mine": "false", "priority": "HIGH"},
                         headers=auth(admin))
        assert [r["id"] for r in res.json()["items"]] == [high.id]
        assert res.json()["items"][0]["priority"] == "HIGH"

    def test_high_priority_count_is_only_what_still_waits(self, client, db, world):
        project, admin, ravi, meena = world
        make_row(db, project, ravi, priority=RequestPriority.HIGH)                 # awaiting
        partly = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        partly.travellers.append(RequestTraveller(user_id=meena.id,
                                                  status=TravellerStatus.APPROVED))
        make_row(db, project, ravi, priority=RequestPriority.HIGH,
                 traveller_status=TravellerStatus.BOOKED)
        make_row(db, project, ravi, priority=RequestPriority.HIGH,
                 traveller_status=TravellerStatus.CANCELLED)
        make_row(db, project, ravi, priority=RequestPriority.HIGH, days_ahead=-5)   # expired
        make_row(db, project, ravi, priority=RequestPriority.MEDIUM)
        db.commit()

        res = client.get("/requests/queue/counts", headers=auth(admin))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["high_priority"] == 2
        assert body["awaiting"] == 2 and body["partially_approved"] == 1
        # Split by tab, so the banner can open the one the work is on.
        assert body["high_priority_awaiting"] == 1
        assert body["high_priority_partial"] == 1

    def test_partly_approved_urgent_work_is_counted_on_its_own_tab(self, client, db, world):
        project, admin, ravi, meena = world
        partly = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        partly.travellers.append(RequestTraveller(user_id=meena.id,
                                                  status=TravellerStatus.APPROVED))
        db.commit()

        body = client.get("/requests/queue/counts", headers=auth(admin)).json()
        assert (body["high_priority"], body["high_priority_awaiting"],
                body["high_priority_partial"]) == (1, 0, 1)
        res = client.get("/requests", headers=auth(admin), params={
            "mine": "false", "status": "PARTIALLY_APPROVED", "priority": "HIGH"})
        assert [r["id"] for r in res.json()["items"]] == [partly.id]

    def test_tab_counts_follow_the_priority_and_search(self, client, db, world):
        """The tab labels must agree with the filtered list under them."""
        project, admin, ravi, _ = world
        make_row(db, project, ravi, priority=RequestPriority.HIGH)
        make_row(db, project, ravi, priority=RequestPriority.LOW)
        make_row(db, project, ravi, priority=RequestPriority.HIGH,
                 traveller_status=TravellerStatus.BOOKED)
        make_row(db, project, ravi, priority=RequestPriority.LOW,
                 traveller_status=TravellerStatus.BOOKED)
        make_row(db, project, ravi, priority=RequestPriority.LOW,
                 traveller_status=TravellerStatus.BOOKED)
        db.commit()

        def counts(**params):
            res = client.get("/requests/queue/counts", params=params, headers=auth(admin))
            assert res.status_code == 200, res.text
            return res.json()

        everything = counts()
        assert (everything["awaiting"], everything["booked"]) == (2, 3)
        high = counts(priority="HIGH")
        assert (high["awaiting"], high["booked"]) == (1, 1)
        low = counts(priority="LOW")
        assert (low["awaiting"], low["booked"]) == (1, 2)
        assert counts(search="Pune")["booked"] == 3
        assert counts(search="Nowhere-at-all")["booked"] == 0

        # And each tab's count is the number of rows its list returns.
        for tab, key in (("SUBMITTED", "awaiting"), ("BOOKED", "booked")):
            listed = client.get("/requests", headers=auth(admin), params={
                "mine": "false", "status": tab, "priority": "LOW"}).json()["total"]
            assert listed == low[key]


class TestTicketOnTheRow:
    """The Booked tab opens a traveller's uploaded ticket straight from their
    row, so the list names which ticket that is."""

    def ticket(self, db, row, status, path="tickets/x.pdf"):
        ticket = TicketDocument(tenant_id=TENANT, request_id=row.id,
                                traveller_id=row.travellers[0].id, status=status,
                                file_path=path, file_name="x.pdf")
        db.add(ticket)
        db.flush()
        return ticket

    def test_the_confirmed_ticket_wins_and_staff_never_see_one(self, client, db, world):
        project, admin, ravi, _ = world
        row = make_row(db, project, ravi, traveller_status=TravellerStatus.BOOKED)
        confirmed = self.ticket(db, row, TicketStatus.CONFIRMED)
        self.ticket(db, row, TicketStatus.EXTRACTED)            # newer, still in review
        self.ticket(db, row, TicketStatus.DISCARDED, path=None)  # file deleted
        bare = make_row(db, project, ravi, traveller_status=TravellerStatus.BOOKED)
        db.commit()

        listed = client.get("/requests", headers=auth(admin), params={
            "mine": "false", "status": "BOOKED"}).json()["items"]
        by_id = {r["id"]: r["travellers"][0]["ticket_id"] for r in listed}
        assert by_id == {row.id: confirmed.id, bare.id: None}

        mine = client.get("/requests", headers=auth(ravi)).json()["items"]
        assert all(r["travellers"][0]["ticket_id"] is None for r in mine)

    def test_a_ticket_under_review_is_shown_when_none_is_confirmed(self, client, db, world):
        project, admin, ravi, _ = world
        row = make_row(db, project, ravi, traveller_status=TravellerStatus.APPROVED)
        older = self.ticket(db, row, TicketStatus.FAILED)
        newer = self.ticket(db, row, TicketStatus.EXTRACTED)
        db.commit()
        assert older.id < newer.id

        [item] = client.get("/requests", headers=auth(admin), params={
            "mine": "false", "status": "APPROVED"}).json()["items"]
        assert item["travellers"][0]["ticket_id"] == newer.id


class TestTheExport:
    def test_every_row_comes_back_with_cost(self, client, db, world):
        project, admin, ravi, _ = world
        for _ in range(105):
            make_row(db, project, ravi, traveller_status=TravellerStatus.BOOKED,
                     cost=Decimal("3300.00"))
        make_row(db, project, ravi)   # awaiting, so not in the booked tab
        db.commit()

        res = client.get("/requests/queue/export", params={"status": "BOOKED"},
                         headers=auth(admin))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "BOOKED"
        assert body["total"] == 105 and body["truncated"] is False
        assert len(body["items"]) == 105
        assert {r["status"] for r in body["items"]} == {"BOOKED"}
        assert body["items"][0]["travellers"][0]["cost_amount"] == "3300.00"

    def test_it_honours_search_and_priority_and_sorts_high_first(self, client, db, world):
        project, admin, ravi, _ = world
        medium = make_row(db, project, ravi)
        high = make_row(db, project, ravi, priority=RequestPriority.HIGH)
        make_row(db, project, ravi, priority=RequestPriority.LOW).hotel_city = "Chennai"
        db.commit()

        res = client.get("/requests/queue/export",
                         params={"status": "SUBMITTED", "search": "Pune"}, headers=auth(admin))
        assert [r["id"] for r in res.json()["items"]] == [high.id, medium.id]

        res = client.get("/requests/queue/export",
                         params={"status": "SUBMITTED", "priority": "HIGH"}, headers=auth(admin))
        assert [r["id"] for r in res.json()["items"]] == [high.id]

    def test_the_waiting_tabs_carry_clash_warnings(self, client, db, world):
        project, admin, ravi, _ = world
        make_row(db, project, ravi)
        make_row(db, project, ravi)   # same person, same city, same nights
        db.commit()
        res = client.get("/requests/queue/export", params={"status": "SUBMITTED"},
                         headers=auth(admin))
        assert all(r["conflicts"] for r in res.json()["items"])

    def test_it_is_capped_and_says_so(self, client, db, world, monkeypatch):
        project, admin, ravi, _ = world
        monkeypatch.setattr(svc, "MAX_EXPORT_ROWS", 2)
        for _ in range(3):
            make_row(db, project, ravi, traveller_status=TravellerStatus.BOOKED)
        db.commit()
        body = client.get("/requests/queue/export", params={"status": "BOOKED"},
                          headers=auth(admin)).json()
        assert body["total"] == 3 and body["truncated"] is True and len(body["items"]) == 2

    def test_ground_staff_cannot_export(self, client, world):
        _, _, ravi, _ = world
        res = client.get("/requests/queue/export", params={"status": "BOOKED"},
                         headers=auth(ravi))
        assert res.status_code == 403

    def test_a_tab_is_required(self, client, world):
        _, admin, _, _ = world
        assert client.get("/requests/queue/export", headers=auth(admin)).status_code == 422
