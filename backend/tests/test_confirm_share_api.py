"""
An admin confirming a shared room (addendum C2), through the real routes.

The requester's ask is checked when it is saved, but the confirmation can come
days later. By then a profile may have changed or either stay may have been
rejected, cancelled or moved, so the confirmation checks both people again and
refuses with a reason the Approvals page can show as it is.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import clock, ratelimit
from app.core.enums import Gender, RequestType, Role, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications

TENANT = "designboxed"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    # The after-response email send opens its own session on the real database.
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

    def person(name, email, role=Role.GROUND_STAFF, gender=Gender.MALE):
        return User(tenant_id=TENANT, email=email, full_name=name, role=role,
                    gender=gender, password_hash="x")

    admin = person("Priya Shah", "priya@designboxed.com", role=Role.ADMIN, gender=Gender.FEMALE)
    ravi = person("Ravi Kumar", "ravi@designboxed.com")
    arjun = person("Arjun Nair", "arjun@designboxed.com")
    db.add_all([project, admin, ravi, arjun])
    db.commit()
    return project, admin, ravi, arjun


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def stay(db, project, user, *, city="Mumbai", days_ahead=10, nights=2):
    """A submitted, undecided hotel stay straight on the table."""
    check_in = clock.local_today() + timedelta(days=days_ahead)
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=project.id,
        requester_id=user.id, hotel_city=city, hotel_state="Maharashtra",
        check_in=check_in, check_out=check_in + timedelta(days=nights),
        travel_reason="Store audit", submitted_at=naive_utcnow(),
    )
    row.travellers = [RequestTraveller(user_id=user.id, status=TravellerStatus.PENDING)]
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def asked(client, db, world):
    """Ravi has asked, through the requester's own route, to share with Arjun."""
    project, admin, ravi, arjun = world
    theirs = stay(db, project, arjun)
    mine = stay(db, project, ravi)
    res = client.post(
        f"/requests/{mine.id}/room-sharing",
        json={"traveller_id": mine.travellers[0].id, "choice": "SHARE_EXISTING",
              "share_with_user_id": arjun.id},
        headers=auth(ravi),
    )
    assert res.status_code == 200, res.text
    return mine, theirs


def confirm(client, admin, request):
    return client.post(
        f"/requests/{request.id}/travellers/{request.travellers[0].id}/confirm-share",
        headers=auth(admin),
    )


def test_an_admin_confirms_and_the_requester_sees_it(client, world, asked):
    _, admin, ravi, arjun = world
    mine, _ = asked

    res = confirm(client, admin, mine)
    assert res.status_code == 200, res.text
    row = res.json()["travellers"][0]
    assert row["share_confirmed"] is True
    assert row["share_with_user_id"] == arjun.id

    # What My requests reads: no longer "(pending)".
    seen = client.get(f"/requests/{mine.id}", headers=auth(ravi)).json()
    assert seen["travellers"][0]["share_confirmed"] is True


def test_ground_staff_cannot_confirm(client, world, asked):
    _, _, ravi, _ = world
    mine, _ = asked
    assert confirm(client, ravi, mine).status_code == 403


def test_a_traveller_who_never_asked_cannot_be_confirmed(client, db, world):
    project, admin, ravi, _ = world
    mine = stay(db, project, ravi)
    res = confirm(client, admin, mine)
    assert res.status_code == 400
    assert res.json()["detail"] == "This traveller has not asked to share a room."


@pytest.mark.parametrize("status", [TravellerStatus.REJECTED, TravellerStatus.CANCELLED])
def test_the_colleague_must_still_be_on_the_trip(client, db, world, asked, status):
    _, admin, _, _ = world
    mine, theirs = asked
    theirs.travellers[0].status = status
    db.commit()

    res = confirm(client, admin, mine)
    assert res.status_code == 409
    assert res.json()["detail"] == (
        "Arjun Nair no longer has a live hotel stay in Mumbai on any of these nights."
    )
    assert mine.travellers[0].share_confirmed_at is None


def test_the_colleague_must_still_overlap(client, db, world, asked):
    _, admin, _, _ = world
    mine, theirs = asked
    theirs.check_in = mine.check_out
    theirs.check_out = mine.check_out + timedelta(days=2)
    db.commit()
    assert confirm(client, admin, mine).status_code == 409


def test_the_colleague_cancelling_their_request_stops_it(client, db, world, asked):
    _, admin, _, _ = world
    mine, theirs = asked
    theirs.is_cancelled = True
    db.commit()
    assert confirm(client, admin, mine).status_code == 409


def test_a_colleague_tagged_on_the_same_stay_is_on_the_trip(client, db, world):
    """Not excluded just because it is the same request."""
    project, admin, ravi, arjun = world
    mine = stay(db, project, ravi)
    mine.travellers.append(RequestTraveller(user_id=arjun.id, status=TravellerStatus.PENDING))
    db.commit()
    res = client.post(
        f"/requests/{mine.id}/room-sharing",
        json={"traveller_id": mine.travellers[0].id, "choice": "SHARE_EXISTING",
              "share_with_user_id": arjun.id},
        headers=auth(ravi),
    )
    assert res.status_code == 200, res.text
    assert confirm(client, admin, mine).status_code == 200


def test_the_gender_policy_is_checked_again(client, db, world, asked):
    """A profile edited after the ask must not slip a pairing past the policy."""
    _, admin, _, arjun = world
    mine, _ = asked
    arjun.gender = Gender.UNDISCLOSED
    db.commit()

    res = confirm(client, admin, mine)
    assert res.status_code == 409
    assert res.json()["detail"] == "These two travellers cannot share a room."


def test_a_rejected_requester_cannot_have_a_share_confirmed(client, db, world, asked):
    _, admin, _, _ = world
    mine, _ = asked
    mine.travellers[0].status = TravellerStatus.REJECTED
    db.commit()

    res = confirm(client, admin, mine)
    assert res.status_code == 409
    assert res.json()["detail"] == "Ravi Kumar is no longer travelling on this request."


def test_a_cancelled_request_cannot_have_a_share_confirmed(client, db, world, asked):
    _, admin, _, _ = world
    mine, _ = asked
    mine.is_cancelled = True
    db.commit()

    res = confirm(client, admin, mine)
    assert res.status_code == 409
    assert res.json()["detail"] == "This request has been cancelled."
