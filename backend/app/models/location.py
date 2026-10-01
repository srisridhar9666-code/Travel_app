"""
States and cities people can travel between (SOW section 3).

Origin, destination and hotel city used to be free text, which meant `HYD`,
`hyd`, `Hyd` and `Hyderabad` were four different places. Nothing downstream
survives that: conflict detection compares cities as strings, co-stay matching
looks for colleagues in the same city, and section 6 wants deployment grouped by
state. One careless abbreviation and a colleague already in that hotel becomes
invisible.

So cities are picked from a list rather than typed. The list is data rather than
a constant, because field work reaches towns no hardcoded list would have, and
an admin needs to add one without waiting for a release.
"""
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Location(Base, TenantMixin, TimestampMixin):
    __tablename__ = "locations"
    __table_args__ = (
        # The constraint that actually prevents the duplicates. Stored in one
        # canonical casing, so "Hyderabad" can only exist once per state.
        Index("uq_locations_tenant_state_city", "tenant_id", "state", "city", unique=True),
        Index("ix_locations_tenant_state", "tenant_id", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    state: Mapped[str] = mapped_column(String(80), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Deactivated rather than deleted: a city that is no longer worked in still
    #: has to resolve on every past request that went there.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Location {self.city}, {self.state}>"
