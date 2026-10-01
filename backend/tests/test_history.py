"""
Per-employee travel history (SOW section 5).

The behaviour that matters most here is the one that distinguishes this from the
request list: a movement belongs to everyone named on it, not just whoever
raised it. Someone tagged onto a colleague's cab was in the cab.
"""
from datetime import date, datetime, timedelta

import pytest

from app.core.enums import (
    ProjectStatus,
    RequestType,
    Role,
    TravelMode,
    TravellerStatus,
)
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import history

TENANT = "designboxed"


@pytest.fixture
def project(db):
    row = Project(
        tenant_id=TENANT, name="Monsoon Survey", code="MS-1", status=ProjectStatus.ACTIVE
    )
    db.add(row)
    db.commit()
    return row


def make_user(db, name, email):
    row = User(
        tenant_id=TENANT, email=email, full_name=name,
        role=Role.GROUND_STAFF, password_hash="x",
    )
    db.add(row)
    db.commit()
    return row


def make_trip(
    db,
    project,
    requester,
    *,
    travellers,
    kind=RequestType.LONG_DISTANCE,
    start=None,
    check_in=None,
    check_out=None,
    status=TravellerStatus.BOOKED,
    is_draft=False,
    is_cancelled=False,
    origin="Hyderabad",
    destination="Indore",
    hotel_city=None,
):
    request = TravelRequest(
        tenant_id=TENANT,
        request_type=kind,
        project_id=project.id,
        requester_id=requester.id,
        is_draft=is_draft,
        is_cancelled=is_cancelled,
        mode=TravelMode.FLIGHT if kind is RequestType.LONG_DISTANCE else None,
        origin=origin if kind is not RequestType.HOTEL else None,
        destination=destination if kind is not RequestType.HOTEL else None,
        start_at=start,
        hotel_city=hotel_city,
        check_in=check_in,
        check_out=check_out,
    )
    db.add(request)
    db.flush()

    for person in travellers:
        db.add(
            RequestTraveller(
                request_id=request.id,
                user_id=person.id,
                status=status,
            )
        )
    db.commit()
    return request


def build(db, user, **kwargs):
    kwargs.setdefault("since", date(2000, 1, 1))
    return history.build(db, user_id=user.id, tenant_id=TENANT, **kwargs)


class TestWhoseMovementItIs:
    def test_the_requester_sees_their_own_trip(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1, 9, 0))

        assert build(db, ravi)["summary"]["movements"] == 1

    def test_someone_tagged_on_sees_it_too(self, db, project):
        """The point of the whole feature. Priya never raised this request, but
        she was on the flight, and an admin reconstructing her month needs it."""
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        priya = make_user(db, "Priya Sharma", "priya@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi, priya], start=datetime(2026, 5, 1, 9, 0))

        assert build(db, priya)["summary"]["movements"] == 1

    def test_companions_are_named(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        priya = make_user(db, "Priya Sharma", "priya@designboxed.com")
        deepak = make_user(db, "Deepak Shah", "deepak@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi, priya, deepak], start=datetime(2026, 5, 1))

        entry = build(db, priya)["entries"][0]
        assert {c["full_name"] for c in entry["companions"]} == {"Ravi Kumar", "Deepak Shah"}

    def test_the_subject_is_not_their_own_companion(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        priya = make_user(db, "Priya Sharma", "priya@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi, priya], start=datetime(2026, 5, 1))

        entry = build(db, ravi)["entries"][0]
        assert all(c["full_name"] != "Ravi Kumar" for c in entry["companions"])

    def test_a_colleagues_trip_you_are_not_on_does_not_appear(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        priya = make_user(db, "Priya Sharma", "priya@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1))

        assert build(db, priya)["summary"]["movements"] == 0


class TestWhatCounts:
    def test_rejected_travellers_did_not_travel(self, db, project):
        """Showing them would misreport where someone was."""
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi],
            start=datetime(2026, 5, 1), status=TravellerStatus.REJECTED,
        )
        assert build(db, ravi)["summary"]["movements"] == 0

    def test_cancelled_travellers_are_excluded(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi],
            start=datetime(2026, 5, 1), status=TravellerStatus.CANCELLED,
        )
        assert build(db, ravi)["summary"]["movements"] == 0

    def test_drafts_are_excluded(self, db, project):
        """A draft is a thought, not a movement."""
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1), is_draft=True)
        assert build(db, ravi)["summary"]["movements"] == 0

    def test_cancelled_requests_are_excluded(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1), is_cancelled=True
        )
        assert build(db, ravi)["summary"]["movements"] == 0

    def test_pending_still_shows_as_an_intended_movement(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi],
            start=datetime(2026, 5, 1), status=TravellerStatus.PENDING,
        )
        result = build(db, ravi)
        assert result["summary"]["movements"] == 1
        assert result["entries"][0]["status"] == "PENDING"


class TestHotelStays:
    def test_nights_are_counted(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi], kind=RequestType.HOTEL,
            hotel_city="Indore", check_in=date(2026, 5, 1), check_out=date(2026, 5, 5),
        )
        result = build(db, ravi)
        assert result["entries"][0]["nights"] == 4
        assert result["summary"]["nights_away"] == 4

    def test_the_city_is_what_is_shown(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi], kind=RequestType.HOTEL,
            hotel_city="Indore", check_in=date(2026, 5, 1), check_out=date(2026, 5, 3),
        )
        assert build(db, ravi)["entries"][0]["where"] == "Indore"

    def test_a_stay_with_no_checkout_does_not_crash_the_count(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(
            db, project, ravi, travellers=[ravi], kind=RequestType.HOTEL,
            hotel_city="Indore", check_in=date(2026, 5, 1), check_out=None,
        )
        assert build(db, ravi)["entries"][0]["nights"] is None


class TestOrderingAndWindow:
    def test_newest_movement_first(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 1, 1), destination="A")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 6, 1), destination="B")

        wheres = [e["where"] for e in build(db, ravi)["entries"]]
        assert wheres[0].endswith("B")

    def test_the_window_excludes_older_movements(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        long_ago = datetime.combine(date.today() - timedelta(days=400), datetime.min.time())
        make_trip(db, project, ravi, travellers=[ravi], start=long_ago)

        # Default window is 90 days.
        assert history.build(db, user_id=ravi.id, tenant_id=TENANT)["summary"]["movements"] == 0
        # Widened, it is there.
        assert build(db, ravi)["summary"]["movements"] == 1


class TestCostVisibility:
    def test_costs_are_withheld_unless_asked_for(self, db, project):
        """A ground staff member reading their own timeline has no business
        seeing what the company paid for their seat."""
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        trip = make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1))
        traveller = db.query(RequestTraveller).filter_by(request_id=trip.id).one()
        traveller.cost_amount = 4250
        db.commit()

        assert build(db, ravi)["entries"][0]["cost_amount"] is None
        assert build(db, ravi, include_costs=True)["entries"][0]["cost_amount"] is not None


class TestTenantScope:
    def test_another_tenants_movement_is_invisible(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1))

        other = history.build(
            db, user_id=ravi.id, tenant_id="anomality", since=date(2000, 1, 1)
        )
        assert other["summary"]["movements"] == 0


class TestSummary:
    def test_counts_by_type(self, db, project):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi], start=datetime(2026, 5, 1))
        make_trip(
            db, project, ravi, travellers=[ravi], kind=RequestType.LOCAL_CAB,
            start=datetime(2026, 5, 2),
        )
        make_trip(
            db, project, ravi, travellers=[ravi], kind=RequestType.HOTEL,
            hotel_city="Indore", check_in=date(2026, 5, 1), check_out=date(2026, 5, 3),
        )

        by_type = build(db, ravi)["summary"]["by_type"]
        assert by_type == {"LONG_DISTANCE": 1, "LOCAL_CAB": 1, "HOTEL": 1}

    def test_distinct_colleagues_not_appearances(self, db, project):
        """Travelling with the same person twice is one colleague, not two."""
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        priya = make_user(db, "Priya Sharma", "priya@designboxed.com")
        make_trip(db, project, ravi, travellers=[ravi, priya], start=datetime(2026, 5, 1))
        make_trip(db, project, ravi, travellers=[ravi, priya], start=datetime(2026, 6, 1))

        assert build(db, ravi)["summary"]["travelled_with"] == 1

    def test_an_empty_history_summarises_to_zero(self, db):
        ravi = make_user(db, "Ravi Kumar", "ravi@designboxed.com")
        summary = build(db, ravi)["summary"]
        assert summary["movements"] == 0
        assert summary["nights_away"] == 0
        assert summary["cities"] == 0
