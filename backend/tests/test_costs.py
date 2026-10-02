"""
Money: splitting it, and never losing a paisa (addendum B5 / C1).

These are the tests that keep a financial report trustworthy. Every one of them
is about arithmetic that looks obvious and is not: an even split that does not
sum to the total, a float that arrives as 0.30000000000000004, a campaign that
looks cheap because one fare was never entered.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from app.core import clock
from app.core.enums import ProjectStatus, RequestType, Role, TravellerStatus
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import analytics, costs
from app.services.insights import Filters

TENANT = "designboxed"


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total,shares,expected",
    [
        ("1000", 3, ["333.34", "333.33", "333.33"]),
        ("100", 4, ["25.00", "25.00", "25.00", "25.00"]),
        ("7", 2, ["3.50", "3.50"]),
        ("10", 3, ["3.34", "3.33", "3.33"]),
        ("0.05", 3, ["0.02", "0.02", "0.01"]),
        ("0.01", 2, ["0.01", "0.00"]),
        ("1000", 1, ["1000.00"]),
    ],
)
def test_a_split_is_exact(total, shares, expected):
    parts = costs.split_evenly(total, shares)
    assert [str(p) for p in parts] == expected


@pytest.mark.parametrize(
    "total,shares",
    [("1000", 3), ("1000.10", 3), ("0.05", 3), ("12345.67", 7), ("99999.99", 11), ("1", 8)],
)
def test_a_split_always_sums_back_to_the_total(total, shares):
    """The property that matters: no rupee invented, none lost."""
    parts = costs.split_evenly(total, shares)
    assert sum(parts) == costs.to_money(total)
    assert len(parts) == shares


def test_the_odd_paisa_goes_to_the_first_traveller():
    """Who is always the requester - they absorb it rather than a colleague being
    charged more than the person who booked the trip."""
    parts = costs.split_evenly("1000", 3)
    assert parts[0] > parts[1] == parts[2]


def test_shares_never_differ_by_more_than_a_paisa():
    for shares in range(1, 20):
        parts = costs.split_evenly("1000", shares)
        assert max(parts) - min(parts) <= Decimal("0.01"), shares


def test_splitting_across_nobody_is_refused():
    with pytest.raises(ValueError):
        costs.split_evenly("100", 0)


def test_a_negative_total_is_refused():
    with pytest.raises(ValueError):
        costs.split_evenly("-100", 2)


def test_zero_splits_to_zero():
    assert costs.split_evenly("0", 3) == [Decimal("0.00")] * 3


# ---------------------------------------------------------------------------
# Decimal discipline
# ---------------------------------------------------------------------------


def test_a_float_does_not_poison_the_amount():
    """`Decimal(0.1)` is 0.1000000000000000055…; going through `str` is what
    keeps a rounding error out of a financial report."""
    assert costs.to_money(0.1 + 0.2) == Decimal("0.30")
    assert costs.to_money(2.675) == Decimal("2.68")


def test_amounts_are_quantised_to_paise():
    assert costs.to_money("10.005") == Decimal("10.01")
    assert costs.to_money("10.004") == Decimal("10.00")
    assert costs.to_money(5) == Decimal("5.00")


def test_a_missing_amount_is_an_error_not_a_zero():
    """Zero is a real cost. Absent is a different fact, and conflating them is
    how an unrecorded fare becomes a free trip in a report."""
    with pytest.raises(ValueError):
        costs.to_money(None)


def test_totalling_skips_missing_values_without_inventing_zeroes():
    assert costs.total_of(["10.50", None, "4.50"]) == Decimal("15.00")
    assert costs.total_of([]) == Decimal("0.00")


# ---------------------------------------------------------------------------
# What the reports count
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db):
    monsoon = Project(
        tenant_id=TENANT, name="Monsoon Survey", code="MON-1", location="Maharashtra"
    )
    coastal = Project(
        tenant_id=TENANT, name="Coastal Audit", code="CST-1", location="Kerala"
    )
    ravi = User(
        tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
        role=Role.GROUND_STAFF, password_hash="x",
    )
    meera = User(
        tenant_id=TENANT, email="meera@designboxed.com", full_name="Meera Iyer",
        role=Role.GROUND_STAFF, password_hash="x",
    )
    db.add_all([monsoon, coastal, ravi, meera])
    db.commit()
    return monsoon, coastal, ravi, meera


def trip(
    db, project, people, *, status=TravellerStatus.BOOKED, cost="1000.00",
    days_out=10, kind=RequestType.LONG_DISTANCE, cancelled=False, draft=False,
):
    start = clock.local_today() + timedelta(days=days_out)
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=kind,
        project_id=project.id,
        requester_id=people[0].id,
        is_cancelled=cancelled,
        is_draft=draft,
        submitted_at=naive_utcnow(),
    )
    if kind is RequestType.HOTEL:
        row.hotel_city = "Mumbai"
        row.check_in = start
        row.check_out = start + timedelta(days=2)
    else:
        row.origin, row.destination = "Hyderabad", "Mumbai"
        row.start_at = naive_utcnow().replace(microsecond=0) + timedelta(days=days_out)
    row.travellers = [
        RequestTraveller(
            user_id=p.id,
            status=status,
            cost_amount=Decimal(cost) if cost is not None else None,
        )
        for p in people
    ]
    db.add(row)
    db.commit()
    return row


def test_only_booked_travellers_count_as_spend(db, world):
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi], status=TravellerStatus.BOOKED, cost="1000.00")
    trip(db, monsoon, [meera], status=TravellerStatus.APPROVED, cost="500.00")

    result = analytics.overview(db, TENANT)
    assert result["spent"] == "1000.00"
    # Approved but unticketed is a forecast, reported separately so nobody has to
    # reconcile a dashboard against an invoice and find a trip that never happened.
    assert result["committed"] == "500.00"


@pytest.mark.parametrize(
    "status,counted",
    [
        (TravellerStatus.BOOKED, True),
        (TravellerStatus.APPROVED, False),
        (TravellerStatus.PENDING, False),
        (TravellerStatus.REJECTED, False),
        (TravellerStatus.CANCELLED, False),
    ],
)
def test_spend_ignores_every_status_but_booked(db, world, status, counted):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], status=status, cost="750.00")
    assert (analytics.overview(db, TENANT)["spent"] == "750.00") is counted


def test_a_cancelled_request_is_not_counted(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00", cancelled=True)
    assert analytics.overview(db, TENANT)["spent"] == "0.00"


def test_a_draft_is_not_counted(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00", draft=True)
    assert analytics.overview(db, TENANT)["spent"] == "0.00"


def test_a_booked_trip_with_no_cost_is_reported_as_uncosted(db, world):
    """The check that keeps every other figure honest: a campaign with a blank
    fare must not look cheaper than it is."""
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi], cost="1000.00")
    trip(db, monsoon, [meera], cost=None)

    result = analytics.overview(db, TENANT)
    assert result["spent"] == "1000.00"
    assert result["uncosted"] == 1
    assert result["booked_travellers"] == 2


def test_the_average_ignores_uncosted_rows(db, world):
    """Dividing by rows that have no number would understate the average and make
    travel look cheaper the worse the bookkeeping got."""
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi], cost="1000.00")
    trip(db, monsoon, [meera], cost=None)
    assert analytics.overview(db, TENANT)["average_per_traveller"] == "1000.00"


def test_a_group_trip_counts_every_traveller(db, world):
    """Counting requests instead would report a two-person trip as one fare."""
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi, meera], cost="600.00")

    result = analytics.overview(db, TENANT)
    assert result["spent"] == "1200.00"
    assert result["booked_travellers"] == 2
    assert result["trips"] == 1


def test_spend_is_grouped_by_campaign(db, world):
    monsoon, coastal, ravi, meera = world
    trip(db, monsoon, [ravi], cost="1000.00")
    trip(db, coastal, [meera], cost="2500.00")

    rows = {r["code"]: r for r in analytics.by_campaign(db, TENANT)}
    assert rows["MON-1"]["spent"] == "1000.00"
    assert rows["CST-1"]["spent"] == "2500.00"
    # Biggest spend first: the campaign an admin is most likely looking for.
    assert analytics.by_campaign(db, TENANT)[0]["code"] == "CST-1"


def test_campaign_rows_carry_their_own_uncosted_count(db, world):
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi], cost=None)
    trip(db, monsoon, [meera], cost="100.00")

    row = analytics.by_campaign(db, TENANT)[0]
    assert row["uncosted"] == 1
    assert row["spent"] == "100.00"


def test_spend_is_grouped_by_request_type(db, world):
    monsoon, _, ravi, meera = world
    trip(db, monsoon, [ravi], cost="4000.00", kind=RequestType.LONG_DISTANCE)
    trip(db, monsoon, [meera], cost="900.00", kind=RequestType.HOTEL)

    rows = {r["request_type"]: r for r in analytics.by_type(db, TENANT)}
    assert rows["LONG_DISTANCE"]["spent"] == "4000.00"
    assert rows["HOTEL"]["spent"] == "900.00"
    # Every type appears, even at zero, so a chart does not silently omit one.
    assert rows["LOCAL_CAB"]["spent"] == "0.00"


def this_year():
    today = clock.local_today()
    return date(today.year, 1, 1), date(today.year, 12, 31)


def test_monthly_spend_includes_quiet_months_as_zero(db, world):
    """A chart that omits an empty month misrepresents the trend."""
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00", days_out=0)

    since, until = this_year()
    result = analytics.trend(analytics.rows_for(db, TENANT), since=since, until=until)
    assert result["grain"] == "month"
    assert len(result["points"]) == 12
    assert result["points"][0]["period"] < result["points"][-1]["period"]   # oldest first
    assert sum(p["spent"] != "0.00" for p in result["points"]) == 1


def test_deployment_counts_people_by_campaign_location(db, world):
    """Where the work is, not where staff are based - a Hyderabad auditor on a
    Pune campaign is deployed to Maharashtra."""
    monsoon, coastal, ravi, meera = world
    trip(db, monsoon, [ravi, meera], cost="500.00", days_out=5)
    trip(db, coastal, [ravi], cost="500.00", days_out=5)

    rows = {r["location"]: r for r in analytics.deployment(db, TENANT)}
    assert rows["Maharashtra"]["people"] == 2
    assert rows["Kerala"]["people"] == 1


def test_deployment_looks_forward_not_back(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="500.00", days_out=-10)
    assert analytics.deployment(db, TENANT) == []


def test_the_uncosted_worklist_names_what_is_missing(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost=None)

    rows = analytics.uncosted_bookings(db, TENANT)
    assert len(rows) == 1
    assert rows[0]["traveller_name"] == "Ravi Kumar"
    assert rows[0]["project_code"] == "MON-1"


def test_a_fully_costed_book_leaves_an_empty_worklist(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00")
    assert analytics.uncosted_bookings(db, TENANT) == []


def test_archived_campaigns_keep_their_history(db, world):
    """Projects are archived, never deleted, precisely so section 6 can still
    report on them."""
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00")
    monsoon.status = ProjectStatus.ARCHIVED
    db.commit()

    rows = {r["code"]: r for r in analytics.by_campaign(db, TENANT)}
    assert rows["MON-1"]["spent"] == "1000.00"
    assert rows["MON-1"]["status"] == "ARCHIVED"


def test_totals_stay_exact_across_many_split_trips(db, world):
    """The end-to-end version of the split property.

    Ten two-person trips, each splitting 1000 rupees, must total exactly 10,000 -
    no drift accumulating one rounded paisa at a time, which is how a report
    starts disagreeing with the invoices behind it.
    """
    monsoon, _, ravi, meera = world
    for _ in range(10):
        left, right = costs.split_evenly("1000", 2)
        row = trip(db, monsoon, [ravi, meera], cost=str(left))
        row.travellers[1].cost_amount = right
        db.commit()

    assert Decimal(analytics.overview(db, TENANT)["spent"]) == Decimal("10000.00")


def test_an_odd_split_across_three_trips_still_totals_exactly(db, world):
    """1000 across three is the awkward case: 333.34 + 333.33 + 333.33. Three of
    those trips must come to 3000.00, not 2999.97."""
    monsoon, _, ravi, meera = world
    for _ in range(3):
        shares = costs.split_evenly("1000", 3)
        row = trip(db, monsoon, [ravi, meera], cost=str(shares[0]))
        row.travellers[1].cost_amount = shares[1] + shares[2]
        db.commit()

    assert Decimal(analytics.overview(db, TENANT)["spent"]) == Decimal("3000.00")


def test_all_time_stretches_to_cover_future_bookings(db, world):
    """No dates means every booking, ahead as well as behind - field teams book
    weeks out - and the chart starts at the first trip, not in the year 2000."""
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00", days_out=-100)
    trip(db, monsoon, [ravi], cost="2500.00", days_out=100)

    result = analytics.trend(analytics.rows_for(db, TENANT), since=None, until=None)
    today = clock.local_today()
    assert today - timedelta(days=101) <= result["since"] <= today - timedelta(days=99)
    assert result["until"] >= today + timedelta(days=99)
    assert result["grain"] == "month"
    assert sum(Decimal(p["spent"]) for p in result["points"]) == Decimal("3500.00")


def test_with_nothing_booked_the_chart_is_the_last_30_days(db, world):
    result = analytics.trend([], since=None, until=None)
    assert result["grain"] == "day"
    assert len(result["points"]) == 30
    assert result["until"] == clock.local_today()


def test_future_booked_travel_appears_in_the_chart(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="2500.00", days_out=40)

    today = clock.local_today()
    result = analytics.trend(analytics.rows_for(db, TENANT), since=today, until=None)
    assert any(p["spent"] == "2500.00" for p in result["points"]), result["points"]


def test_travel_beyond_the_window_is_excluded_rather_than_piled_on_the_last_bucket(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="9999.00", days_out=400)

    since, until = this_year()
    result = analytics.trend(analytics.rows_for(db, TENANT), since=since, until=until)
    assert all(p["spent"] == "0.00" for p in result["points"])


# ---------------------------------------------------------------------------
# Filters: every figure on the cost page follows the same slice
# ---------------------------------------------------------------------------


def place(row, *, state=None, city=None, origin_state="Telangana"):
    """Give a trip from trip() a destination."""
    if row.request_type is RequestType.HOTEL:
        row.hotel_state, row.hotel_city = state, city
    elif row.request_type is RequestType.LOCAL_CAB:
        row.origin_state, row.destination_state, row.drop_city = origin_state, state, city
    else:
        row.origin_state, row.destination_state, row.destination = origin_state, state, city


def everything(db, filters):
    rows = analytics.rows_for(db, TENANT, filters)
    return {
        "overview": analytics.overview(db, TENANT, rows=rows),
        "by_campaign": analytics.by_campaign(db, TENANT, rows=rows),
        "by_type": analytics.by_type(db, TENANT, rows=rows),
        "by_person": analytics.by_person(db, TENANT, rows=rows),
        "by_state": analytics.by_state(db, TENANT, rows=rows),
        "by_city": analytics.by_city(db, TENANT, rows=rows),
        "uncosted": analytics.uncosted_bookings(db, TENANT, rows=rows),
    }


def test_the_date_range_slices_every_cost_figure(db, world):
    monsoon, coastal, ravi, meera = world
    inside = trip(db, monsoon, [ravi], cost="1000.00", days_out=5)
    place(inside, state="Maharashtra", city="Pune")
    outside = trip(db, coastal, [meera], cost=None, days_out=60)
    place(outside, state="Kerala", city="Kochi")
    db.commit()

    today = clock.local_today()
    got = everything(db, Filters(since=today, until=today + timedelta(days=30)))
    assert got["overview"]["spent"] == "1000.00"
    assert got["overview"]["uncosted"] == 0
    assert [c["code"] for c in got["by_campaign"]] == ["MON-1"]
    assert {t["request_type"]: t["travellers"] for t in got["by_type"]}["LONG_DISTANCE"] == 1
    assert [p["full_name"] for p in got["by_person"]] == ["Ravi Kumar"]
    assert [s["label"] for s in got["by_state"]] == ["Maharashtra"]
    assert [c["label"] for c in got["by_city"]] == ["Pune"]
    assert got["uncosted"] == []


def test_a_hotel_is_costed_by_check_in(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="900.00", days_out=10, kind=RequestType.HOTEL)

    today = clock.local_today()
    hit = Filters(since=today + timedelta(days=10), until=today + timedelta(days=10))
    miss = Filters(since=today + timedelta(days=11), until=today + timedelta(days=20))
    assert analytics.overview(db, TENANT, hit)["spent"] == "900.00"
    assert analytics.overview(db, TENANT, miss)["spent"] == "0.00"


def test_campaign_and_employee_filters_slice_costs(db, world):
    monsoon, coastal, ravi, meera = world
    trip(db, monsoon, [ravi, meera], cost="600.00")
    trip(db, coastal, [meera], cost="400.00")

    mine = everything(db, Filters(user_id=ravi.id))
    assert mine["overview"]["spent"] == "600.00"
    assert [p["full_name"] for p in mine["by_person"]] == ["Ravi Kumar"]

    coast = everything(db, Filters(project_id=coastal.id))
    assert coast["overview"]["spent"] == "400.00"
    assert [c["code"] for c in coast["by_campaign"]] == ["CST-1"]

    people = {p["full_name"]: p for p in everything(db, Filters())["by_person"]}
    assert people["Meera Iyer"]["spent"] == "1000.00"
    assert people["Meera Iyer"]["trips"] == 2


@pytest.mark.parametrize(
    "status,spent,committed",
    [
        (TravellerStatus.BOOKED, "750.00", "0.00"),
        (TravellerStatus.APPROVED, "0.00", "750.00"),
        (TravellerStatus.PENDING, "0.00", "0.00"),
    ],
)
def test_only_booked_counts_as_spend_under_filters(db, world, status, spent, committed):
    monsoon, _, ravi, _ = world
    row = trip(db, monsoon, [ravi], status=status, cost="750.00")
    place(row, state="Maharashtra", city="Pune")
    db.commit()

    result = analytics.overview(db, TENANT, Filters(state="Maharashtra"))
    assert (result["spent"], result["committed"]) == (spent, committed)


def test_spend_by_state_and_city_groups_by_destination(db, world):
    monsoon, _, ravi, meera = world
    flight = trip(db, monsoon, [ravi], cost="4000.00")
    place(flight, state="Maharashtra", city="Pune")
    hotel = trip(db, monsoon, [meera], cost="900.00", kind=RequestType.HOTEL)
    place(hotel, state="Tamil Nadu", city="Chennai")
    old_cab = trip(db, monsoon, [ravi], cost="300.00", kind=RequestType.LOCAL_CAB)
    old_cab.destination = "Nagpur Station"   # an address, with no city recorded
    place(old_cab, state="Maharashtra", city=None)
    db.commit()

    got = everything(db, Filters())
    states = {s["label"]: s["spent"] for s in got["by_state"]}
    assert states == {"Maharashtra": "4300.00", "Tamil Nadu": "900.00"}
    cities = {c["label"]: c for c in got["by_city"]}
    assert set(cities) == {"Pune", "Chennai", analytics.NO_CITY}
    assert cities[analytics.NO_CITY]["city"] is None
    assert cities["Pune"]["state"] == "Maharashtra"
    # Each booked row lands in exactly one bar, so the bars add up.
    total = Decimal(got["overview"]["spent"])
    assert sum(Decimal(s["spent"]) for s in got["by_state"]) == total
    assert sum(Decimal(c["spent"]) for c in got["by_city"]) == total


def test_the_city_filter_is_the_destination_city(db, world):
    monsoon, _, ravi, meera = world
    to_pune = trip(db, monsoon, [ravi], cost="1000.00")
    place(to_pune, state="Maharashtra", city="Pune")
    from_pune = trip(db, monsoon, [meera], cost="2000.00")
    from_pune.origin = "Pune"
    place(from_pune, state="Telangana", city="Hyderabad", origin_state="Maharashtra")
    db.commit()

    assert analytics.overview(db, TENANT, Filters(city="Pune"))["spent"] == "1000.00"
    assert analytics.overview(db, TENANT, Filters(state="Maharashtra"))["spent"] == "1000.00"


def test_deployment_uses_trip_state_and_follows_filters(db, world):
    monsoon, coastal, ravi, meera = world
    headed = trip(db, coastal, [ravi], cost="500.00", days_out=5)
    place(headed, state="Tamil Nadu", city="Chennai")
    trip(db, monsoon, [meera], cost="500.00", days_out=5)   # no state: campaign's location
    db.commit()

    rows = {r["location"]: r["people"] for r in analytics.deployment(db, TENANT)}
    assert rows == {"Tamil Nadu": 1, "Maharashtra": 1}

    only_ravi = analytics.deployment(db, TENANT, Filters(user_id=ravi.id))
    assert [r["location"] for r in only_ravi] == ["Tamil Nadu"]

    today = clock.local_today()
    past = Filters(since=today - timedelta(days=30), until=today - timedelta(days=1))
    assert analytics.deployment(db, TENANT, past) == []


def test_deployment_falls_back_to_the_campaign_state(db, world):
    monsoon, _, ravi, _ = world
    monsoon.state = "Goa"
    trip(db, monsoon, [ravi], cost="500.00", days_out=5)
    assert [r["location"] for r in analytics.deployment(db, TENANT)] == ["Goa"]


def test_deployed_people_counts_someone_in_two_states_once(db, world):
    monsoon, coastal, ravi, meera = world
    place(trip(db, coastal, [ravi], cost="500.00", days_out=5), state="Karnataka")
    place(trip(db, monsoon, [ravi], status=TravellerStatus.APPROVED, cost=None, days_out=8),
          state="Maharashtra")
    trip(db, monsoon, [meera], cost="500.00", days_out=-3)   # already back: not deployed
    db.commit()

    rows = analytics.deployment(db, TENANT)
    assert sum(r["people"] for r in rows) == 2
    assert analytics.deployed_people(db, TENANT) == 1
