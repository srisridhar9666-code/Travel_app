"""Request and response bodies for ticket upload, review and confirmation."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    TicketStatus,
)


class ExtractedField(BaseModel):
    """One proposed value and how sure the model was.

    Confidence travels with the value rather than in a separate block so the
    review screen cannot accidentally show one without the other.
    """

    value: str | None = None
    confidence: float | None = None
    #: True when the value needs a human read before it is trusted.
    needs_review: bool = False


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    traveller_id: int
    traveller_name: str
    status: TicketStatus

    file_name: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    uploaded_by_name: str | None = None
    created_at: datetime

    # --- what the model proposed --------------------------------------------
    booking_reference: str | None = None
    carrier: str | None = None
    service_number: str | None = None
    passenger_name: str | None = None
    origin: str | None = None
    destination: str | None = None
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    hotel_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    #: The fare the model read. Pre-fills the cost field; never saved as a cost
    #: without a human confirming it.
    fare_amount: Decimal | None = None
    fare_currency: str | None = None

    confidence: dict[str, float] | None = None
    #: Fields the model was unsure of, so the reviewer knows where to look first.
    needs_review: list[str] = Field(default_factory=list)
    model_id: str | None = None
    extraction_error: str | None = None
    extracted_at: datetime | None = None

    # --- the human step ------------------------------------------------------
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None
    confirmed_reference: str | None = None

    #: Differences between the ticket and what was asked for. Advisory: a ticket
    #: that does not match the request is usually a real problem, occasionally a
    #: deliberate change, and never something to reject automatically.
    mismatches: list[str] = Field(default_factory=list)


class ConfirmPayload(BaseModel):
    """The admin accepting a ticket, with whatever they corrected.

    The reference is required and pre-filled from the extraction rather than
    taken from it silently - addendum B3 exists because a model misparse must not
    be able to book a wrong PNR on its own. The same applies to the fare: it is
    offered, and it only becomes a number in a financial report once someone has
    looked at it (C1).
    """

    booking_reference: str = Field(min_length=2, max_length=120)
    carrier: str | None = Field(default=None, max_length=120)
    service_number: str | None = Field(default=None, max_length=60)
    #: What this traveller's trip cost. Optional here because a fare is not
    #: always known at booking; the costs endpoints fill it in later.
    cost_amount: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    #: Skip the traveller email, for a correction that should not re-notify.
    notify: bool = True


class NotificationRow(BaseModel):
    """One line of the delivery ledger, as an admin sees it."""

    id: int
    user_id: int
    user_name: str | None = None
    kind: str
    category: NotificationCategory
    title: str
    body: str
    channel: NotificationChannel
    status: NotificationStatus
    to_address: str | None = None
    subject: str | None = None
    attempts: int
    sent_at: datetime | None = None
    last_error: str | None = None
    request_id: int | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationLedger(BaseModel):
    items: list[NotificationRow]
    total: int
    page: int
    page_size: int
    summary: dict


class PreferenceUpdate(BaseModel):
    """One category switched on or off for the signed-in person."""

    category: NotificationCategory
    enabled: bool


class PreferencesRead(BaseModel):
    """Email preferences, one entry per switchable category.

    `DECISIONS` is absent on purpose - see `OPTIONAL_CATEGORIES`. The screen says
    so rather than showing a toggle that does nothing.
    """

    email: dict[str, bool]
    unread: int


class JobResult(BaseModel):
    job: str
    notified: int | None = None
    considered: int | None = None
    stale: int | None = None
    attempted: int | None = None
    sent: int | None = None
    still_failing: int | None = None
    error: str | None = None


class SchedulerStatus(BaseModel):
    enabled: bool
    running: bool
    interval_minutes: int
    travel_reminder_days: int
    stale_after_days: int
    failed_email: int
