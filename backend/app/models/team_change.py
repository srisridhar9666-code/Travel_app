"""
A change a manager asked for on their own team, held until an admin decides.

Managers add, edit and remove the people who report to them, but nothing
happens to an account until an admin approves it: the request is stored here,
with exactly what was asked, and applied only then. Kept after the decision as
the record of who asked, who decided and why.
"""
from datetime import datetime

from sqlalchemy import JSON, Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import TeamChangeKind, TeamChangeStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class TeamChange(Base, TenantMixin, TimestampMixin):
    __tablename__ = "team_changes"
    __table_args__ = (
        Index("ix_team_changes_tenant_status", "tenant_id", "status"),
        Index("ix_team_changes_requested_by", "requested_by_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[TeamChangeKind] = mapped_column(
        SAEnum(TeamChangeKind, native_enum=False, length=10, validate_strings=True), nullable=False
    )
    status: Mapped[TeamChangeStatus] = mapped_column(
        SAEnum(TeamChangeStatus, native_enum=False, length=12, validate_strings=True),
        default=TeamChangeStatus.PENDING,
        nullable=False,
    )

    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The member it is about. Null for ADD until approval creates them.
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: What was asked: the new person's details for ADD, the fields to change
    #: for EDIT, the reason and exit date for REMOVE.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    #: The manager's own words on why.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    requested_by = relationship("User", foreign_keys=[requested_by_id], lazy="joined")
    target_user = relationship("User", foreign_keys=[target_user_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")
