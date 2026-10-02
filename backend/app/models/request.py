"""
Requests, the people on them, and the record of every edit (SOW section 3).

A request is one *trip*: one route, one stay or one cab. The people it covers are
`RequestTraveller` rows, and each of those carries its own status - that row, not
the request, is the unit an admin approves or rejects. A request's own status is
derived (`derive_request_status`) and never stored.

Times are wall-clock local. Staff type "09:30" meaning 09:30 where they are,
every traveller is in one country, and comparing two wall-clock values is exactly
what the conflict rules need - so they are stored as entered rather than
converted. `UTCDateTime` is used here for its microsecond precision, not to
assert a timezone.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    RequestPriority,
    RequestType,
    RoomSharingChoice,
    TravelMode,
    TravellerStatus,
)
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime, naive_utcnow


def _enum(cls, length: int = 20):
    return SAEnum(cls, native_enum=False, length=length, validate_strings=True)


def _with_city(place: str | None, city: str | None) -> str:
    """"Banjara Hills, Hyderabad" - unless the address already names the city."""
    place = (place or "").strip()
    city = (city or "").strip()
    if not city or city.lower() in place.lower():
        return place or city
    return f"{place}, {city}" if place else city


class TravelRequest(Base, TenantMixin, TimestampMixin):
    __tablename__ = "travel_requests"
    __table_args__ = (
        Index("ix_travel_requests_tenant_requester", "tenant_id", "requester_id"),
        Index("ix_travel_requests_tenant_project", "tenant_id", "project_id"),
        Index("ix_travel_requests_hotel_city", "tenant_id", "hotel_city"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_type: Mapped[RequestType] = mapped_column(_enum(RequestType), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # A draft is visible only to its owner and never occupies anyone's calendar.
    # Submitting clears it.
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancelled_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- long distance and cab ---------------------------------------------
    mode: Mapped[TravelMode | None] = mapped_column(_enum(TravelMode), nullable=True)
    #: The state each place sits in, captured at pick time. Derivable from the
    #: city, but stored because section 6 groups deployment by state and a join
    #: per row to answer "where is everyone" is the wrong shape.
    origin_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    destination_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hotel_state: Mapped[str | None] = mapped_column(String(80), nullable=True)

    origin: Mapped[str | None] = mapped_column(String(160), nullable=True)       # cab: pickup address
    destination: Mapped[str | None] = mapped_column(String(160), nullable=True)  # cab: drop address
    #: A cab's pickup and drop are street addresses ("Banjara Hills"), which no
    #: report can group. These hold the city or constituency each is in, from
    #: the same list a flight's cities come from; the state is in origin_state
    #: and destination_state. Null on flights and hotels, and on cabs raised
    #: before the fields existed.
    pickup_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    drop_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- hotel --------------------------------------------------------------
    hotel_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    check_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_out: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Why this trip is happening. Mandatory on new requests (enforced in the
    #: schema, not the column, so the rows that predate it stay readable). An
    #: admin approving a trip needs to know what it is for.
    travel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: How soon the requester needs a decision. They set it, and can change it
    #: while the request is still editable; admins see high-priority work first.
    priority: Mapped[RequestPriority] = mapped_column(
        _enum(RequestPriority),
        default=RequestPriority.MEDIUM,
        server_default=RequestPriority.MEDIUM.value,
        nullable=False,
    )

    #: Set when the requester picked "Other" instead of a listed campaign and
    #: typed a name. The request still points at the seeded "Other" project so
    #: every join and every report keeps working; this carries what they meant,
    #: and an admin can create the real campaign and reassign later.
    other_project_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project = relationship("Project", lazy="joined")
    requester = relationship("User", foreign_keys=[requester_id], lazy="joined")
    travellers: Mapped[list["RequestTraveller"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestTraveller.id",
        lazy="selectin",
    )
    revisions: Mapped[list["RequestRevision"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestRevision.revision_number",
        lazy="noload",
    )

    @property
    def origin_label(self) -> str:
        """Where it starts, as a person would say it: a cab's address with its
        city ("Banjara Hills, Hyderabad"), otherwise the origin city."""
        return _with_city(self.origin, self.pickup_city)

    @property
    def destination_label(self) -> str:
        return _with_city(self.destination, self.drop_city)

    def route_label(self, sep: str = " → ") -> str:
        return f"{self.origin_label}{sep}{self.destination_label}"

    @property
    def traveller_statuses(self) -> list[TravellerStatus]:
        return [t.status for t in self.travellers]

    @property
    def travel_ends_on(self) -> date | None:
        """The last day this trip touches, or None if it carries no date.

        Used only to decide whether a still-undecided request has gone stale -
        see `EXPIRED` in `derive_request_status`.
        """
        if self.request_type is RequestType.HOTEL:
            return self.check_out or self.check_in
        latest = self.end_at or self.start_at
        return latest.date() if latest else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TravelRequest {self.id} {self.request_type}>"


class RequestTraveller(Base, TimestampMixin):
    """One person on one request. The decision unit for selective approval."""

    __tablename__ = "request_travellers"
    __table_args__ = (
        UniqueConstraint("request_id", "user_id", name="uq_request_traveller"),
        Index("ix_request_travellers_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    status: Mapped[TravellerStatus] = mapped_column(
        _enum(TravellerStatus), default=TravellerStatus.PENDING, nullable=False
    )

    # --- the admin decision (SOW section 4) ---------------------------------
    #: Who decided, when, and why. Held on the traveller rather than the request
    #: because on a group booking these four people may have had four different
    #: answers from two different admins.
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: Mandatory on a rejection - the traveller is shown it.
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: PNR, ticket number or hotel confirmation. Typed by an admin in V1; Phase 5
    #: fills it from the uploaded ticket, still behind a human confirmation
    #: (addendum B3).
    booking_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- what it cost (SOW sections 2 and 6, addendum C1) -------------------
    #: This person's share, not the whole request. Cost lives on the traveller
    #: for the same reason status does: a shared cab is one payment and several
    #: people, and every report in section 6 is per person or per campaign.
    #: NUMERIC, never float - see `services/costs.py`.
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    cost_currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    #: How the number was arrived at, e.g. "shared cab, split 3 ways". Free text
    #: because the interesting cases are the ones a dropdown would not have.
    cost_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cost_entered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cost_entered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- co-stay (addendum B7, open question C2) ----------------------------
    room_sharing: Mapped[RoomSharingChoice] = mapped_column(
        _enum(RoomSharingChoice), default=RoomSharingChoice.NOT_OFFERED, nullable=False
    )
    share_with_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: A share is only *requested* until an admin confirms it. Held as two
    #: columns on this row so that changing C2 - say, to require the colleague's
    #: own consent - is one more column and one more check, not a redesign.
    share_confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    share_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    request: Mapped[TravelRequest] = relationship(back_populates="travellers")
    user = relationship("User", foreign_keys=[user_id], lazy="joined")
    share_with = relationship("User", foreign_keys=[share_with_user_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")
    cost_entered_by = relationship("User", foreign_keys=[cost_entered_by_id], lazy="joined")


class RequestRevision(Base):
    """One saved edit, with a field-level before/after diff (addendum A1).

    Revision 1 is the request as first submitted and carries no diff; every later
    number is an amendment. Append-only, like the ledger - nothing updates one.
    """

    __tablename__ = "request_revisions"
    __table_args__ = (
        UniqueConstraint("request_id", "revision_number", name="uq_request_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    editor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=naive_utcnow, nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    request: Mapped[TravelRequest] = relationship(back_populates="revisions")
    editor = relationship("User", foreign_keys=[editor_id], lazy="joined")


class Notification(Base):
    """One notice to one person, and the record of trying to deliver it.

    This is the notification ledger addendum B11 asks for. A row is written when
    something happens to someone, and delivery is a separate, recorded act: the
    row carries the channel, the address it went to, how many attempts were made
    and what the server said if it refused. Nothing is ever silently dropped -
    mail that is deliberately not sent is SUPPRESSED, mail that was refused is
    FAILED, and the two are different facts.

    Channel-agnostic on purpose (C3): adding SMS is a new channel value and a new
    sender, not a migration.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_status", "user_id", "status"),
        # Unique rather than merely indexed: the guarantee wanted is "this event
        # reaches this person once", and only the database can hold that against
        # two workers running at the same time.
        Index(
            "uq_notifications_dedupe",
            "user_id", "channel", "dedupe_key",
            unique=True,
            mysql_length={"dedupe_key": 120},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What this notice is about, so a person can switch off a whole group.
    #: Stored rather than derived from `kind` so the ledger stays readable after
    #: a kind is renamed or retired.
    category: Mapped[NotificationCategory] = mapped_column(
        _enum(NotificationCategory, 20), default=NotificationCategory.DECISIONS, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        _enum(NotificationChannel), default=NotificationChannel.IN_APP, nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        _enum(NotificationStatus), default=NotificationStatus.SENT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=naive_utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- delivery ------------------------------------------------------------
    #: Resolved when the row is written, not when it is sent, so the ledger
    #: records where it was meant to go even if the account changes later.
    to_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: Why it did not go. Truncated: an SMTP refusal can be paragraphs long.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Identifies the *event* rather than the row, so a job that runs every ten
    #: minutes cannot remind the same person of the same trip twice. Null for
    #: notices that are inherently one-off, like a decision.
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
