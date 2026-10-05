"""
Vendors: the travel agents, cab operators and hotels an organisation pays.

A vendor is recorded against each traveller's cost (`RequestTraveller.vendor_id`)
when an admin enters what the trip cost, and is what an invoice is raised
against. A vendor is switched off rather than deleted: past costs and invoices
point at it, and the people who settle accounts need to see who was paid even
after the organisation stops using them.
"""
from sqlalchemy import Boolean, Enum as SAEnum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import VendorKind
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Vendor(Base, TenantMixin, TimestampMixin):
    __tablename__ = "vendors"
    __table_args__ = (
        # The collation ignores case, so "Sai Travels" and "sai travels" are one
        # vendor - an invoice raised against the wrong twin is money paid twice.
        Index("uq_vendors_tenant_name", "tenant_id", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[VendorKind] = mapped_column(
        SAEnum(VendorKind, native_enum=False, length=20, validate_strings=True),
        default=VendorKind.OTHER,
        nullable=False,
    )
    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: India's GST number, printed on the invoice. Optional: a local cab
    #: operator often has none.
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Only active vendors can be picked for a new cost. Invoices for a vendor
    #: who has been switched off still work, so their last bills can be settled.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # No created_by column, as with departments: the audit log says who added it.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Vendor {self.id} {self.name}>"
