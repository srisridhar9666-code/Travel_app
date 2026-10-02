"""
A cab's pickup and drop: the street address as typed, plus the state and city
it is in, picked from the same list a flight's cities come from.

The address alone ("Banjara Hills") cannot be grouped, filtered by state or
counted as a place visited; the city beside it can.
"""
import pytest
from pydantic import ValidationError

from app.core.enums import ProjectStatus, RequestType, Role, TravellerStatus
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import RequestBody
from app.services import insights, locations
from app.services import requests as svc

TENANT = "designboxed"

CAB = dict(
    request_type="LOCAL_CAB", project_id=1, travel_reason="Store audit",
    origin="Banjara Hills", destination="RGIA Airport", start_at="2026-10-05T06:00",
    origin_state="Telangana", pickup_city="Hyderabad",
    destination_state="Telangana", drop_city="Hyderabad",
)


class TestTheForm:
    def test_a_cab_keeps_its_addresses_and_gains_cities(self):
        body = RequestBody(**CAB)
        assert (body.origin, body.pickup_city) == ("Banjara Hills", "Hyderabad")
        assert (body.destination, body.drop_city) == ("RGIA Airport", "Hyderabad")

    @pytest.mark.parametrize("missing", ["origin_state", "pickup_city"])
    def test_a_cab_needs_its_pickup_state_and_city(self, missing):
        with pytest.raises(ValidationError, match="picks up"):
            RequestBody(**{**CAB, missing: None})

    @pytest.mark.parametrize("missing", ["destination_state", "drop_city"])
    def test_a_cab_needs_its_drop_state_and_city(self, missing):
        with pytest.raises(ValidationError, match="drops"):
            RequestBody(**{**CAB, missing: None})

    def test_a_flight_does_not_carry_cab_cities(self):
        body = RequestBody(**{**CAB, "request_type": "LONG_DISTANCE", "mode": "FLIGHT",
                              "origin": "Hyderabad", "destination": "Pune"})
        assert body.pickup_city is None and body.drop_city is None

    def test_a_hotel_drops_the_route_and_its_states(self):
        """Switching the form from cab to hotel must not leave a state behind
        for the state filter to find."""
        body = RequestBody(**{**CAB, "request_type": "HOTEL", "hotel_city": "Pune",
                              "hotel_state": "Maharashtra", "check_in": "2026-10-05"})
        assert body.origin_state is None and body.destination_state is None
        assert body.pickup_city is None and body.drop_city is None


class TestCanonicalCities:
    def test_typed_cities_land_on_the_list_and_addresses_are_only_tidied(self, db):
        locations.seed(db, TENANT)
        body = RequestBody(**{**CAB, "origin_state": "telangana", "pickup_city": "hyd",
                              "origin": "  banjara   hills "})
        svc.canonicalise_places(db, TENANT, body)
        assert (body.origin_state, body.pickup_city) == ("Telangana", "Hyderabad")
        assert body.origin == "banjara hills"


@pytest.fixture
def cab(db):
    project = Project(tenant_id=TENANT, name="Monsoon", code="MS-1", status=ProjectStatus.ACTIVE)
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, password_hash="x")
    db.add_all([project, ravi])
    db.flush()
    from datetime import datetime

    request = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.LOCAL_CAB, project_id=project.id,
        requester_id=ravi.id, origin="Banjara Hills", destination="RGIA Airport",
        origin_state="Telangana", pickup_city="Hyderabad",
        destination_state="Telangana", drop_city="Shamshabad",
        start_at=datetime(2026, 8, 10, 6, 0), travel_reason="Airport run",
    )
    db.add(request)
    db.flush()
    db.add(RequestTraveller(request_id=request.id, user_id=ravi.id, status=TravellerStatus.BOOKED))
    db.commit()
    return request


class TestWhereTheCabWent:
    def test_the_route_reads_address_then_city(self, cab):
        assert cab.route_label() == "Banjara Hills, Hyderabad → RGIA Airport, Shamshabad"

    def test_an_address_that_already_names_the_city_is_not_repeated(self, cab):
        cab.origin = "Hyderabad Central"
        assert cab.origin_label == "Hyderabad Central"

    def test_an_old_cab_without_cities_still_reads(self, cab):
        cab.pickup_city = cab.drop_city = None
        assert cab.route_label(" to ") == "Banjara Hills to RGIA Airport"

    def test_the_travel_log_finds_it_by_state_and_city(self, db, cab):
        filters = dict(statuses=insights.TRAVELLED)
        by_state = insights.travel_log(db, TENANT, insights.Filters(state="Telangana", **filters))
        assert by_state["total"] == 1
        entry = by_state["entries"][0]
        assert entry["where"] == "Banjara Hills, Hyderabad → RGIA Airport, Shamshabad"
        assert entry["drop_city"] == "Shamshabad"
        assert insights.travel_log(db, TENANT, insights.Filters(search="shamshabad", **filters))["total"] == 1

    def test_a_place_visited_is_the_city_not_the_street(self, db, cab):
        log = insights.travel_log(db, TENANT, insights.Filters(statuses=insights.TRAVELLED))
        assert log["summary"]["places"] == 1
