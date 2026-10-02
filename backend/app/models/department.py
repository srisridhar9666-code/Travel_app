"""
Departments: which part of the organisation someone works in.

Separate from `Role`, which decides what a person may do in this app and so has
to stay a fixed list. Departments are the open-ended grouping admins asked for
("create more roles"): they add one while adding a person, and it is reporting
only.
"""
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Department(Base, TenantMixin, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        # The collation ignores case, so "Field Ops" and "field ops" are one.
        Index("uq_departments_tenant_name", "tenant_id", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # No created_by column: users point at departments, so a pointer back would
    # make the two tables depend on each other. The audit log says who added it.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Department {self.id} {self.name}>"
