"""
ID proof retention: 90 days after exit (addendum C4).

The rule that matters most here is the distinction between deactivation and
exit. Suspending someone for a fortnight must never start a deletion clock.
"""
from datetime import date, timedelta


from app.core import clock
from app.core import pii
from app.core.enums import AuditAction, IdProofType, Role
from app.models.audit import AuditLog
from app.models.id_proof import IdProof
from app.models.user import User
from app.services import audit, retention

TENANT = "designboxed"
NUMBER = "4321 8765 2109"


def make_user(db, *, exited_on=None, is_active=True, email="ravi@designboxed.com"):
    user = User(
        tenant_id=TENANT,
        email=email,
        full_name="Ravi Kumar",
        role=Role.GROUND_STAFF,
        password_hash="x",
        is_active=is_active,
        exited_on=exited_on,
    )
    db.add(user)
    db.commit()
    return user


def make_proof(db, user, *, file_path=None):
    proof = IdProof(
        tenant_id=TENANT,
        user_id=user.id,
        proof_type=IdProofType.AADHAAR,
        number_encrypted=pii.encrypt(NUMBER),
        number_last4=pii.last4(NUMBER),
        number_masked=pii.mask(NUMBER),
        fingerprint=pii.fingerprint(NUMBER),
        file_path=file_path,
    )
    db.add(proof)
    db.commit()
    return proof


class TestWhatIsDue:
    def test_current_employee_is_never_due(self, db):
        make_proof(db, make_user(db))
        assert retention.count_due(db, TENANT) == 0

    def test_deactivation_alone_does_not_start_the_clock(self, db):
        """A suspended contractor has not left. Their documents stay."""
        user = make_user(db, is_active=False, exited_on=None)
        make_proof(db, user)
        assert retention.count_due(db, TENANT) == 0

    def test_recent_leaver_is_not_yet_due(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=30))
        make_proof(db, user)
        assert retention.count_due(db, TENANT) == 0

    def test_day_before_the_window_closes(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=89))
        make_proof(db, user)
        assert retention.count_due(db, TENANT) == 0

    def test_exactly_at_the_window(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=90))
        make_proof(db, user)
        assert retention.count_due(db, TENANT) == 1

    def test_long_past_the_window(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=400))
        make_proof(db, user)
        assert retention.count_due(db, TENANT) == 1

    def test_cutoff_is_ninety_days_back(self):
        assert retention.cutoff_date(date(2026, 9, 20)) == date(2026, 6, 22)


class TestPurge:
    def test_purge_empties_the_record(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        proof = make_proof(db, user)

        result = retention.purge_expired(db, TENANT)
        db.commit()
        db.refresh(proof)

        assert result["purged"] == 1
        assert proof.number_encrypted is None
        assert proof.number_last4 is None
        assert proof.number_masked is None
        assert proof.fingerprint is None
        assert proof.file_path is None

    def test_the_row_survives_as_a_tombstone(self, db):
        """Deleting the row would break every ledger entry that references it."""
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        proof = make_proof(db, user)
        proof_id = proof.id

        retention.purge_expired(db, TENANT)
        db.commit()

        surviving = db.get(IdProof, proof_id)
        assert surviving is not None
        assert surviving.is_purged is True
        assert surviving.purged_at is not None

    def test_purge_is_audited(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        make_proof(db, user)

        retention.purge_expired(db, TENANT)
        db.commit()

        entries = db.query(AuditLog).filter(AuditLog.entity_type == "id_proof").all()
        assert len(entries) == 1
        assert entries[0].action is AuditAction.DELETE
        assert "retention" in (entries[0].reason or "").lower()

    def test_purge_does_not_record_the_number(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        make_proof(db, user)
        retention.purge_expired(db, TENANT)
        db.commit()

        entries = db.query(AuditLog).all()
        blob = " ".join(f"{e.summary} {e.changes} {e.reason}" for e in entries)
        assert "432187652109" not in blob
        assert NUMBER not in blob

    def test_purge_is_idempotent(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        make_proof(db, user)

        first = retention.purge_expired(db, TENANT)
        db.commit()
        second = retention.purge_expired(db, TENANT)
        db.commit()

        assert first["purged"] == 1
        assert second["purged"] == 0

    def test_only_expired_records_are_touched(self, db):
        stale = make_user(db, exited_on=clock.local_today() - timedelta(days=100), email="gone@designboxed.com")
        current = make_user(db, email="here@designboxed.com")
        stale_proof = make_proof(db, stale)
        current_proof = make_proof(db, current)

        retention.purge_expired(db, TENANT)
        db.commit()
        db.refresh(stale_proof)
        db.refresh(current_proof)

        assert stale_proof.is_purged is True
        assert current_proof.is_purged is False
        assert current_proof.number_encrypted is not None

    def test_another_tenant_is_untouched(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        proof = make_proof(db, user)

        retention.purge_expired(db, "anomality")
        db.commit()
        db.refresh(proof)

        assert proof.is_purged is False

    def test_the_ledger_stays_verifiable_after_a_purge(self, db):
        user = make_user(db, exited_on=clock.local_today() - timedelta(days=100))
        make_proof(db, user)
        retention.purge_expired(db, TENANT)
        db.commit()

        assert audit.verify_chain(db, TENANT)["ok"] is True
