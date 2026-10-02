"""
Uploaded tickets and what the model made of them (SOW section 4, addendum B3).

The SOW has an upload populate the fields "instantly" and set the request to
Booked, then email the traveller. A single misparse would silently book a wrong
PNR and mail it out, so this table exists to hold the model's *proposal*
separately from the booking it might become:

    upload -> extract -> admin review & confirm -> Booked -> notify

Everything the model said is kept - the raw response, the model id and a
per-field confidence - so a booking that turns out wrong can be traced back to
what was actually proposed rather than to what someone remembers approving.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import TicketStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class TicketDocument(Base, TenantMixin, TimestampMixin):
    __tablename__ = "ticket_documents"
    __table_args__ = (
        Index("ix_ticket_documents_request", "request_id"),
        Index("ix_ticket_documents_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False
    )
    #: A ticket is one person's. Booking is per traveller, so the document that
    #: books someone is attached to their row rather than to the whole group.
    traveller_id: Mapped[int] = mapped_column(
        ForeignKey("request_travellers.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, native_enum=False, length=20, validate_strings=True),
        default=TicketStatus.UPLOADED,
        nullable=False,
    )

    # --- the file ------------------------------------------------------------
    # Stored outside any static mount and never web-served, exactly like an ID
    # proof scan. It carries a PNR and a full name, so it is not public.
    file_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)

    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # --- what the model proposed --------------------------------------------
    booking_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    service_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    passenger_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(160), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(160), nullable=True)
    depart_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    arrive_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    hotel_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    check_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_out: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: The fare printed on the document. Pre-fills the cost field on the booking
    #: screen; like every other extracted value it is a proposal, and an admin
    #: confirms it before it becomes a number in a financial report (C1, B3).
    fare_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fare_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    #: Per-field confidence, 0-1, as the model reported it. Drives the review
    #: screen: anything it was unsure of is highlighted for a human to read.
    confidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: The response verbatim, so a wrong booking can be traced to what was
    #: actually proposed rather than to what someone remembers approving.
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- the human step ------------------------------------------------------
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: What the admin actually saved, which may differ from what the model said.
    #: Kept beside the proposal rather than overwriting it.
    confirmed_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    request = relationship("TravelRequest", lazy="joined")
    traveller = relationship("RequestTraveller", lazy="joined")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id], lazy="joined")
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_id], lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TicketDocument {self.id} {self.status}>"
