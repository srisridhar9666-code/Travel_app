"""
Employee status: Active, Deactivated, Left, Deleted - and what each does to
signing in.

Only Active may sign in. The tests below are mostly about the ways a switched-off
person might still get in - an old token, an unused invite link, a reset email -
and about who may flip the switch on whom.
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    AuditAction,
    Gender,
    NotificationChannel,
    RequestType,
    Role,
    TokenPurpose,
    TravellerStatus,
    UserStatus,
)
from app.core.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.auth_token import AuthToken
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.routers.auth import _issue_token
from app.services import accounts, locations, notifications

TENANT = "designboxed"
PASSWORD = "Monsoon-Ledger-42"
HASH = hash_password(PASSWORD)


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


def person(db, name, email, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email, full_name=name, role=role,
                gender=Gender.MALE, password_hash=HASH, **extra)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def people(db):
    boss = person(db, "Sridhar Rao", "sridhar@designboxed.com", Role.SYSTEM_ADMIN)
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com")
    return boss, admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def login(client, email, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def set_status(client, actor, user, status, **body):
    return client.post(f"/users/{user.id}/status", headers=auth(actor),
                       json={"status": status, **body})


class TestSigningIn:
    @pytest.mark.parametrize("state, words", [
        (UserStatus.DEACTIVATED, "Your account is deactivated."),
        (UserStatus.LEFT, "marked as left the organisation"),
        (UserStatus.DELETED, "This account has been removed."),
    ])
    def test_a_switched_off_account_is_told_why_after_the_password(
        self, client, db, people, state, words
    ):
        _, _, ravi = people
        ravi.status = state
        db.commit()

        r = login(client, ravi.email)
        assert r.status_code == 403
        assert words in r.json()["detail"]

        # Without the password, nothing about the account is given away.
        r = login(client, ravi.email, "wrong-password-1")
        assert r.status_code == 401
        assert r.json()["detail"] == "Incorrect email or password."

    def test_an_open_session_ends_on_the_next_request(self, client, db, people):
        boss, _, ravi = people
        headers = auth(ravi)
        assert client.get("/auth/me", headers=headers).status_code == 200

        assert set_status(client, boss, ravi, "DEACTIVATED").status_code == 200

        r = client.get("/auth/me", headers=headers)
        # 401, not 403: the web signs out on a 401 and shows this message.
        assert r.status_code == 401
        assert "deactivated" in r.json()["detail"]

        assert set_status(client, boss, ravi, "ACTIVE").status_code == 200
        assert login(client, ravi.email).status_code == 200

    def test_a_row_switched_off_the_old_way_is_still_blocked(self, client, db, people):
        _, _, ravi = people
        headers = auth(ravi)
        ravi.is_active = False  # older code and tests set the flag directly
        db.commit()
        r = client.get("/auth/me", headers=headers)
        assert r.status_code == 401
        assert "deactivated" in r.json()["detail"]

    def test_is_active_mirrors_status(self, db, people):
        _, _, ravi = people
        for state in UserStatus:
            ravi.status = state
            assert ravi.is_active is (state is UserStatus.ACTIVE)


class TestLinks:
    def test_a_status_change_spends_outstanding_links(self, client, db, people):
        boss, _, ravi = people
        r = client.post(f"/users/{ravi.id}/reinvite", headers=auth(boss))
        assert r.status_code == 200
        assert r.json()["purpose"] == "PASSWORD_RESET"
        raw = r.json()["invite_url"].split("token=")[1]
        assert client.get(f"/auth/token/{raw}").status_code == 200

        assert set_status(client, boss, ravi, "LEFT").status_code == 200

        assert client.get(f"/auth/token/{raw}").status_code == 404
        r = client.post("/auth/set-password", json={"token": raw, "password": "Brand-new-sky-77"})
        assert r.status_code == 404
        unused = db.execute(
            select(AuthToken).where(AuthToken.user_id == ravi.id, AuthToken.used_at.is_(None))
        ).scalars().all()
        assert unused == []

    def test_a_link_for_a_left_account_is_refused(self, client, db, people):
        _, _, ravi = people
        ravi.status = UserStatus.LEFT
        raw, _ = _issue_token(db, ravi, TokenPurpose.PASSWORD_RESET, valid_hours=2)
        db.commit()
        assert client.get(f"/auth/token/{raw}").status_code == 404
        r = client.post("/auth/set-password", json={"token": raw, "password": "Brand-new-sky-77"})
        assert r.status_code == 404

    def test_forgot_password_stays_silent_for_a_left_account(self, client, db, people):
        _, _, ravi = people
        ravi.status = UserStatus.LEFT
        db.commit()
        r = client.post("/auth/forgot-password", json={"email": ravi.email})
        assert r.status_code == 200
        assert "If that address belongs to an account" in r.json()["detail"]
        assert db.execute(select(AuthToken).where(AuthToken.user_id == ravi.id)).first() is None

    def test_reinvite_needs_an_active_account(self, client, db, people):
        boss, _, ravi = people
        ravi.status = UserStatus.DEACTIVATED
        db.commit()
        r = client.post(f"/users/{ravi.id}/reinvite", headers=auth(boss))
        assert r.status_code == 400
        assert "status to Active" in r.json()["detail"]


class TestWhoMayChangeWhom:
    def test_nobody_changes_their_own_status(self, client, people):
        boss, admin, _ = people
        r = set_status(client, admin, admin, "DEACTIVATED")
        assert r.status_code == 400
        assert set_status(client, boss, boss, "LEFT").status_code == 400

    def test_ground_staff_cannot_change_anyones_status(self, client, people):
        _, admin, ravi = people
        assert set_status(client, ravi, admin, "DEACTIVATED").status_code == 403

    def test_an_admin_cannot_touch_a_system_admin(self, client, db, people):
        boss, admin, _ = people
        headers = auth(admin)
        expected = "Only a system administrator can change another system administrator's account."

        r = set_status(client, admin, boss, "DEACTIVATED")
        assert (r.status_code, r.json()["detail"]) == (403, expected)
        assert client.patch(f"/users/{boss.id}", headers=headers,
                            json={"role": "GROUND_STAFF"}).status_code == 403
        assert client.patch(f"/users/{boss.id}", headers=headers,
                            json={"phone": "+91 98765 43210"}).status_code == 403
        # The worst one: the response carries a reset link for the account.
        assert client.post(f"/users/{boss.id}/reinvite", headers=headers).status_code == 403
        assert client.post(f"/users/{boss.id}/unlock", headers=headers).status_code == 403

        db.refresh(boss)
        assert boss.status is UserStatus.ACTIVE and boss.role is Role.SYSTEM_ADMIN

    def test_an_admin_may_manage_other_admins_and_staff(self, client, db, people):
        _, admin, ravi = people
        other = person(db, "Kiran Rao", "kiran@designboxed.com", Role.ADMIN)
        assert set_status(client, admin, other, "DEACTIVATED").status_code == 200
        assert set_status(client, admin, ravi, "DEACTIVATED").status_code == 200

    def test_a_system_admin_may_manage_another(self, client, db, people):
        boss, _, _ = people
        second = person(db, "Meena Iyer", "meena@designboxed.com", Role.SYSTEM_ADMIN)
        headers = auth(boss)
        assert client.patch(f"/users/{second.id}", headers=headers,
                            json={"phone": "+91 98765 43210"}).status_code == 200
        assert client.post(f"/users/{second.id}/reinvite", headers=headers).status_code == 200
        assert set_status(client, boss, second, "DEACTIVATED").status_code == 200

    def test_the_last_active_system_admin_is_kept(self, db, people):
        boss, _, _ = people
        with pytest.raises(HTTPException) as caught:
            accounts.assert_keeps_a_system_admin(db, boss, new_status=UserStatus.LEFT)
        assert caught.value.status_code == 409
        with pytest.raises(HTTPException):
            accounts.assert_keeps_a_system_admin(db, boss, new_role=Role.ADMIN)

        # With a second one active, either change is fine.
        person(db, "Meena Iyer", "meena@designboxed.com", Role.SYSTEM_ADMIN)
        accounts.assert_keeps_a_system_admin(db, boss, new_status=UserStatus.LEFT)
        accounts.assert_keeps_a_system_admin(db, boss, new_role=Role.ADMIN)


class TestTransitions:
    def test_left_defaults_the_exit_date_to_today_in_india(self, client, db, people):
        boss, _, ravi = people
        r = set_status(client, boss, ravi, "LEFT", reason="Moved to Pune office")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "LEFT"
        assert body["exited_on"] == clock.local_today().isoformat()
        assert body["is_active"] is False
        assert body["status_changed_at"] is not None

        row = db.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == "user", AuditLog.entity_id == ravi.id)
            .order_by(AuditLog.id.desc())
        ).scalars().first()
        assert row.action is AuditAction.UPDATE
        assert row.changes["status"] == {"from": "ACTIVE", "to": "LEFT"}
        assert row.reason == "Moved to Pune office"
        assert "from active to left" in row.summary

    def test_left_takes_a_past_exit_date_but_not_a_future_one(self, client, people):
        boss, _, ravi = people
        future = clock.local_today() + timedelta(days=1)
        r = set_status(client, boss, ravi, "LEFT", exited_on=future.isoformat())
        assert r.status_code == 422

        past = clock.local_today() - timedelta(days=10)
        r = set_status(client, boss, ravi, "LEFT", exited_on=past.isoformat())
        assert r.json()["exited_on"] == past.isoformat()

    def test_an_exit_date_does_not_go_with_deactivation(self, client, people):
        boss, _, ravi = people
        r = set_status(client, boss, ravi, "DEACTIVATED",
                       exited_on=clock.local_today().isoformat())
        assert r.status_code == 422

    def test_deactivation_leaves_the_exit_date_alone(self, client, people):
        """A suspension is not a departure; retention must not start."""
        boss, _, ravi = people
        r = set_status(client, boss, ravi, "DEACTIVATED")
        assert r.json()["exited_on"] is None

    def test_deleted_fills_the_exit_date_and_active_clears_it(self, client, people):
        boss, _, ravi = people
        r = set_status(client, boss, ravi, "DELETED")
        assert r.json()["exited_on"] == clock.local_today().isoformat()
        r = set_status(client, boss, ravi, "ACTIVE")
        assert r.json()["exited_on"] is None
        assert r.json()["is_active"] is True

    def test_the_same_status_again_records_nothing(self, client, db, people):
        boss, _, ravi = people
        before = db.execute(select(AuditLog.id)).all()
        assert set_status(client, boss, ravi, "ACTIVE").status_code == 200
        assert db.execute(select(AuditLog.id)).all() == before

    def test_status_is_not_editable_through_patch(self, client, people):
        boss, _, ravi = people
        for body in ({"is_active": False}, {"exited_on": "2026-01-01"}, {"status": "LEFT"}):
            r = client.patch(f"/users/{ravi.id}", headers=auth(boss), json=body)
            assert r.status_code == 422, body


class TestTheList:
    def test_deleted_people_are_hidden_unless_asked_for(self, client, db, people):
        boss, _, ravi = people
        person(db, "Anita Desai", "anita@designboxed.com", status=UserStatus.LEFT)
        ravi.status = UserStatus.DELETED
        db.commit()

        emails = {u["email"] for u in client.get("/users", headers=auth(boss)).json()["items"]}
        assert "ravi@designboxed.com" not in emails
        assert "anita@designboxed.com" in emails  # left people stay findable

        r = client.get("/users", headers=auth(boss), params={"status": "DELETED"})
        assert [u["email"] for u in r.json()["items"]] == ["ravi@designboxed.com"]

    def test_a_deleted_accounts_email_points_at_restoring_it(self, client, db, people):
        boss, _, ravi = people
        ravi.status = UserStatus.DELETED
        db.commit()
        r = client.post("/users", headers=auth(boss), json={
            "email": "Ravi@DesignBoxed.com", "full_name": "Ravi Kumar", "gender": "MALE",
        })
        assert r.status_code == 409
        assert "restore it from the team list" in r.json()["detail"]

    def test_left_and_deactivated_people_cannot_be_tagged(self, client, db, people):
        _, admin, ravi = people
        person(db, "Anita Desai", "anita@designboxed.com", status=UserStatus.LEFT)
        person(db, "Kiran Rao", "kiran@designboxed.com", status=UserStatus.DEACTIVATED)
        names = {c["full_name"] for c in client.get("/requests/colleagues", headers=auth(ravi)).json()}
        assert names == {"Sridhar Rao", "Priya Shah"}


def make_trip(db, project, user, *, days_out, status, draft=False, cancelled=False):
    start = clock.local_today() + timedelta(days=days_out)
    trip = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=project.id,
        requester_id=user.id, hotel_city="Mumbai", check_in=start,
        check_out=start + timedelta(days=1), is_draft=draft, is_cancelled=cancelled,
        submitted_at=naive_utcnow(),
    )
    trip.travellers = [RequestTraveller(user_id=user.id, status=status)]
    db.add(trip)
    db.commit()


class TestOpenTrips:
    def test_counts_live_trips_that_are_not_over(self, client, db, people):
        boss, _, ravi = people
        project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
        db.add(project)
        db.commit()

        make_trip(db, project, ravi, days_out=3, status=TravellerStatus.PENDING)
        make_trip(db, project, ravi, days_out=0, status=TravellerStatus.APPROVED)
        make_trip(db, project, ravi, days_out=5, status=TravellerStatus.BOOKED)
        # None of these count.
        make_trip(db, project, ravi, days_out=-5, status=TravellerStatus.BOOKED)  # over
        make_trip(db, project, ravi, days_out=3, status=TravellerStatus.PENDING, draft=True)
        make_trip(db, project, ravi, days_out=3, status=TravellerStatus.PENDING, cancelled=True)
        make_trip(db, project, ravi, days_out=3, status=TravellerStatus.REJECTED)

        r = client.get(f"/users/{ravi.id}/open-trips", headers=auth(boss))
        assert r.status_code == 200
        assert r.json() == {"pending": 1, "approved": 1, "booked": 1, "total": 3}


class TestNotices:
    def test_no_email_for_someone_switched_off(self, db, people):
        _, _, ravi = people
        ravi.status = UserStatus.LEFT
        db.commit()
        rows = notifications.notify(db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED",
                                    title="Approved", body="Your trip was approved.")
        assert [row.channel for row in rows] == [NotificationChannel.IN_APP]
        assert db.execute(
            select(Notification).where(Notification.user_id == ravi.id,
                                       Notification.channel == NotificationChannel.EMAIL)
        ).first() is None


class TestGenderAndPlaces:
    def _create(self, client, admin, **body):
        payload = {"email": "anita@designboxed.com", "full_name": "Anita Desai", **body}
        return client.post("/users", headers=auth(admin), json=payload)

    def test_gender_is_required_on_create(self, client, people):
        boss, _, _ = people
        assert self._create(client, boss).status_code == 422

    @pytest.mark.parametrize("gender", ["UNDISCLOSED", "OTHER"])
    def test_only_male_or_female_can_be_chosen(self, client, people, gender):
        boss, _, _ = people
        r = self._create(client, boss, gender=gender)
        assert r.status_code == 422
        assert "Choose Male or Female." in r.json()["detail"][0]["msg"]

    def test_a_legacy_gender_survives_an_edit_that_does_not_touch_it(self, client, db, people):
        boss, _, ravi = people
        ravi.gender = Gender.UNDISCLOSED
        db.commit()
        headers = auth(boss)
        r = client.patch(f"/users/{ravi.id}", headers=headers, json={"phone": "9876543210"})
        assert r.status_code == 200
        assert r.json()["gender"] == "UNDISCLOSED"
        r = client.patch(f"/users/{ravi.id}", headers=headers, json={"gender": "UNDISCLOSED"})
        assert r.status_code == 422
        assert client.patch(f"/users/{ravi.id}", headers=headers,
                            json={"gender": "FEMALE"}).json()["gender"] == "FEMALE"

    def test_base_place_is_matched_to_the_list_and_searchable(self, client, db, people):
        boss, _, _ = people
        locations.seed(db, TENANT)
        db.commit()
        r = self._create(client, boss, gender="FEMALE", base_state="telangana", base_location="hyd")
        assert r.status_code == 201
        found = client.get("/users", headers=auth(boss), params={"search": "Hyderabad"}).json()
        assert [(u["base_state"], u["base_location"]) for u in found["items"]] == [
            ("Telangana", "Hyderabad")
        ]

    def test_a_null_on_a_required_field_is_a_422_not_a_500(self, client, people):
        boss, _, ravi = people
        for body in ({"full_name": None}, {"gender": None}, {"email": None}, {"role": None}):
            r = client.patch(f"/users/{ravi.id}", headers=auth(boss), json=body)
            assert r.status_code == 422, body

    def test_phone_is_checked_on_create(self, client, people):
        boss, _, _ = people
        assert self._create(client, boss, gender="MALE", phone="12345").status_code == 422
        r = self._create(client, boss, gender="MALE", phone=" +91 98765  43210 ")
        assert r.status_code == 201
