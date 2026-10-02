"""
Campaigns: the optional code, the place, the dates, delete, and the built-in
"Other" campaign the request form cannot do without.

Admins found the mandatory code confusing, so a blank one is made from the
name. Delete exists only for a campaign nothing points at; everything else is
archived, and the Other campaign is neither.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import AuditAction, ProjectStatus, RequestType, Role
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import TravelRequest
from app.models.user import User
from app.services import locations
from app.services.projects import code_base, unique_code
from app.services.seed import OTHER_PROJECT_CODE, ensure_other_project

TENANT = "designboxed"
YY = f"{clock.local_today().year % 100:02d}"


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


@pytest.fixture
def rival_row(engine):
    """Commit a campaign from another connection, as a second admin would.

    Requested before `client`, so it is cleaned up after the test's own
    transaction has rolled back and released its locks.
    """
    made = []

    def commit(**values):
        with engine.begin() as conn:
            conn.execute(Project.__table__.insert().values(
                tenant_id=TENANT, status=ProjectStatus.ACTIVE.value, **values
            ))
        made.append(values["code"])

    yield commit
    if made:
        with engine.begin() as conn:
            conn.execute(Project.__table__.delete().where(
                Project.tenant_id == TENANT, Project.code.in_(made)
            ))


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def make(db, name, code, **extra):
    project = Project(tenant_id=TENANT, name=name, code=code, **extra)
    db.add(project)
    db.commit()
    return project


def draft_request(db, project, requester):
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=RequestType.LONG_DISTANCE,
        project_id=project.id,
        requester_id=requester.id,
        is_draft=True,
        origin="Hyderabad",
        destination="Mumbai",
        start_at=naive_utcnow().replace(microsecond=0) + timedelta(days=5),
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Making a code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("Monsoon Retail Audit", "MRA-26"),
    ("Survey of the Godavari Districts", "SGD-26"),
    ("Elections", "ELEC-26"),
    ("తెలంగాణ సర్వే", "CMP-26"),
    ("Mahé Coastal Audit", "MCA-26"),
    ("The Of", "TO-26"),
    ("Rural Health Survey Phase Two Extra", "RHSP-26"),
    ("2026 Polls", "POLL-26"),
])
def test_code_base_is_initials_and_two_digit_year(name, expected):
    assert code_base(name, 2026) == expected


def test_unique_code_counts_up_and_never_hands_out_other(db):
    make(db, "Monsoon Retail Audit", "mra-26")
    assert unique_code(db, TENANT, "MRA-26") == "MRA-26-2"
    make(db, "Monsoon Retail Audit again", "MRA-26-2")
    assert unique_code(db, TENANT, "MRA-26") == "MRA-26-3"
    assert unique_code(db, TENANT, OTHER_PROJECT_CODE) == f"{OTHER_PROJECT_CODE}-2"


class TestCreate:
    def test_a_blank_or_missing_code_is_made_for_you(self, client, people):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={"name": "Monsoon Retail Audit"})
        assert r.status_code == 201, r.text
        assert r.json()["code"] == f"MRA-{YY}"

        r = client.post(
            "/projects", headers=auth(admin), json={"name": "Monsoon Retail Audit", "code": "  "}
        )
        assert r.status_code == 201, r.text
        assert r.json()["code"] == f"MRA-{YY}-2"

    def test_losing_the_race_for_a_code_takes_the_next_one(
        self, rival_row, client, people, monkeypatch
    ):
        # Another admin commits MRA-YY after this request's snapshot was taken
        # (the auth lookup), so the code SELECT cannot see it but the INSERT
        # hits the unique index.
        admin, _ = people
        real_canonical = locations.canonical

        def commit_a_rival(*args, **kwargs):
            rival_row(name="Monsoon Retail Audit", code=f"MRA-{YY}")
            return real_canonical(*args, **kwargs)

        monkeypatch.setattr(locations, "canonical", commit_a_rival)
        r = client.post("/projects", headers=auth(admin), json={"name": "Monsoon Retail Audit"})
        assert r.status_code == 201, r.text
        assert r.json()["code"] == f"MRA-{YY}-2"

    def test_the_year_comes_from_the_start_date(self, client, people):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={
            "name": "Rural Health Survey", "start_date": "2027-03-01",
        })
        assert r.status_code == 201, r.text
        assert r.json()["code"] == "RHS-27"

    def test_a_typed_code_is_tidied_and_checked(self, client, people, db):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={"name": "Alpha", "code": "ab cd"})
        assert r.status_code == 201
        assert r.json()["code"] == "AB-CD"

        r = client.post("/projects", headers=auth(admin), json={"name": "Beta", "code": "x"})
        assert r.status_code == 422
        assert "at least 2 characters" in r.text

        r = client.post("/projects", headers=auth(admin), json={"name": "Gamma", "code": "ab-cd"})
        assert r.status_code == 409
        assert "Alpha" in r.json()["detail"]

        r = client.post("/projects", headers=auth(admin), json={"name": "Delta", "code": "other"})
        assert r.status_code == 409

    def test_no_dates_and_no_place_are_fine(self, client, people):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={"name": "Open Ended"})
        assert r.status_code == 201
        body = r.json()
        assert body["start_date"] is None and body["end_date"] is None
        assert body["state"] is None and body["city"] is None
        assert body["request_count"] == 0 and body["is_fallback"] is False

    def test_place_spelling_is_cleaned(self, client, people, db):
        admin, _ = people
        locations.seed(db, TENANT)
        db.commit()
        r = client.post("/projects", headers=auth(admin), json={
            "name": "City Audit", "state": "telangana", "city": "hyd",
        })
        assert r.status_code == 201, r.text
        assert (r.json()["state"], r.json()["city"]) == ("Telangana", "Hyderabad")

        r = client.post("/projects", headers=auth(admin), json={
            "name": "State Audit", "state": "Kerala",
        })
        assert (r.json()["state"], r.json()["city"]) == ("Kerala", None)

    def test_end_before_start_is_refused_in_plain_words(self, client, people):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={
            "name": "Backwards", "start_date": "2026-05-01", "end_date": "2026-01-01",
        })
        assert r.status_code == 422
        assert "End date cannot be before the start date" in r.text

    def test_creating_is_audited(self, client, people, db):
        admin, _ = people
        r = client.post("/projects", headers=auth(admin), json={"name": "Logged One"})
        entry = db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "project", AuditLog.entity_id == r.json()["id"]
            )
        ).scalar_one()
        assert entry.action is AuditAction.CREATE
        assert entry.changes["code"]["to"] == f"LO-{YY}"


class TestUpdate:
    def test_a_blank_code_is_remade_without_clashing_with_itself(self, client, people, db):
        admin, _ = people
        project = make(db, "Monsoon Retail Audit", "MANUAL-1", start_date=date(2025, 6, 1))
        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"code": None})
        assert r.status_code == 200, r.text
        assert r.json()["code"] == "MRA-25"

        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"code": ""})
        assert r.json()["code"] == "MRA-25"

        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"name": "Renamed"})
        assert r.json()["code"] == "MRA-25"

    def test_picking_a_state_clears_the_old_free_text(self, client, people, db):
        admin, _ = people
        locations.seed(db, TENANT)
        project = make(db, "Legacy", "LEG-1", location="somewhere near Pune")

        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"name": "Legacy 2"})
        assert r.json()["location"] == "somewhere near Pune"

        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={
            "state": "Maharashtra", "city": "Pune",
        })
        assert r.status_code == 200
        body = r.json()
        assert (body["state"], body["city"], body["location"]) == ("Maharashtra", "Pune", None)

        # Clearing the city alone keeps the state.
        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"city": None})
        assert (r.json()["state"], r.json()["city"]) == ("Maharashtra", None)

    def test_end_before_the_stored_start_is_refused(self, client, people, db):
        admin, _ = people
        project = make(db, "Dated", "DAT-1", start_date=date(2026, 5, 1))
        r = client.patch(
            f"/projects/{project.id}", headers=auth(admin), json={"end_date": "2026-01-01"}
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "End date cannot be before the start date."
        db.refresh(project)
        assert project.end_date is None

    def test_a_null_name_is_a_422_not_a_500(self, client, people, db):
        admin, _ = people
        project = make(db, "Named", "NAM-1")
        r = client.patch(f"/projects/{project.id}", headers=auth(admin), json={"name": None})
        assert r.status_code == 422

    def test_a_typed_duplicate_code_is_refused(self, client, people, db):
        admin, _ = people
        make(db, "First", "FIR-1")
        second = make(db, "Second", "SEC-1")
        r = client.patch(f"/projects/{second.id}", headers=auth(admin), json={"code": "fir-1"})
        assert r.status_code == 409
        assert "First" in r.json()["detail"]


class TestTheOtherCampaign:
    @pytest.fixture
    def other(self, db):
        project = ensure_other_project(db, TENANT)
        db.commit()
        return project

    def test_it_cannot_be_put_away_recoded_or_deleted(self, client, people, other):
        admin, _ = people
        h = auth(admin)
        assert client.patch(f"/projects/{other.id}", headers=h,
                            json={"status": "ARCHIVED"}).status_code == 409
        assert client.patch(f"/projects/{other.id}", headers=h,
                            json={"status": "PAUSED"}).status_code == 409
        assert client.patch(f"/projects/{other.id}", headers=h,
                            json={"code": "X1"}).status_code == 409
        assert client.patch(f"/projects/{other.id}", headers=h,
                            json={"code": None}).status_code == 409
        assert client.post(f"/projects/{other.id}/archive", headers=h).status_code == 409
        assert client.delete(f"/projects/{other.id}", headers=h).status_code == 409

    def test_its_name_can_change_and_it_is_flagged(self, client, people, other):
        admin, _ = people
        r = client.patch(f"/projects/{other.id}", headers=auth(admin),
                         json={"name": "Other / not on the list", "status": "ACTIVE"})
        assert r.status_code == 200
        assert r.json()["is_fallback"] is True
        assert r.json()["code"] == OTHER_PROJECT_CODE

    def test_one_archived_before_the_guard_is_reopened(self, db, other):
        other.status = ProjectStatus.ARCHIVED
        db.commit()
        assert ensure_other_project(db, TENANT).status is ProjectStatus.ACTIVE


class TestDelete:
    def test_a_campaign_with_no_requests_can_be_deleted(self, client, people, db):
        admin, _ = people
        project = make(db, "Mistake", "MIS-1")
        project_id = project.id
        r = client.delete(f"/projects/{project_id}", headers=auth(admin))
        assert r.status_code == 204
        db.expire_all()
        assert db.get(Project, project_id) is None
        entry = db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "project", AuditLog.entity_id == project_id
            )
        ).scalar_one()
        assert entry.action is AuditAction.DELETE

    def test_one_with_even_a_draft_is_kept(self, client, people, db):
        admin, ravi = people
        project = make(db, "Busy", "BUS-1")
        draft_request(db, project, ravi)
        r = client.delete(f"/projects/{project.id}", headers=auth(admin))
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Busy has 1 request, so it can't be deleted.")
        assert db.get(Project, project.id) is not None

    def test_ground_staff_cannot_delete(self, client, people, db):
        _, ravi = people
        project = make(db, "Theirs", "THE-1")
        assert client.delete(f"/projects/{project.id}", headers=auth(ravi)).status_code == 403


class TestList:
    def test_order_counts_and_what_staff_see(self, client, people, db):
        admin, ravi = people
        archived = make(db, "Aardvark", "AAR-1", status=ProjectStatus.ARCHIVED)
        make(db, "Bravo", "BRA-1", status=ProjectStatus.COMPLETED)
        make(db, "Charlie", "CHA-1", status=ProjectStatus.PAUSED)
        busy = make(db, "Zulu", "ZUL-1")
        draft_request(db, busy, ravi)
        draft_request(db, busy, ravi)

        r = client.get("/projects", headers=auth(admin))
        assert r.status_code == 200
        items = r.json()["items"]
        statuses = [i["status"] for i in items]
        assert statuses == sorted(
            statuses, key=["ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED"].index
        )
        assert items[-1]["name"] == "Aardvark"
        by_name = {i["name"]: i for i in items}
        assert by_name["Zulu"]["request_count"] == 2
        assert by_name["Charlie"]["request_count"] == 0

        staff = client.get("/projects", headers=auth(ravi)).json()["items"]
        assert archived.id not in {i["id"] for i in staff}
        # Not even by asking for them.
        r = client.get("/projects", headers=auth(ravi), params={"status": "ARCHIVED"})
        assert r.status_code == 200
        assert r.json()["items"] == [] and r.json()["total"] == 0
        r = client.get("/projects", headers=auth(admin), params={"status": "ARCHIVED"})
        assert [i["id"] for i in r.json()["items"]] == [archived.id]

    def test_search_finds_by_place(self, client, people, db):
        admin, _ = people
        make(db, "Coastal", "COA-1", state="Kerala", city="Kochi")
        r = client.get("/projects", headers=auth(admin), params={"search": "kochi"})
        assert [i["name"] for i in r.json()["items"]] == ["Coastal"]
