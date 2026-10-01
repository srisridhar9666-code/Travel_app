"""
The person and their credentials.

Users and employees are one table rather than two. Every one of the ~110 people
in this system needs both a login and a travel profile, so splitting them would
buy a mandatory 1:1 join and nothing else. ID proofs live in their own table
(Phase 2) because they are multi-valued and carry their own access rules.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Designation, Gender, Role
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # Email is unique per tenant, not globally, so a second company can
        # onboard someone who already exists under the first.
        Index("uq_users_tenant_email", "tenant_id", "email", unique=True),
        Index("ix_users_tenant_role", "tenant_id", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- identity -----------------------------------------------------------
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    employee_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Null until the invite is accepted - an invited user exists but cannot
    # authenticate yet.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- authorisation ------------------------------------------------------
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=20, validate_strings=True),
        default=Role.GROUND_STAFF,
        nullable=False,
    )

    # --- travel profile (SOW section 5) -------------------------------------
    # Designation is hierarchy for reporting only. Approval routing on it is
    # explicitly out of scope for V1 - see addendum B11.
    designation: Mapped[Designation | None] = mapped_column(
        SAEnum(Designation, native_enum=False, length=20, validate_strings=True),
        nullable=True,
    )
    # Drives the room-sharing policy. Anything other than an exact match between
    # two people falls back to separate rooms - see addendum B7.
    gender: Mapped[Gender] = mapped_column(
        SAEnum(Gender, native_enum=False, length=20, validate_strings=True),
        default=Gender.UNDISCLOSED,
        nullable=False,
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    base_location: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- preferences --------------------------------------------------------
    # Persisted server-side so the theme follows the user across devices,
    # rather than living only in one browser's localStorage.
    theme_preference: Mapped[str] = mapped_column(String(10), default="system", nullable=False)

    # --- account state ------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: The day they left the organisation. Distinct from `is_active`, which only
    #: says whether they can sign in - a contractor can be suspended for a week
    #: without having left. Retention counts from this date, not from
    #: deactivation, so a temporary suspension never triggers a purge.
    exited_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by = relationship("User", remote_side=[id], lazy="noload")

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.ADMIN, Role.SYSTEM_ADMIN)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.email} {self.role}>"
