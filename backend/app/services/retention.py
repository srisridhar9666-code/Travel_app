"""
ID proof retention (addendum C4).

Identity documents are purged **90 days after the employee's exit date**. The
window is measured from `User.exited_on`, not from deactivation, so suspending
someone for a fortnight never starts the clock.

A purge empties the record rather than deleting the row. The tombstone keeps the
audit trail honest - the ledger can still say "a passport was held for this
person and was purged on this date" - while the number, the fingerprint and the
scan are gone.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.enums import AuditAction
from app.models.base import naive_utcnow
from app.models.id_proof import IdProof
from app.models.user import User
from app.services import audit, storage

logger = logging.getLogger(__name__)
settings = get_settings()


def cutoff_date(on: date | None = None) -> date:
    """Exit dates on or before this are past their retention window."""
    return (on or clock.local_today()) - timedelta(days=settings.id_proof_retention_days)


def _due_query(tenant_id: str, cutoff: date):
    return (
        select(IdProof)
        .join(User, User.id == IdProof.user_id)
        .where(
            IdProof.tenant_id == tenant_id,
            IdProof.purged_at.is_(None),
            User.exited_on.is_not(None),
            User.exited_on <= cutoff,
        )
    )


def count_due(db: Session, tenant_id: str, *, on: date | None = None) -> int:
    """How many records the next run would purge. Surfaced in the admin UI so
    the deletion is visible before it happens, not only afterwards."""
    return db.execute(
        select(func.count()).select_from(_due_query(tenant_id, cutoff_date(on)).subquery())
    ).scalar_one()


def purge_expired(
    db: Session, tenant_id: str, *, on: date | None = None, actor: User | None = None
) -> dict:
    """Purge every ID proof past its retention window.

    Idempotent: already-purged records are skipped, so running it twice in a day
    is harmless. The caller owns the commit.
    """
    cutoff = cutoff_date(on)
    due = db.execute(_due_query(tenant_id, cutoff)).scalars().all()

    purged = 0
    files_removed = 0

    for proof in due:
        if proof.file_path and storage.delete(proof.file_path):
            files_removed += 1

        owner_id = proof.user_id
        proof_type = str(proof.proof_type)

        proof.number_encrypted = None
        proof.number_last4 = None
        proof.number_masked = None
        proof.fingerprint = None
        proof.file_path = None
        proof.file_size = None
        proof.content_type = None
        proof.purged_at = naive_utcnow()
        purged += 1

        audit.record(
            db,
            action=AuditAction.DELETE,
            entity_type="id_proof",
            entity_id=proof.id,
            summary=(
                f"Purged {proof_type} for user {owner_id} - "
                f"{settings.id_proof_retention_days} days past exit"
            ),
            reason="Scheduled retention policy",
            tenant_id=tenant_id,
            actor=actor,
        )

    if purged:
        logger.info(
            "Retention purge: %d record(s), %d file(s) removed, cutoff %s",
            purged, files_removed, cutoff,
        )

    return {
        "purged": purged,
        "files_removed": files_removed,
        "cutoff": cutoff.isoformat(),
        "retention_days": settings.id_proof_retention_days,
    }
