"""
Travel logs across employees, and the filterable dashboard.

The question an admin brings to the log is "where was this person last month",
so the cases here are about the window being the trip's own date, about one
person's log not leaking another's, and about rejected trips staying out of a
record of where people actually went.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.core import clock
from app.core.enums import (
    ProjectStatus,
    RequestPriority,
    RequestType,
    Role,
    TravelMode,
    TravellerStatus,
    UserStatus,
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

    def test_filter_options_list_campaigns_people_and_destinations_in_use(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        priya.status = UserStatus.LEFT
        db.commit()

        options = insights.filter_options(db, TENANT)
        assert "MS-1" in [p["code"] for p in options["projects"]]
        statuses = {p["full_name"]: p["status"] for p in options["people"]}
        assert statuses["Ravi Kumar"] == "ACTIVE"
        assert statuses["Priya Kumar"] == "LEFT"
        # Destinations only: the trip starts in Telangana, but nobody went there.
        assert options["states"] == ["Maharashtra"]
        assert options["cities"] == [{"state": "Maharashtra", "city": "Pune"}]

    def test_filter_options_list_cab_drop_cities_never_addresses(self, db, project, people):
        ravi, _ = people
        cab = trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), kind=RequestType.LOCAL_CAB,
                   destination="RGIA Airport", destination_state="Telangana")
        trip(db, project, [ravi], kind=RequestType.HOTEL, check_in=date(2026, 8, 6),
             check_out=date(2026, 8, 7), hotel_city="Chennai", hotel_state="Tamil Nadu")
        cities = {c["city"] for c in insights.filter_options(db, TENANT)["cities"]}
        assert cities == {"Chennai"}   # the old cab has an address, not a city

        cab.drop_city = "Shamshabad"
        db.commit()
        cities = {c["city"] for c in insights.filter_options(db, TENANT)["cities"]}
        assert cities == {"Chennai", "Shamshabad"}

    def test_someone_switched_off_the_old_way_is_not_shown_as_active(self, db, project, people):
        ravi, _ = people
        ravi.is_active = False
        db.commit()
        statuses = {p["full_name"]: p["status"] for p in insights.filter_options(db, TENANT)["people"]}
        assert statuses["Ravi Kumar"] != "ACTIVE"


def days_from_today(days: int, hour: int = 9) -> datetime:
    return datetime.combine(clock.local_today() + timedelta(days=days), time(hour))


def group_trip(db, project, decided: list[tuple[User, TravellerStatus]], *, days: int,
               destination="Pune", destination_state="Maharashtra", kind=RequestType.LONG_DISTANCE):
    """A trip whose travellers can each be at a different status."""
    request = trip(db, project, [decided[0][0]], start=days_from_today(days), kind=kind,
                   destination=destination, destination_state=destination_state,
                   status=decided[0][1])
    for person, status in decided[1:]:
        db.add(RequestTraveller(request_id=request.id, user_id=person.id, status=status))
    db.commit()
    db.refresh(request)
    return request


class TestAwaiting:
    """The tile follows every filter, and counts only what can still be decided."""

    @pytest.fixture
    def queue(self, db, project, people):
        ravi, priya = people
        other = Project(tenant_id=TENANT, name="Retail", code="RT-1", status=ProjectStatus.ACTIVE)
        db.add(other)
        db.commit()
        group_trip(db, project, [(ravi, TravellerStatus.PENDING)], days=5)
        group_trip(db, other, [(priya, TravellerStatus.PENDING)], days=6,
                   destination="Chennai", destination_state="Tamil Nadu")
        # The date went by with nobody deciding: Expired, not awaiting.
        group_trip(db, project, [(ravi, TravellerStatus.PENDING)], days=-3)
        # One decided, one still waiting: partly approved.
        group_trip(db, project, [(ravi, TravellerStatus.APPROVED), (priya, TravellerStatus.PENDING)],
                   days=8)
        return other

    def awaiting(self, db, **kwargs):
        today = clock.local_today()
        kwargs.setdefault("since", today - timedelta(days=30))
        kwargs.setdefault("until", today + timedelta(days=30))
        return insights.dashboard(db, TENANT, insights.Filters(**kwargs))["awaiting"]

    def test_counts_requests_awaiting_a_decision(self, db, queue):
        result = self.awaiting(db)
        assert result["requests"] == 2
        assert result["people"] == 2
        assert result["partly_approved"] == 1
        assert result["with_conflicts"] == 0

    def test_follows_every_filter(self, db, project, people, queue):
        ravi, priya = people
        today = clock.local_today()
        assert self.awaiting(db, project_id=queue.id)["requests"] == 1
        assert self.awaiting(db, project_id=project.id)["requests"] == 1
        assert self.awaiting(db, user_id=priya.id)["requests"] == 1
        assert self.awaiting(db, user_id=priya.id)["partly_approved"] == 1
        assert self.awaiting(db, state="Tamil Nadu")["requests"] == 1
        assert self.awaiting(db, city="Chennai")["requests"] == 1
        assert self.awaiting(db, request_type=RequestType.HOTEL)["requests"] == 0
        # A past-only window: the expired request is not counted.
        past = self.awaiting(db, since=today - timedelta(days=30), until=today - timedelta(days=1))
        assert past["requests"] == 0

    def test_a_clash_is_flagged(self, db, project, people, queue):
        ravi, _ = people
        # Ravi is already on a trip departing at the same moment.
        group_trip(db, project, [(ravi, TravellerStatus.BOOKED)], days=5)
        assert self.awaiting(db)["with_conflicts"] >= 1


class TestOpenDates:
    def board(self, db, **kwargs):
        return insights.dashboard(db, TENANT, insights.Filters(**kwargs))

    def test_all_time_includes_upcoming_trips(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=days_from_today(40))
        trip(db, project, [ravi], start=days_from_today(-400))

        board = self.board(db)
        today = clock.local_today()
        assert board["kpis"]["movements"] == 2
        assert board["until"] >= (today + timedelta(days=40)).isoformat()
        first = today - timedelta(days=400)
        assert board["trend"][0]["period"] == f"{first.year:04d}-{first.month:02d}"
        assert board["grain"] == "month"

    def test_an_open_until_is_not_capped_at_today(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=days_from_today(10))
        board = self.board(db, since=clock.local_today() - timedelta(days=5))
        assert board["kpis"]["movements"] == 1
        assert sum(b["movements"] for b in board["trend"]) == 1

    def test_no_data_and_no_dates_is_the_last_30_days(self, db):
        board = self.board(db)
        assert board["until"] == clock.local_today().isoformat()
        assert len(board["trend"]) == 30

    def test_reversed_dates_are_swapped(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        board = self.board(db, since=date(2026, 8, 31), until=date(2026, 8, 1))
        assert (board["since"], board["until"]) == ("2026-08-01", "2026-08-31")
        assert board["kpis"]["movements"] == 1


class TestDestinations:
    window = {"since": date(2026, 8, 1), "until": date(2026, 8, 31)}

    def board(self, db, **kwargs):
        return insights.dashboard(db, TENANT, insights.Filters(**self.window, **kwargs))

    def test_the_state_filter_is_the_destination(self, db, project, people):
        ravi, priya = people
        # Hyderabad (Telangana) to Chennai, and a hotel in Pune.
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), destination="Chennai",
             destination_state="Tamil Nadu")
        trip(db, project, [priya], kind=RequestType.HOTEL, check_in=date(2026, 8, 6),
             check_out=date(2026, 8, 8), hotel_city="Pune", hotel_state="Maharashtra")

        assert self.board(db, state="Telangana")["kpis"]["movements"] == 0
        tamil = self.board(db, state="Tamil Nadu")
        assert tamil["top_states"] == [{"label": "Tamil Nadu", "count": 1}]
        assert tamil["kpis"]["movements"] == 1
        maharashtra = self.board(db, state="Maharashtra")
        assert maharashtra["kpis"]["movements"] == 1
        assert {t["request_type"]: t["count"] for t in maharashtra["by_type"]}["HOTEL"] == 1
        assert log(db, state="Telangana", **self.window)["total"] == 0
        assert log(db, state="Maharashtra", **self.window)["total"] == 1

    def test_the_city_filter_is_the_destination_city(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))     # to Pune
        back = trip(db, project, [ravi], start=datetime(2026, 8, 9, 9), destination="Hyderabad",
                    destination_state="Telangana")
        back.origin, back.origin_state = "Pune", "Maharashtra"
        cab = trip(db, project, [ravi], start=datetime(2026, 8, 6, 9), kind=RequestType.LOCAL_CAB,
                   destination="Pune Station", destination_state="Maharashtra")
        db.commit()

        assert log(db, city="Pune", **self.window)["total"] == 1
        cab.drop_city = "Pune"
        db.commit()
        assert log(db, city="Pune", **self.window)["total"] == 2
        assert self.board(db, city="Pune")["kpis"]["movements"] == 2

    def test_trips_without_a_state_are_reported_not_hidden(self, db, project, people):
        ravi, _ = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), destination_state=None)
        board = self.board(db)
        assert board["top_states"] == []
        assert board["kpis"]["unstated"] == 1
        assert board["kpis"]["movements"] == 1

    def test_top_places_include_cab_drop_cities(self, db, project, people):
        ravi, _ = people
        cab = trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), kind=RequestType.LOCAL_CAB,
                   destination="RGIA Airport", destination_state="Telangana")
        cab.drop_city = "Shamshabad"
        trip(db, project, [ravi], start=datetime(2026, 8, 6, 9), kind=RequestType.LOCAL_CAB,
             destination="Road No. 12, Banjara Hills", destination_state="Telangana")   # legacy
        db.commit()

        places = self.board(db, request_type=RequestType.LOCAL_CAB)["top_places"]
        assert places == [{"label": "Shamshabad", "count": 1}]

    def test_a_cancelled_request_counts_as_cancelled(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        gone = trip(db, project, [priya], start=datetime(2026, 8, 6, 9),
                    status=TravellerStatus.CANCELLED)
        gone.is_cancelled = True
        db.commit()

        board = self.board(db)
        assert {s["status"]: s["count"] for s in board["by_status"]}["CANCELLED"] == 1
        assert board["kpis"]["movements"] == 1
        assert log(db, **self.window)["total"] == 1
        assert log(db, statuses=(TravellerStatus.CANCELLED,), **self.window)["total"] == 1


class TestEveryStatusLog:
    def test_no_status_lists_everything_but_totals_count_travel(self, db, project, people):
        ravi, priya = people
        trip(db, project, [ravi], start=datetime(2026, 8, 5, 9), cost=Decimal("1000"))
        trip(db, project, [priya], kind=RequestType.HOTEL, check_in=date(2026, 8, 6),
             check_out=date(2026, 8, 9), hotel_city="Chennai", hotel_state="Tamil Nadu",
             status=TravellerStatus.REJECTED)
        trip(db, project, [priya], start=datetime(2026, 8, 7, 9), status=TravellerStatus.CANCELLED,
             destination="Mumbai")

        result = insights.travel_log(db, TENANT, insights.Filters())
        assert result["total"] == 3
        summary = result["summary"]
        assert summary["movements"] == 1
        assert summary["people"] == 1
        assert summary["nights"] == 0       # the rejected hotel is not a night away
        assert summary["places"] == 1
        assert summary["spent"] == "1000.00"

    def test_each_entry_carries_its_priority(self, db, project, people):
        ravi, _ = people
        request = trip(db, project, [ravi], start=datetime(2026, 8, 5, 9))
        request.priority = RequestPriority.HIGH
        db.commit()
        assert log(db)["entries"][0]["priority"] == "HIGH"
