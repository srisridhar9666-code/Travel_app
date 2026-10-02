"""
Money: splitting it, and never losing a paisa (addendum B5 / C1).

These are the tests that keep a financial report trustworthy. Every one of them
is about arithmetic that looks obvious and is not: an even split that does not
sum to the total, a float that arrives as 0.30000000000000004, a campaign that
looks cheap because one fare was never entered.
"""
from datetime import timedelta
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


def test_monthly_spend_includes_quiet_months_as_zero(db, world):
    """A chart that omits an empty month misrepresents the trend."""
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="1000.00", days_out=5)

    rows = analytics.by_month(db, TENANT, months=6)
    assert len(rows) == 6
    assert rows[-1]["month"] >= rows[0]["month"]   # oldest first
    assert any(r["spent"] != "0.00" for r in rows)


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


def test_the_monthly_window_is_centred_on_today(db, world):
    """A backwards-only window showed an empty chart for a team that books ahead
    - which is every field team. Half history, half already ticketed."""
    monsoon, _, ravi, _ = world
    rows = analytics.by_month(db, TENANT, months=6)
    this_month = f"{clock.local_today().year:04d}-{clock.local_today().month:02d}"

    assert len(rows) == 6
    assert this_month in [r["month"] for r in rows]
    assert rows[0]["month"] < this_month < rows[-1]["month"]


def test_future_booked_travel_appears_in_the_monthly_chart(db, world):
    """The case the centred window exists for."""
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="2500.00", days_out=40)

    rows = analytics.by_month(db, TENANT, months=6)
    assert any(r["spent"] == "2500.00" for r in rows), [r["spent"] for r in rows]


def test_travel_beyond_the_window_is_excluded_rather_than_piled_on_the_last_month(db, world):
    monsoon, _, ravi, _ = world
    trip(db, monsoon, [ravi], cost="9999.00", days_out=400)

    rows = analytics.by_month(db, TENANT, months=6)
    assert all(r["spent"] == "0.00" for r in rows), [r["spent"] for r in rows]
