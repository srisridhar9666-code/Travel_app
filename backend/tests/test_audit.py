"""
The activity ledger: chaining, diffing, and tamper detection.

Runs against an in-memory SQLite database so the suite needs no MySQL. The hash
logic is dialect-independent; what MySQL adds is DATETIME(6), and the fact that
plain DATETIME would break the chain is the reason `UTCDateTime` exists.
"""
import pytest
from sqlalchemy import create_engine

from app.core.enums import AuditAction, Role
from app.models.audit import GENESIS_HASH
from app.models.user import User
from app.services import audit

TENANT = "designboxed"


@pytest.fixture
def actor(db):
    user = User(
        tenant_id=TENANT,
        email="admin@designboxed.com",
        full_name="System Admin",
        role=Role.SYSTEM_ADMIN,
        password_hash="x",
    )
    db.add(user)
    db.commit()
    return user


def write(db, summary, actor=None, **kwargs):
    entry = audit.record(
        db,
        action=kwargs.pop("action", AuditAction.UPDATE),
        entity_type=kwargs.pop("entity_type", "user"),
        summary=summary,
        tenant_id=TENANT,
        actor=actor,
        **kwargs,
    )
    db.commit()
    return entry


class TestChaining:
    def test_first_row_starts_from_genesis(self, db, actor):
        entry = write(db, "first thing", actor)
        assert entry.prev_hash == GENESIS_HASH
        assert len(entry.row_hash) == 64

    def test_each_row_links_to_the_one_before(self, db, actor):
        a = write(db, "first", actor)
        b = write(db, "second", actor)
        c = write(db, "third", actor)
        assert b.prev_hash == a.row_hash
        assert c.prev_hash == b.row_hash

    def test_identical_entries_still_hash_differently(self, db, actor):
        """Because each commits to a different predecessor and timestamp."""
        a = write(db, "same text", actor)
        b = write(db, "same text", actor)
        assert a.row_hash != b.row_hash

    def test_empty_ledger_verifies(self, db):
        result = audit.verify_chain(db, TENANT)
        assert result["ok"] is True
        assert result["checked"] == 0

    def test_intact_chain_verifies(self, db, actor):
        for i in range(10):
            write(db, f"event {i}", actor)
        result = audit.verify_chain(db, TENANT)
        assert result["ok"] is True
        assert result["checked"] == 10

    def test_separate_tenants_keep_separate_chains(self, db, actor):
        write(db, "designboxed event", actor)
        other = audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="user",
            summary="other tenant event",
            tenant_id="anomality",
        )
        db.commit()
        assert other.prev_hash == GENESIS_HASH
        assert audit.verify_chain(db, TENANT)["ok"]
        assert audit.verify_chain(db, "anomality")["ok"]


class TestTamperDetection:
    def test_edited_row_is_caught(self, db, actor):
        write(db, "first", actor)
        target = write(db, "admin rejected the request", actor)
        write(db, "third", actor)

        target.summary = "admin approved the request"
        db.commit()

        result = audit.verify_chain(db, TENANT)
        assert result["ok"] is False
        assert result["broken_at_id"] == target.id
        assert "edited" in result["detail"]

    def test_deleted_row_is_caught(self, db, actor):
        write(db, "first", actor)
        middle = write(db, "second", actor)
        write(db, "third", actor)

        db.delete(middle)
        db.commit()

        result = audit.verify_chain(db, TENANT)
        assert result["ok"] is False
        assert "removed or reordered" in result["detail"]

    def test_reassigned_actor_is_caught(self, db, actor):
        """Rewriting who did something is exactly what the chain exists to stop."""
        entry = write(db, "someone approved a booking", actor)
        entry.actor_email = "someone.else@designboxed.com"
        db.commit()
        assert audit.verify_chain(db, TENANT)["ok"] is False

    def test_backdated_row_is_caught(self, db, actor):
        from datetime import timedelta

        entry = write(db, "a late-night change", actor)
        entry.created_at = entry.created_at - timedelta(days=30)
        db.commit()
        assert audit.verify_chain(db, TENANT)["ok"] is False


class TestActorSnapshot:
    def test_actor_details_are_copied_onto_the_row(self, db, actor):
        entry = write(db, "did a thing", actor)
        assert entry.actor_email == "admin@designboxed.com"
        assert entry.actor_name == "System Admin"
        assert entry.actor_role == "SYSTEM_ADMIN"

    def test_renaming_the_user_does_not_rewrite_history(self, db, actor):
        entry = write(db, "did a thing", actor)
        actor.full_name = "Someone Entirely Different"
        db.commit()
        db.refresh(entry)
        assert entry.actor_name == "System Admin"
        assert audit.verify_chain(db, TENANT)["ok"] is True

    def test_system_actions_have_no_actor(self, db):
        entry = write(db, "nightly job ran")
        assert entry.actor_user_id is None
        assert entry.actor_email is None


class TestDiff:
    def test_only_changed_fields_appear(self):
        out = audit.diff(
            {"name": "Ravi", "city": "Hyderabad"},
            {"name": "Ravi", "city": "Chennai"},
        )
        assert out == {"city": {"from": "Hyderabad", "to": "Chennai"}}

    def test_no_changes_gives_empty(self):
        assert audit.diff({"a": 1}, {"a": 1}) == {}

    def test_added_and_removed_keys_are_captured(self):
        out = audit.diff({"a": 1}, {"b": 2})
        assert out == {"a": {"from": 1, "to": None}, "b": {"from": None, "to": 2}}

    def test_redacted_fields_record_that_they_changed_not_what_to(self):
        out = audit.diff(
            {"password": "old-secret"},
            {"password": "new-secret"},
            redact={"password"},
        )
        assert out == {"password": {"from": "***", "to": "***"}}
        assert "secret" not in str(out)


class TestJsonSafety:
    """Regression cover for a bug that silently rolled back user edits.

    The `changes` column is MySQL JSON, serialised with the stdlib encoder.
    Diffs carry whatever the ORM held - dates, enums, Decimals - and the encoder
    raises on all three. Because the audit row shares a transaction with the
    change it describes, that exception rolled back the edit itself: the user's
    update vanished with a 500 and no record of why.
    """

    def test_dates_survive_a_diff(self):
        from datetime import date

        out = audit.diff({"exited_on": None}, {"exited_on": date(2026, 6, 12)})
        assert out == {"exited_on": {"from": None, "to": "2026-06-12"}}

    def test_datetimes_survive_a_diff(self):
        from datetime import datetime

        out = audit.diff({"at": None}, {"at": datetime(2026, 6, 12, 9, 30)})
        assert out["at"]["to"] == "2026-06-12T09:30:00"

    def test_enums_become_their_value(self):
        out = audit.diff({"role": Role.GROUND_STAFF}, {"role": Role.ADMIN})
        assert out == {"role": {"from": "GROUND_STAFF", "to": "ADMIN"}}

    def test_decimals_become_numbers(self):
        from decimal import Decimal

        out = audit.diff({"cost": Decimal("0")}, {"cost": Decimal("1250.50")})
        assert out["cost"]["to"] == 1250.5

    def test_nested_structures_are_flattened_too(self):
        from datetime import date

        out = audit.diff({"trip": None}, {"trip": {"on": date(2026, 6, 12), "who": ["a", "b"]}})
        assert out["trip"]["to"] == {"on": "2026-06-12", "who": ["a", "b"]}

    def test_a_date_diff_actually_writes(self, db, actor):
        """The failure this guards against was at INSERT time, not in diff()."""
        from datetime import date

        changes = audit.diff({"exited_on": None}, {"exited_on": date(2026, 6, 12)})
        entry = write(db, "set an exit date", actor, changes=changes)
        db.refresh(entry)
        assert entry.changes == {"exited_on": {"from": None, "to": "2026-06-12"}}

    def test_raw_values_passed_straight_to_record_are_coerced(self, db, actor):
        from datetime import date

        entry = write(db, "raw payload", actor, changes={"on": date(2026, 6, 12)})
        db.refresh(entry)
        assert entry.changes == {"on": "2026-06-12"}

    def test_the_chain_still_verifies_afterwards(self, db, actor):
        from datetime import date

        write(db, "set an exit date", actor, changes={"on": date(2026, 6, 12)})
        assert audit.verify_chain(db, TENANT)["ok"] is True
