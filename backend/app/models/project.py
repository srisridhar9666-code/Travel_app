"""
Projects and campaigns (SOW section 2).

Every travel, cab and hotel request is tagged against one of these, which is what
makes the campaign-level reporting in section 6 possible at all.

Projects are archived, never deleted. A deleted project would orphan the requests
that reference it and silently erase the campaign's history - so `ARCHIVED` drops
it out of the request dropdowns while leaving every past request intact.
"""
from datetime import date

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ProjectStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Project(Base, TenantMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        # Codes are how admins refer to campaigns in conversation, so they have
        # to be unambiguous within a tenant.
        Index("uq_projects_tenant_code", "tenant_id", "code", unique=True),
        Index("ix_projects_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, native_enum=False, length=20, validate_strings=True),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Where the campaign is running. Feeds the "deployed staff by state" view
    #: in section 6.
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="noload")

    @property
    def accepts_requests(self) -> bool:
        """Only live campaigns appear in the request dropdowns."""
        return self.status is ProjectStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Project {self.id} {self.code} {self.status}>"
