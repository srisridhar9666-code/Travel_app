"""
The place list and how picked or typed places are settled.

What matters: every state is there with its constituencies, seeding can run
again without duplicating anything, an empty table repairs itself on first
read, and a place typed under "Other" lands on the listed spelling whenever the
list has it - conflict detection and co-stay compare places exactly.
"""
from sqlalchemy import func, select

from app.models.location import Location
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
