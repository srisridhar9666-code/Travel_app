"""
Departments: the open-ended grouping admins add as they go.

Adding one is meant to be harmless to repeat - a double click, or two admins
typing the same name - so most of this is about one name meaning one row.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import ratelimit
from app.core.enums import AuditAction, Gender, Role, UserStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.department import Department
from app.models.user import User

TENANT = "designboxed"


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
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, gender=Gender.FEMALE, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, gender=Gender.MALE, password_hash="x")
    db.add_all([admin, ravi])
    db.commit()
    return admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


class TestAdding:
    def test_a_new_name_is_created_and_audited(self, client, db, people):
        admin, _ = people
        r = client.post("/departments", headers=auth(admin), json={"name": "  Field   Operations "})
        assert r.status_code == 201
        assert r.json()["name"] == "Field Operations"

        row = db.execute(
            select(AuditLog).where(AuditLog.entity_type == "department")
        ).scalar_one()
        assert row.action is AuditAction.CREATE
        assert row.entity_id == r.json()["id"]

    def test_the_same_name_in_any_case_returns_the_existing_one(self, client, db, people):
        admin, _ = people
        first = client.post("/departments", headers=auth(admin), json={"name": "Field Operations"})
        again = client.post("/departments", headers=auth(admin), json={"name": "field operations"})
        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        assert again.json()["name"] == "Field Operations"
        assert db.execute(select(func.count(Department.id))).scalar_one() == 1

    def test_ground_staff_may_not_list_or_add(self, client, people):
        _, ravi = people
        assert client.get("/departments", headers=auth(ravi)).status_code == 403
        assert client.post("/departments", headers=auth(ravi), json={"name": "Data"}).status_code == 403

    def test_a_one_letter_name_is_refused(self, client, people):
        admin, _ = people
        assert client.post("/departments", headers=auth(admin), json={"name": " x "}).status_code == 422


class TestListing:
    def test_lists_by_name_with_member_counts(self, client, db, people):
        admin, ravi = people
        data = Department(tenant_id=TENANT, name="Data")
        field = Department(tenant_id=TENANT, name="Field Operations")
        db.add_all([data, field])
        db.flush()
        ravi.department_id = field.id
        gone = User(tenant_id=TENANT, email="old@designboxed.com", full_name="Old Hand",
                    password_hash="x", department_id=field.id, status=UserStatus.DELETED)
        db.add(gone)
        db.commit()

        r = client.get("/departments", headers=auth(admin))
        assert [(d["name"], d["member_count"]) for d in r.json()] == [
            ("Data", 0), ("Field Operations", 1),  # the deleted account does not count
        ]


class TestOnPeople:
    def test_create_and_edit_take_a_department(self, client, db, people):
        admin, ravi = people
        dept = Department(tenant_id=TENANT, name="Finance")
        db.add(dept)
        db.commit()

        r = client.post("/users", headers=auth(admin), json={
            "email": "anita@designboxed.com", "full_name": "Anita Desai",
            "gender": "FEMALE", "department_id": dept.id,
        })
        assert r.status_code == 201
        anita = db.execute(select(User).where(User.email == "anita@designboxed.com")).scalar_one()
        assert anita.department_name == "Finance"

        r = client.patch(f"/users/{ravi.id}", headers=auth(admin), json={"department_id": dept.id})
        assert r.json()["department_name"] == "Finance"
        r = client.patch(f"/users/{ravi.id}", headers=auth(admin), json={"department_id": None})
        assert r.json()["department_id"] is None

    def test_an_unknown_or_foreign_department_is_refused(self, client, db, people):
        admin, ravi = people
        elsewhere = Department(tenant_id="othercorp", name="Finance")
        db.add(elsewhere)
        db.commit()
        for department_id in (elsewhere.id, 999999):
            r = client.patch(f"/users/{ravi.id}", headers=auth(admin),
                             json={"department_id": department_id})
            assert r.status_code == 422
            assert r.json()["detail"] == "Unknown department."
            r = client.post("/users", headers=auth(admin), json={
                "email": "anita@designboxed.com", "full_name": "Anita Desai",
                "gender": "FEMALE", "department_id": department_id,
            })
            assert r.status_code == 422

    def test_the_list_filters_and_searches_by_department(self, client, db, people):
        admin, ravi = people
        dept = Department(tenant_id=TENANT, name="Field Operations")
        db.add(dept)
        db.flush()
        ravi.department_id = dept.id
        db.commit()

        r = client.get("/users", headers=auth(admin), params={"department_id": dept.id})
        assert [u["email"] for u in r.json()["items"]] == ["ravi@designboxed.com"]
        r = client.get("/users", headers=auth(admin), params={"search": "field op"})
        assert [u["email"] for u in r.json()["items"]] == ["ravi@designboxed.com"]


class TestRenameAndDelete:
    def test_rename_refuses_a_name_in_use(self, client, db, people):
        admin, _ = people
        db.add_all([Department(tenant_id=TENANT, name="Data"),
                    Department(tenant_id=TENANT, name="Finance")])
        db.commit()
        data = db.execute(select(Department).where(Department.name == "Data")).scalar_one()

        r = client.patch(f"/departments/{data.id}", headers=auth(admin), json={"name": "FINANCE"})
        assert r.status_code == 409
        r = client.patch(f"/departments/{data.id}", headers=auth(admin), json={"name": "Data Team"})
        assert r.json()["name"] == "Data Team"

    def test_only_an_empty_department_can_be_deleted(self, client, db, people):
        admin, ravi = people
        dept = Department(tenant_id=TENANT, name="Finance")
        db.add(dept)
        db.flush()
        ravi.department_id = dept.id
        db.commit()

        r = client.delete(f"/departments/{dept.id}", headers=auth(admin))
        assert r.status_code == 409
        assert "1 person" in r.json()["detail"]

        ravi.department_id = None
        db.commit()
        assert client.delete(f"/departments/{dept.id}", headers=auth(admin)).status_code == 204
        assert db.get(Department, dept.id) is None


class TestImport:
    def test_commit_creates_a_named_department_once(self, client, db, people):
        admin, _ = people
        body = (
            "full_name,email,gender,department,base_state,base_location\n"
            "Ravi Two,ravi2@designboxed.com,M,Field Operations,,\n"
            "Anita Desai,anita@designboxed.com,f,field operations,,\n"
        )
        r = client.post("/users/import", headers=auth(admin),
                        files={"file": ("team.csv", body.encode(), "text/csv")})
        assert r.status_code == 200
        assert r.json()["created"] == 2

        depts = db.execute(select(Department)).scalars().all()
        assert [d.name for d in depts] == ["Field Operations"]
        imported = db.execute(
            select(User).where(User.email.in_(["ravi2@designboxed.com", "anita@designboxed.com"]))
        ).scalars().all()
        assert {u.department_id for u in imported} == {depts[0].id}
        assert {u.gender for u in imported} == {Gender.MALE, Gender.FEMALE}
        assert db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.entity_type == "department")
        ).scalar_one() == 1
