"""
The place list and how picked or typed places are settled.

What matters: every state is there with its constituencies, seeding can run
again without duplicating anything, an empty table repairs itself on first
read, and a place typed under "Other" lands on the listed spelling whenever the
list has it - conflict detection and co-stay compare places exactly.
"""
from datetime import datetime

from sqlalchemy import func, select

from app.core.enums import ProjectStatus, RequestType, Role
from app.models.location import Location
from app.models.project import Project
from app.models.request import TravelRequest
from app.models.user import User
from app.services import locations

TENANT = "designboxed"


def count(db) -> int:
    return db.execute(
        select(func.count(Location.id)).where(Location.tenant_id == TENANT)
    ).scalar_one()


class TestTheList:
    def test_every_state_and_union_territory_is_present(self):
        places = locations.load_places()
        assert len(places) == 36
        assert {"Telangana", "Uttar Pradesh", "Ladakh", "Lakshadweep", "Delhi"} <= set(places)

    def test_no_state_is_empty(self):
        assert all(places for places in locations.load_places().values())

    def test_constituencies_districts_and_cities_are_all_offered(self):
        telangana = locations.load_places()["Telangana"]
        assert "Kukatpally" in telangana        # assembly constituency
        assert "Rangareddy" in telangana        # district
        assert "Hyderabad" in telangana         # city

    def test_one_spelling_per_place(self):
        for state, places in locations.load_places().items():
            keys = [locations.normalise(p) for p in places]
            assert len(keys) == len(set(keys)), state

    def test_accents_fold_like_the_database_collation(self):
        assert locations.normalise("Mahé") == locations.normalise("Mahe")


class TestSeeding:
    def test_seed_is_idempotent(self, db):
        first = locations.seed(db, TENANT)
        db.commit()
        assert first > 4000
        assert locations.seed(db, TENANT) == 0
        assert count(db) == first

    def test_an_existing_spelling_is_not_added_twice(self, db):
        db.add(Location(tenant_id=TENANT, state="Telangana", city="KUKATPALLY"))
        db.commit()
        locations.seed(db, TENANT)
        db.commit()
        rows = db.execute(
            select(Location.city).where(
                Location.tenant_id == TENANT,
                Location.state == "Telangana",
                func.lower(Location.city) == "kukatpally",
            )
        ).all()
        assert len(rows) == 1

    def test_an_empty_table_repairs_itself_on_read(self, db):
        """The form used to show no states at all when the API started before
        the migration that created the table."""
        assert count(db) == 0
        locations.ensure_seeded(db, TENANT)
        assert count(db) > 4000

    def test_a_populated_table_is_left_alone(self, db):
        db.add(Location(tenant_id=TENANT, state="Goa", city="Panaji"))
        db.commit()
        locations.ensure_seeded(db, TENANT)
        assert count(db) == 1


class TestCanonical:
    def test_a_picked_place_is_unchanged(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "Telangana", "Kukatpally") == (
            "Telangana",
            "Kukatpally",
        )

    def test_a_typed_abbreviation_lands_on_the_listed_city(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, None, "hyd") == ("Telangana", "Hyderabad")

    def test_typed_case_and_spacing_are_settled(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "Telangana", "  kukat   pally ") == (
            "Telangana",
            "Kukatpally",
        )

    def test_the_chosen_state_wins_for_a_name_two_states_share(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "Bihar", "aurangabad") == ("Bihar", "Aurangabad")

    def test_a_typed_place_stays_in_the_state_that_was_picked(self, db):
        """Shamshabad, typed under Telangana, is near Hyderabad airport - not
        the listed Shamshabad in Madhya Pradesh."""
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "Telangana", "Shamshabad") == (
            "Telangana",
            "Shamshabad",
        )

    def test_an_abbreviation_resolves_inside_the_picked_state(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "telangana", "hyd") == ("Telangana", "Hyderabad")

    def test_an_ambiguous_name_with_no_state_is_not_guessed(self, db):
        locations.seed(db, TENANT)
        state, place = locations.canonical(db, TENANT, None, "Aurangabad")
        assert state is None and place == "Aurangabad"

    def test_a_place_not_on_the_list_is_kept_as_typed(self, db):
        locations.seed(db, TENANT)
        assert locations.canonical(db, TENANT, "Telangana", "  Gandipet  village ") == (
            "Telangana",
            "Gandipet village",
        )

    def test_blank_stays_blank(self, db):
        assert locations.canonical(db, TENANT, "Goa", "   ") == ("Goa", None)


class TestBackfillingRequestStates:
    """Requests saved before states were captured get one where it is certain."""

    def request(self, db, kind, **fields):
        project = Project(tenant_id=TENANT, name="Monsoon", code=f"MS-{kind}-{len(fields)}",
                          status=ProjectStatus.ACTIVE)
        person = User(tenant_id=TENANT, email=f"{kind.lower()}{len(fields)}@designboxed.com",
                      full_name="Ravi Kumar", role=Role.GROUND_STAFF, password_hash="x")
        db.add_all([project, person])
        db.flush()
        row = TravelRequest(tenant_id=TENANT, request_type=RequestType(kind),
                            project_id=project.id, requester_id=person.id,
                            start_at=datetime(2026, 8, 5, 9), **fields)
        db.add(row)
        db.commit()
        return row

    def test_a_flight_between_listed_cities_gets_both_states(self, db):
        locations.seed(db, TENANT)
        row = self.request(db, "LONG_DISTANCE", origin="Hyderabad", destination="Chennai")
        assert locations.backfill_request_states(db, TENANT) == 1
        assert (row.origin_state, row.destination_state) == ("Telangana", "Tamil Nadu")

    def test_a_hotel_city_gets_its_state(self, db):
        locations.seed(db, TENANT)
        row = self.request(db, "HOTEL", hotel_city="Udaipur")
        locations.backfill_request_states(db, TENANT)
        assert row.hotel_state == "Rajasthan"

    def test_an_abbreviation_is_understood(self, db):
        locations.seed(db, TENANT)
        row = self.request(db, "LONG_DISTANCE", origin="hyd", destination="Pune")
        locations.backfill_request_states(db, TENANT)
        assert (row.origin_state, row.destination_state) == ("Telangana", "Maharashtra")

    def test_a_name_two_states_share_is_left_empty(self, db):
        locations.seed(db, TENANT)
        row = self.request(db, "HOTEL", hotel_city="Aurangabad")
        assert locations.backfill_request_states(db, TENANT) == 0
        assert row.hotel_state is None

    def test_a_cab_is_never_touched(self, db):
        """Its places are street addresses: "Shamshabad" alone would land in
        Madhya Pradesh, which is wrong for Hyderabad's airport."""
        locations.seed(db, TENANT)
        row = self.request(db, "LOCAL_CAB", origin="Road No. 12, Banjara Hills",
                           destination="Shamshabad")
        assert locations.backfill_request_states(db, TENANT) == 0
        assert (row.origin_state, row.destination_state) == (None, None)

    def test_a_recorded_state_is_never_overwritten(self, db):
        locations.seed(db, TENANT)
        row = self.request(db, "LONG_DISTANCE", origin="Hyderabad", origin_state="Somewhere",
                           destination="Chennai")
        locations.backfill_request_states(db, TENANT)
        assert (row.origin_state, row.destination_state) == ("Somewhere", "Tamil Nadu")

    def test_a_second_run_changes_nothing(self, db):
        locations.seed(db, TENANT)
        self.request(db, "LONG_DISTANCE", origin="Hyderabad", destination="Chennai")
        assert locations.backfill_request_states(db, TENANT) == 1
        assert locations.backfill_request_states(db, TENANT) == 0
