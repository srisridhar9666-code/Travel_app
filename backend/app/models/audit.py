"""
The universal activity ledger (SOW section 7).

Section 7 asserts an "immutable" trail without saying how, so the mechanism is
defined here (addendum B9):

  * The table is append-only. Nothing in the application updates or deletes a
    row, and in production the application's DB user is granted INSERT and
    SELECT only - the grant is the real enforcement, this module is the
    discipline.
  * Each row stores the hash of the row before it, so the log forms a chain.
    Editing or removing any row breaks every hash after it, which makes
    tampering detectable rather than merely discouraged.
  * Actor email and name are snapshotted onto the row. A deleted or renamed user
    must not be able to rewrite who did what two years ago.
"""
from datetime import datetime

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AuditAction
from app.database import Base
from app.models.base import TenantMixin, UTCDateTime, naive_utcnow

#: Opening hash of a tenant's chain. Any chain that does not walk back to this
#: value has had rows removed from the front.
GENESIS_HASH = "0" * 64


class AuditLog(Base, TenantMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_audit_actor", "tenant_id", "actor_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- who ----------------------------------------------------------------
    # Nullable: scheduled jobs and the seed routine act with no human actor.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- what ---------------------------------------------------------------
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, native_enum=False, length=24, validate_strings=True),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: One human-readable line, written for the person who will read this log in
    #: a dispute six months from now.
    summary: Mapped[str] = mapped_column(String(500), nullable=False)

    #: Field-level diff: {"field": {"from": ..., "to": ...}}. This is what makes
    #: the request edit history (addendum A1) reconstructable.
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: Free-text justification, e.g. the mandatory reason on a conflict override.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- where --------------------------------------------------------------
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # fits IPv6
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- when, and the tamper-evidence chain --------------------------------
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=naive_utcnow, nullable=False, index=True
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog {self.id} {self.action} {self.entity_type}:{self.entity_id}>"
