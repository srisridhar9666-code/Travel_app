"""
The conflict rules (addendum B6).

The SOW says only "validates the dates against existing itineraries", so every
rule here is a decision rather than a transcription - which is exactly why they
are pinned down in tests. The cases that matter most are the ones that must
*not* fire: the airport cab against its own flight, and the hotel checkout that
meets another hotel check-in on the same day.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine

from app.core.enums import (
    ConflictKind,
    ConflictSeverity,
    Gender,
    RequestType,
    Role,
    TravellerStatus,
)
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import conflicts
from app.services.conflicts import Itinerary

TENANT = "designboxed"


def dt(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute)


def d(day: int) -> date:
    return date(2026, 10, day)


def stay(check_in: int, check_out: int | None, city: str = "Mumbai", rid: int | None = None):
    return Itinerary(
        request_type=RequestType.HOTEL,
        request_id=rid,
        hotel_city=city,
        check_in=d(check_in),
        check_out=d(check_out) if check_out else None,
    )


def journey(
    start: datetime,
    end: datetime | None = None,
    *,
    kind: RequestType = RequestType.LONG_DISTANCE,
    origin: str = "Hyderabad",
    destination: str = "Mumbai",
    rid: int | None = None,
):
    return Itinerary(
        request_type=kind,
        request_id=rid,
        origin=origin,
        destination=destination,
        start_at=start,
        end_at=end,
    )


# ---------------------------------------------------------------------------
# Hotels: overlap is counted by night
# ---------------------------------------------------------------------------


def test_stays_sharing_a_night_conflict():
    verdict = conflicts.compare(stay(3, 6), stay(5, 8, city="Pune", rid=1))
    assert verdict is not None
    assert verdict[0] is ConflictKind.OVERLAPPING_STAY


def test_checkout_day_meeting_a_checkin_day_is_not_a_conflict():
    """Out of one hotel and into the next on the 5th is a move, not a clash.
    This is the case a naive timestamp comparison gets wrong."""
    assert conflicts.compare(stay(5, 8), stay(2, 5, rid=1)) is None
    assert conflicts.compare(stay(2, 5), stay(5, 8, rid=1)) is None


def test_a_stay_fully_inside_another_conflicts():
    assert conflicts.compare(stay(4, 5), stay(1, 10, rid=1)) is not None


def test_identical_stay_in_the_same_city_is_reported_as_a_duplicate():
    verdict = conflicts.compare(stay(3, 6, city="Mumbai"), stay(3, 6, city="mumbai ", rid=1))
    assert verdict[0] is ConflictKind.DUPLICATE_REQUEST


def test_same_dates_in_a_different_city_is_an_overlap_not_a_duplicate():
    verdict = conflicts.compare(stay(3, 6, city="Mumbai"), stay(3, 6, city="Delhi", rid=1))
    assert verdict[0] is ConflictKind.OVERLAPPING_STAY


def test_a_stay_with_no_checkout_still_occupies_its_first_night():
    """Otherwise the interval is empty and the stay clashes with nothing."""
    assert conflicts.compare(stay(3, None), stay(3, 4, rid=1)) is not None
    assert conflicts.compare(stay(3, None), stay(4, 5, rid=1)) is None


def test_a_stay_that_ends_the_day_it_starts_is_treated_as_one_night():
    assert conflicts.compare(stay(3, 3), stay(3, 4, rid=1)) is not None


def test_stays_that_do_not_touch_are_clear():
    assert conflicts.compare(stay(1, 3), stay(10, 12, rid=1)) is None


# ---------------------------------------------------------------------------
# Journeys: overlap is counted by datetime window
# ---------------------------------------------------------------------------


def test_overlapping_journeys_conflict():
    verdict = conflicts.compare(
        journey(dt(3, 8), dt(3, 14)),
        journey(dt(3, 12), dt(3, 18), origin="Delhi", destination="Goa", rid=1),
    )
    assert verdict[0] is ConflictKind.OVERLAPPING_TRAVEL


def test_journeys_that_merely_touch_still_conflict():
    """Closed intervals: landing at 14:00 and departing at 14:00 is one person
    in two places, however tidy it looks on a form."""
    assert (
        conflicts.compare(
            journey(dt(3, 14), dt(3, 18)),
            journey(dt(3, 8), dt(3, 14), origin="Delhi", destination="Goa", rid=1),
        )
        is not None
    )


def test_journeys_on_different_days_are_clear():
    assert (
        conflicts.compare(journey(dt(3, 8), dt(3, 12)), journey(dt(9, 8), dt(9, 12), rid=1))
        is None
    )


def test_a_journey_with_no_arrival_time_is_a_point_in_time():
    assert conflicts.compare(journey(dt(3, 9)), journey(dt(3, 9), rid=1)) is not None
    assert conflicts.compare(journey(dt(3, 9)), journey(dt(3, 11), dt(3, 15), rid=1)) is None


def test_the_same_route_on_the_same_day_is_a_duplicate():
    verdict = conflicts.compare(
        journey(dt(3, 8), dt(3, 12)),
        journey(dt(3, 9), dt(3, 13), rid=1),
    )
    assert verdict[0] is ConflictKind.DUPLICATE_REQUEST


def test_the_same_route_a_week_later_is_not_a_duplicate():
    assert conflicts.compare(journey(dt(3, 8), dt(3, 12)), journey(dt(10, 8), dt(10, 12), rid=1)) is None


# ---------------------------------------------------------------------------
# The airport run - the rule the SOW would have got wrong
# ---------------------------------------------------------------------------


def test_a_cab_on_the_day_of_your_own_flight_is_not_a_conflict():
    flight = journey(dt(3, 11), dt(3, 14), rid=1)
    cab = journey(
        dt(3, 8),
        dt(3, 10),
        kind=RequestType.LOCAL_CAB,
        origin="Home",
        destination="Airport",
    )
    assert conflicts.compare(cab, flight) is None
    assert conflicts.compare(flight, cab) is None


def test_the_exemption_holds_even_when_the_cab_overlaps_the_flight_window():
    """We cannot tell a late airport run from a genuine clash, and warning on
    every airport run would train admins to ignore the warning."""
    flight = journey(dt(3, 8), dt(3, 14), rid=1)
    cab = journey(dt(3, 9), dt(3, 10), kind=RequestType.LOCAL_CAB, origin="A", destination="B")
    assert conflicts.compare(cab, flight) is None


def test_the_cab_exemption_is_total_in_practice():
    """A cab whose window overlaps a long-distance leg necessarily shares a
    calendar day with it, so the exemption swallows every overlapping pair -
    including an overnight train. Stated here so the breadth of the rule is a
    decision on the record rather than a side effect."""
    overnight = journey(dt(3, 20), dt(4, 6), rid=1)

    for cab_start, cab_end in ((dt(3, 18), dt(3, 21)), (dt(4, 5), dt(4, 7))):
        cab = journey(
            cab_start, cab_end, kind=RequestType.LOCAL_CAB, origin="A", destination="B"
        )
        assert conflicts.compare(cab, overnight) is None

    # Two days later the windows do not meet at all, so there is nothing to exempt.
    assert (
        conflicts.compare(
            journey(dt(6, 9), dt(6, 10), kind=RequestType.LOCAL_CAB, origin="A", destination="B"),
            overnight,
        )
        is None
    )


def test_two_cabs_at_the_same_time_conflict():
    """The exemption is cab-against-long-distance only. One person cannot be in
    two cabs."""
    verdict = conflicts.compare(
        journey(dt(3, 9), dt(3, 10), kind=RequestType.LOCAL_CAB, origin="A", destination="B"),
        journey(
            dt(3, 9, 30),
            dt(3, 11),
            kind=RequestType.LOCAL_CAB,
            origin="C",
            destination="D",
            rid=1,
        ),
    )
    assert verdict[0] is ConflictKind.OVERLAPPING_TRAVEL


# ---------------------------------------------------------------------------
# Shapes that never compete
# ---------------------------------------------------------------------------


def test_a_hotel_never_conflicts_with_a_journey():
    """You fly somewhere in order to sleep there."""
    assert conflicts.compare(stay(3, 6), journey(dt(3, 8), dt(3, 12), rid=1)) is None
    assert conflicts.compare(journey(dt(3, 8), dt(3, 12)), stay(3, 6, rid=1)) is None


def test_a_request_never_conflicts_with_itself():
    existing = stay(3, 6, rid=7)
    candidate = Itinerary(
        request_type=RequestType.HOTEL,
        request_id=7,
        hotel_city="Mumbai",
        check_in=d(3),
        check_out=d(6),
    )
    assert conflicts.compare(candidate, existing) is None


def test_undated_trips_are_skipped_rather_than_guessed_at():
    bare = Itinerary(request_type=RequestType.LONG_DISTANCE, origin="A", destination="B")
    assert conflicts.compare(bare, journey(dt(3, 9), rid=1)) is None


# ---------------------------------------------------------------------------
# Against the database: which rows occupy a calendar
# ---------------------------------------------------------------------------


@pytest.fixture
def people(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
    ravi = User(
        tenant_id=TENANT,
        email="ravi@designboxed.com",
        full_name="Ravi Kumar",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        password_hash="x",
    )
    meera = User(
        tenant_id=TENANT,
        email="meera@designboxed.com",
        full_name="Meera Iyer",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    db.add_all([project, ravi, meera])
    db.commit()
    return project, ravi, meera


def add_stay(db, project, users, *, check_in, check_out, status, draft=False, cancelled=False):
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=users[0].id,
        hotel_city="Mumbai",
        check_in=d(check_in),
        check_out=d(check_out),
        is_draft=draft,
        is_cancelled=cancelled,
    )
    row.travellers = [RequestTraveller(user_id=u.id, status=status) for u in users]
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize(
    "status,expected",
    [
        (TravellerStatus.PENDING, 1),
        (TravellerStatus.APPROVED, 1),
        (TravellerStatus.BOOKED, 1),
        (TravellerStatus.REJECTED, 0),
        (TravellerStatus.CANCELLED, 0),
    ],
)
def test_only_active_traveller_statuses_occupy_the_calendar(db, people, status, expected):
    project, ravi, _ = people
    add_stay(db, project, [ravi], check_in=3, check_out=6, status=status)

    found = conflicts.detect(
        db, tenant_id=TENANT, candidate=stay(4, 7), user_ids=[ravi.id]
    )
    assert len(found) == expected


def test_a_draft_never_warns_anyone(db, people):
    """Drafts are private, so they must not surface through a conflict message
    aimed at someone who cannot see them."""
    project, ravi, _ = people
    add_stay(db, project, [ravi], check_in=3, check_out=6, status=TravellerStatus.PENDING, draft=True)
    assert conflicts.detect(db, tenant_id=TENANT, candidate=stay(4, 7), user_ids=[ravi.id]) == []


def test_a_cancelled_request_frees_the_calendar(db, people):
    project, ravi, _ = people
    add_stay(
        db, project, [ravi], check_in=3, check_out=6, status=TravellerStatus.PENDING, cancelled=True
    )
    assert conflicts.detect(db, tenant_id=TENANT, candidate=stay(4, 7), user_ids=[ravi.id]) == []


def test_each_co_traveller_is_checked_separately(db, people):
    project, ravi, meera = people
    add_stay(db, project, [meera], check_in=3, check_out=6, status=TravellerStatus.APPROVED)

    found = conflicts.detect(
        db, tenant_id=TENANT, candidate=stay(4, 7), user_ids=[ravi.id, meera.id]
    )
    assert [c.user_id for c in found] == [meera.id]
    assert "Meera Iyer" in found[0].message
    assert found[0].severity is ConflictSeverity.WARNING


def test_editing_a_request_does_not_conflict_with_its_own_stored_row(db, people):
    project, ravi, _ = people
    existing = add_stay(db, project, [ravi], check_in=3, check_out=6, status=TravellerStatus.PENDING)

    found = conflicts.detect(
        db,
        tenant_id=TENANT,
        candidate=stay(4, 7, rid=existing.id),
        user_ids=[ravi.id],
        exclude_request_id=existing.id,
    )
    assert found == []


def test_a_conflict_is_never_blocking(db, people):
    """Addendum B6: the whole module warns. Nothing here can stop a submission."""
    project, ravi, _ = people
    add_stay(db, project, [ravi], check_in=3, check_out=6, status=TravellerStatus.BOOKED)
    found = conflicts.detect(db, tenant_id=TENANT, candidate=stay(4, 7), user_ids=[ravi.id])
    assert all(c.severity is ConflictSeverity.WARNING for c in found)
