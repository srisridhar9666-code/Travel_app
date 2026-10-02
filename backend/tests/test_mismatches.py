"""
Where a ticket disagrees with the request it was uploaded against.

These were only ever exercised through the Phase 5 smoke, which calls the live
model - so whether the rules ran at all depended on what Gemini happened to
extract that morning. The rules are deterministic; the extraction is not. They
belong here.

Every one of these is advisory. A ticket that does not match is usually a real
problem - the wrong leg, the wrong day, the wrong person - but occasionally it is
a change the admin already knows about, so the reviewer is informed rather than
blocked.
"""
from datetime import date, datetime

import pytest

from app.core.enums import RequestType, Role, TravellerStatus
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.routers.tickets import _mismatches

TENANT = "designboxed"


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
    ravi = User(
        tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
        role=Role.GROUND_STAFF, password_hash="x",
    )
    db.add_all([project, ravi])
    db.commit()
    return project, ravi


def build(db, project, user, *, request_fields, ticket_fields):
    row = TravelRequest(
        tenant_id=TENANT, project_id=project.id, requester_id=user.id, **request_fields
    )
    row.travellers = [RequestTraveller(user_id=user.id, status=TravellerStatus.APPROVED)]
    db.add(row)
    db.commit()

    ticket = TicketDocument(
        tenant_id=TENANT,
        request_id=row.id,
        traveller_id=row.travellers[0].id,
        **ticket_fields,
    )
    db.add(ticket)
    db.commit()
    return ticket


FLIGHT_REQUEST = dict(
    request_type=RequestType.LONG_DISTANCE,
    origin="Hyderabad",
    destination="Mumbai",
    start_at=datetime(2027, 3, 10, 6, 45),
    end_at=datetime(2027, 3, 10, 8, 20),
)

HOTEL_REQUEST = dict(
    request_type=RequestType.HOTEL,
    hotel_city="Mumbai",
    check_in=date(2027, 3, 10),
    check_out=date(2027, 3, 13),
)


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------


def test_a_matching_ticket_flags_nothing(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad", destination="Mumbai",
            depart_at=datetime(2027, 3, 10, 6, 45), passenger_name="RAVI KUMAR",
        ),
    )
    assert _mismatches(ticket) == []


def test_an_airport_code_in_brackets_is_not_a_mismatch(db, world):
    """Tickets say "Hyderabad (HYD)" where the request says "Hyderabad". One
    containing the other is the same place."""
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad (HYD)", destination="Mumbai (BOM)",
            depart_at=datetime(2027, 3, 10, 6, 45), passenger_name="RAVI KUMAR",
        ),
    )
    assert _mismatches(ticket) == []


def test_the_wrong_origin_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(origin="Pune", destination="Mumbai", passenger_name="RAVI KUMAR"),
    )
    found = _mismatches(ticket)
    assert len(found) == 1
    assert "departs Pune" in found[0]


def test_the_wrong_destination_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(origin="Hyderabad", destination="Nagpur", passenger_name="RAVI KUMAR"),
    )
    assert any("arrives Nagpur" in m for m in _mismatches(ticket))


def test_the_wrong_departure_day_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad", destination="Mumbai",
            depart_at=datetime(2027, 5, 2, 6, 45), passenger_name="RAVI KUMAR",
        ),
    )
    assert any("departs on 2027-05-02" in m for m in _mismatches(ticket))


def test_a_different_time_on_the_right_day_is_not_flagged(db, world):
    """Airlines move departures by an hour constantly. Flagging that would make
    the warning meaningless."""
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad", destination="Mumbai",
            depart_at=datetime(2027, 3, 10, 9, 30), passenger_name="RAVI KUMAR",
        ),
    )
    assert _mismatches(ticket) == []


def test_every_difference_is_reported_not_just_the_first(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Pune", destination="Nagpur",
            depart_at=datetime(2027, 5, 2, 7, 0), passenger_name="RAVI KUMAR",
        ),
    )
    assert len(_mismatches(ticket)) == 3


# ---------------------------------------------------------------------------
# Hotels
# ---------------------------------------------------------------------------


def test_matching_hotel_dates_flag_nothing(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=HOTEL_REQUEST,
        ticket_fields=dict(
            check_in=date(2027, 3, 10), check_out=date(2027, 3, 13),
            passenger_name="RAVI KUMAR",
        ),
    )
    assert _mismatches(ticket) == []


def test_a_different_check_in_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=HOTEL_REQUEST,
        ticket_fields=dict(check_in=date(2027, 3, 11), passenger_name="RAVI KUMAR"),
    )
    assert any("check-in" in m for m in _mismatches(ticket))


def test_a_different_check_out_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=HOTEL_REQUEST,
        ticket_fields=dict(check_out=date(2027, 3, 15), passenger_name="RAVI KUMAR"),
    )
    assert any("check-out" in m for m in _mismatches(ticket))


def test_a_hotel_ticket_is_not_judged_on_route(db, world):
    """A confirmation carries no origin, and comparing one would flag every stay."""
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=HOTEL_REQUEST,
        ticket_fields=dict(
            check_in=date(2027, 3, 10), check_out=date(2027, 3, 13),
            origin="somewhere", destination="elsewhere", passenger_name="RAVI KUMAR",
        ),
    )
    assert _mismatches(ticket) == []


# ---------------------------------------------------------------------------
# The name on the ticket - the one that has to match at the gate
# ---------------------------------------------------------------------------


def test_a_ticket_in_someone_elses_name_is_flagged(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad", destination="Mumbai",
            depart_at=datetime(2027, 3, 10, 6, 45), passenger_name="ARJUN NAIR",
        ),
    )
    found = _mismatches(ticket)
    assert len(found) == 1
    assert "in the name of ARJUN NAIR" in found[0]
    assert "Ravi Kumar" in found[0]


def test_the_name_check_ignores_case_and_ordering(db, world):
    """Airlines print "KUMAR/RAVI MR" as often as "Ravi Kumar"."""
    project, ravi = world
    for printed in ("RAVI KUMAR", "ravi kumar", "KUMAR/RAVI MR", "Mr Ravi Kumar"):
        ticket = build(
            db, project, ravi,
            request_fields=FLIGHT_REQUEST,
            ticket_fields=dict(
                origin="Hyderabad", destination="Mumbai",
                depart_at=datetime(2027, 3, 10, 6, 45), passenger_name=printed,
            ),
        )
        assert _mismatches(ticket) == [], printed


def test_no_passenger_name_on_the_ticket_is_not_a_mismatch(db, world):
    """The model does not always read the name. An absent field is not evidence
    of a wrong one, and guessing would cry wolf."""
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=FLIGHT_REQUEST,
        ticket_fields=dict(
            origin="Hyderabad", destination="Mumbai",
            depart_at=datetime(2027, 3, 10, 6, 45), passenger_name=None,
        ),
    )
    assert _mismatches(ticket) == []


# ---------------------------------------------------------------------------
# Absent data
# ---------------------------------------------------------------------------


def test_an_unread_ticket_flags_nothing(db, world):
    """A failed extraction has no fields to compare, and a wall of warnings on
    top of "could not read this" would be noise."""
    project, ravi = world
    ticket = build(db, project, ravi, request_fields=FLIGHT_REQUEST, ticket_fields={})
    assert _mismatches(ticket) == []


def test_a_request_without_dates_is_not_judged_on_them(db, world):
    project, ravi = world
    ticket = build(
        db, project, ravi,
        request_fields=dict(
            request_type=RequestType.LOCAL_CAB, origin="Bandra", destination="Airport",
            start_at=datetime(2027, 3, 10, 5, 0),
        ),
        ticket_fields=dict(origin="Bandra", destination="Airport", passenger_name="RAVI KUMAR"),
    )
    assert _mismatches(ticket) == []
