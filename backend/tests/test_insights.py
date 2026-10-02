"""
Travel logs across employees, and the filterable dashboard.

The question an admin brings to the log is "where was this person last month",
so the cases here are about the window being the trip's own date, about one
person's log not leaking another's, and about rejected trips staying out of a
record of where people actually went.
"""
from datetime import date, datetime
from decimal import Decimal

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
from app.services import insights

TENANT = "designboxed"


@pytest.fixture
def project(db):
    row = Project(tenant_id=TENANT, name="Monsoon Survey", code="MS-1", status=ProjectStatus.ACTIVE)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def people(db):
    rows = [
        User(tenant_id=TENANT, email=f"{name.lower()}@designboxed.com", full_name=f"{name} Kumar",
             role=Role.GROUND_STAFF, password_hash="x")
        for name in ("Ravi", "Priya")
    ]
    db.add_all(rows)
    db.commit()
    return rows


def trip(db, project, travellers, *, start=None, kind=RequestType.LONG_DISTANCE,
         check_in=None, check_out=None, status=TravellerStatus.BOOKED, cost=None,
         destination="Pune", destination_state="Maharashtra", hotel_city=None, hotel_state=None):
    request = TravelRequest(
        tenant_id=TENANT,
        request_type=kind,
        project_id=project.id,
        requester_id=travellers[0].id,
        mode=TravelMode.FLIGHT if kind is RequestType.LONG_DISTANCE else None,
        origin="Hyderabad" if kind is not RequestType.HOTEL else None,
        origin_state="Telangana" if kind is not RequestType.HOTEL else None,
        destination=destination if kind is not RequestType.HOTEL else None,
        destination_state=destination_state if kind is not RequestType.HOTEL else None,
        start_at=start,
        hotel_city=hotel_city,
        hotel_state=hotel_state,
        check_in=check_in,
        check_out=check_out,
        travel_reason="Store audit",
    )
    db.add(request)
    db.flush()
    for person in travellers:
        db.add(RequestTraveller(request_id=request.id, user_id=person.id, status=status,
                                cost_amount=cost))
    db.commit()
    return request


def log(db, **kwargs):
    kwargs.setdefault("statuses", insights.TRAVELLED)
    return insights.travel_log(db, TENANT, insights.Filters(**kwargs))


class TestTravelLog:
    def test_one_employees_last_month(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 10, 9))
        trip(db, project, [ravi], start=datetime(2026, 9, 3, 9))      # this month
        trip(db, project, [priya], start=datetime(2026, 8, 12, 9))    # someone else

        result = log(db, user_id=ravi.id, since=date(2026, 8, 1), until=date(2026, 8, 31))

        assert result["total"] == 1
        assert result["entries"][0]["full_name"] == "Ravi Kumar"
        assert result["entries"][0]["started_on"] == "2026-08-10"

    def test_the_last_day_of_the_window_is_included(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 31, 23, 30))
        assert log(db, since=date(2026, 8, 1), until=date(2026, 8, 31))["total"] == 1

    def test_a_hotel_is_dated_by_check_in(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], kind=RequestType.HOTEL, check_in=date(2026, 8, 20),
             check_out=date(2026, 8, 23), hotel_city="Pune", hotel_state="Maharashtra")

        result = log(db, since=date(2026, 8, 1), until=date(2026, 8, 31))
        assert result["total"] == 1
        assert result["summary"]["nights"] == 3

    def test_everyone_when_no_employee_is_chosen(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi, priya], start=datetime(2026, 8, 10, 9))
        result = log(db, since=date(2026, 8, 1), until=date(2026, 8, 31))
        assert result["summary"]["people"] == 2
        assert result["summary"]["requests"] == 1

    def test_companions_are_named(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi, priya], start=datetime(2026, 8, 10, 9))
        entry = log(db, user_id=ravi.id)["entries"][0]
        assert entry["companions"] == ["Priya Kumar"]

    def test_a_rejected_trip_is_not_somewhere_anyone_went(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 10, 9), status=TravellerStatus.REJECTED)
        assert log(db)["total"] == 0
        assert log(db, statuses=(TravellerStatus.REJECTED,))["total"] == 1

    def test_state_and_search_narrow_it(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 10, 9))
        trip(db, project, [ravi], start=datetime(2026, 8, 11, 9), destination="Chennai",
             destination_state="Tamil Nadu")

        assert log(db, state="Tamil Nadu")["total"] == 1
        assert log(db, search="chennai")["total"] == 1
        assert log(db, search="ravi")["total"] == 2

    def test_newest_first(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 1, 9))
        trip(db, project, [ravi], start=datetime(2026, 8, 20, 9))
        days = [e["started_on"] for e in log(db)["entries"]]
        assert days == ["2026-08-20", "2026-08-01"]

    def test_spend_counts_booked_only(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 1, 9), cost=Decimal("4500"))
        trip(db, project, [priya], start=datetime(2026, 8, 2, 9), cost=Decimal("999"),
             status=TravellerStatus.APPROVED)
        assert log(db)["summary"]["spent"] == "4500.00"


class TestTravelLogPages:
    """The page on screen is a window; the totals above it are not."""

    @pytest.fixture
    def five(self, db, project, people):
        ravi, priya = people
        for day in range(1, 6):
            trip(db, project, [ravi if day % 2 else priya], start=datetime(2026, 8, day, 9),
                 cost=Decimal("100"))

    def test_a_page_is_a_slice_newest_first(self, db, five):
        result = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED),
                                     page=2, page_size=2)
        assert [e["started_on"] for e in result["entries"]] == ["2026-08-03", "2026-08-02"]
        assert (result["page"], result["pages"], result["total"]) == (2, 3, 5)
        assert result["truncated"] is True

    def test_the_summary_counts_every_page(self, db, five):
        result = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED),
                                     page=3, page_size=2)
        assert len(result["entries"]) == 1
        assert result["truncated"] is False
        assert result["summary"]["movements"] == 5
        assert result["summary"]["people"] == 2
        assert result["summary"]["spent"] == "500.00"

    def test_a_page_past_the_end_shows_the_last_one(self, db, five):
        """Narrowing the filters while on page 9 should not leave an empty table."""
        result = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED),
                                     page=9, page_size=2)
        assert result["page"] == 3
        assert [e["started_on"] for e in result["entries"]] == ["2026-08-01"]

    def test_no_matches_is_one_empty_page(self, db):
        result = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED))
        assert (result["page"], result["pages"], result["total"]) == (1, 1, 0)
        assert result["entries"] == []

    def test_the_page_size_is_capped(self, db, five):
        result = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED),
                                     page_size=insights.MAX_LOG_ROWS + 1)
        assert result["page_size"] == insights.MAX_LOG_ROWS


class TestDashboard:
    def board(self, db, **kwargs):
        return insights.dashboard(db, TENANT, insights.Filters(**kwargs))

    def test_counts_by_status_within_the_window(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), cost=Decimal("3000"))
        trip(db, project, [priya], start=datetime(2026, 8, 6, 9), status=TravellerStatus.PENDING)
        trip(db, project, [priya], start=datetime(2026, 8, 7, 9), status=TravellerStatus.REJECTED)
        trip(db, project, [ravi], start=datetime(2026, 7, 5, 9))   # outside

        board = self.board(db, since=date(2026, 8, 1), until=date(2026, 8, 31))
        kpis = board["kpis"]
        assert kpis["booked"] == 1 and kpis["pending"] == 1 and kpis["rejected"] == 1
        assert kpis["movements"] == 2          # rejected did not travel
        assert kpis["people"] == 2
        assert kpis["spent"] == "3000.00"

    def test_a_short_window_trends_by_day_with_no_gaps(self, db, project, people):
        board = self.board(db, since=date(2026, 8, 1), until=date(2026, 8, 31))
        assert board["grain"] == "day"
        assert len(board["trend"]) == 31

    def test_a_long_window_trends_by_month(self, db, project, people):
        board = self.board(db, since=date(2026, 1, 1), until=date(2026, 12, 31))
        assert board["grain"] == "month"
        assert [b["period"] for b in board["trend"]][:2] == ["2026-01", "2026-02"]

    def test_filters_slice_every_number(self, db, project, people):
        ravi, priya = people
        other = Project(tenant_id=TENANT, name="Retail", code="RT-1", status=ProjectStatus.ACTIVE)
        db.add(other)
        db.commit()
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        trip(db, other, [priya], start=datetime(2026, 8, 5, 9), destination="Chennai",
             destination_state="Tamil Nadu")

        window = {"since": date(2026, 8, 1), "until": date(2026, 8, 31)}
        assert self.board(db, project_id=other.id, **window)["kpis"]["people"] == 1
        assert self.board(db, user_id=ravi.id, **window)["top_travellers"][0]["full_name"] == "Ravi Kumar"
        by_state = self.board(db, state="Tamil Nadu", **window)
        assert by_state["top_states"] == [{"label": "Tamil Nadu", "count": 1}]
        assert by_state["top_places"] == [{"label": "Chennai", "count": 1}]

    def test_filter_options_list_campaigns_people_and_states_in_use(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        options = insights.filter_options(db, TENANT)
        assert "MS-1" in [p["code"] for p in options["projects"]]
        assert "Ravi Kumar" in [p["full_name"] for p in options["people"]]
        assert options["states"] == ["Maharashtra", "Telangana"]
