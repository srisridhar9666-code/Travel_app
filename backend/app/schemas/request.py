"""Request and response bodies for travel, cab and hotel requests."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import (
    ConflictKind,
    ConflictSeverity,
    Designation,
    RequestPriority,
    RequestStatus,
    RequestType,
    RoomSharingChoice,
    TravelMode,
    TravellerStatus,
)
from app.schemas.common import UTCInstant

#: Which fields each request type actually uses. Also the list the revision diff
#: is taken over, so a field absent here is a field no one can amend.
EDITABLE_FIELDS = (
    "project_id",
    "origin_state",
    "destination_state",
    "hotel_state",
    "other_project_name",
    "travel_reason",
    "priority",
    "mode",
    "origin",
    "destination",
    "pickup_city",
    "drop_city",
    "start_at",
    "end_at",
    "hotel_city",
    "check_in",
    "check_out",
    "notes",
)


class RequestBody(BaseModel):
    """Shared shape of a create or a full edit.

    Type-specific validation lives here rather than in the router so a draft, a
    submission and an amendment are all held to the same rules.
    """

    request_type: RequestType
    project_id: int
    traveller_ids: list[int] = Field(default_factory=list)

    mode: TravelMode | None = None
    #: Captured alongside the city so deployment can be grouped by state
    #: without a join per row.
    origin_state: str | None = Field(default=None, max_length=80)
    destination_state: str | None = Field(default=None, max_length=80)
    hotel_state: str | None = Field(default=None, max_length=80)

    origin: str | None = Field(default=None, max_length=160)
    destination: str | None = Field(default=None, max_length=160)
    #: A cab's city or constituency, beside its street address. The state is
    #: origin_state / destination_state.
    pickup_city: str | None = Field(default=None, max_length=120)
    drop_city: str | None = Field(default=None, max_length=120)
    start_at: datetime | None = None
    end_at: datetime | None = None

    hotel_city: str | None = Field(default=None, max_length=120)
    check_in: date | None = None
    check_out: date | None = None

    #: Mandatory. An admin deciding on a trip needs to know what it is for,
    #: and "because I was asked to" in a free-text note was not reliably there.
    travel_reason: str = Field(min_length=5, max_length=500)

    #: How soon the requester needs a decision. Defaults to MEDIUM so an older
    #: client that never sends it still gets the honest middle answer.
    priority: RequestPriority = RequestPriority.MEDIUM

    #: Only when the chosen campaign is the fallback "Other" one. Validated in
    #: the service, which is where the project is actually looked up.
    other_project_name: str | None = Field(default=None, max_length=160)

    notes: str | None = Field(default=None, max_length=2000)
    is_draft: bool = False

    @model_validator(mode="after")
    def _shape_matches_type(self):
        if self.request_type is RequestType.HOTEL:
            if not self.hotel_city:
                raise ValueError("A hotel request needs a city.")
            if self.check_in is None:
                raise ValueError("A hotel request needs a check-in date.")
            if self.check_out is not None and self.check_out <= self.check_in:
                raise ValueError("Check-out must be after check-in.")
            # A stay has no route; carrying one would confuse the conflict rules,
            # which decide by shape rather than by request type alone.
            self.mode = None
            self.origin = self.destination = None
            self.origin_state = self.destination_state = None
            self.pickup_city = self.drop_city = None
            self.start_at = self.end_at = None
        else:
            if not self.origin:
                raise ValueError("A travel request needs a pickup or origin.")
            if not self.destination:
                raise ValueError("A travel request needs a drop or destination.")
            if self.start_at is None:
                raise ValueError("A travel request needs a departure date and time.")
            if self.end_at is not None and self.end_at < self.start_at:
                raise ValueError("Arrival cannot be before departure.")
            if self.request_type is RequestType.LOCAL_CAB:
                self.mode = TravelMode.CAB
                if not (self.origin_state and self.pickup_city):
                    raise ValueError("Pick the state and city the cab picks up in.")
                if not (self.destination_state and self.drop_city):
                    raise ValueError("Pick the state and city the cab drops in.")
            else:
                if self.mode is None or self.mode is TravelMode.CAB:
                    raise ValueError("Choose flight, train or bus for a long-distance request.")
                self.pickup_city = self.drop_city = None
            self.hotel_city = self.hotel_state = None
            self.check_in = self.check_out = None

        # Naive datetimes throughout: these are wall-clock values, and a browser
        # that helpfully appends a zone must not shift someone's 06:00 flight.
        for field in ("start_at", "end_at"):
            value = getattr(self, field)
            if value is not None and value.tzinfo is not None:
                object.__setattr__(self, field, value.replace(tzinfo=None))
        return self


class RequestCreate(RequestBody):
    pass


class RequestEdit(RequestBody):
    """A full replacement. The diff is computed server-side against what is
    stored, so a partial PATCH would make "what changed" ambiguous."""


class RoomSharingChoicePayload(BaseModel):
    traveller_id: int
    choice: RoomSharingChoice
    share_with_user_id: int | None = None

    @model_validator(mode="after")
    def _share_needs_a_person(self):
        if self.choice is RoomSharingChoice.SHARE_EXISTING and self.share_with_user_id is None:
            raise ValueError("Choosing to share needs the colleague to share with.")
        if self.choice is not RoomSharingChoice.SHARE_EXISTING:
            self.share_with_user_id = None
        return self


class CancelPayload(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class TravellerRead(BaseModel):
    id: int
    user_id: int
    full_name: str
    email: str
    designation: Designation | None = None
    status: TravellerStatus
    is_requester: bool
    room_sharing: RoomSharingChoice
    share_with_user_id: int | None = None
    share_with_name: str | None = None
    share_confirmed: bool = False

    # --- the decision, once one has been taken (Phase 4) -------------------
    decided_by_name: str | None = None
    decided_at: UTCInstant | None = None
    decision_reason: str | None = None
    booking_reference: str | None = None
    #: The uploaded ticket to open from this row (GET /tickets/{id}/file):
    #: the confirmed one, else the newest under review. On the admin queue list only.
    ticket_id: int | None = None

    # --- cost (SOW 2 and 6, addendum C1). Admin-only; see the router. -------
    cost_amount: Decimal | None = None
    cost_currency: str | None = None
    cost_note: str | None = None
    cost_entered_by_name: str | None = None


class ConflictRead(BaseModel):
    user_id: int
    user_name: str
    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    other_request_id: int | None = None
    other_request_type: RequestType | None = None
    other_summary: str | None = None


class CoStayMatchRead(BaseModel):
    """What the requester is shown about a colleague already in that city.

    Name and designation, per the C2 assumption - see `services/costay.py`.
    """

    user_id: int
    full_name: str
    designation: Designation | None = None
    request_id: int
    hotel_city: str
    check_in: date
    check_out: date | None = None
    overlapping_nights: int
    status: str


class RevisionRead(BaseModel):
    revision_number: int
    editor_name: str | None = None
    created_at: UTCInstant
    summary: str
    changes: dict[str, dict] | None = None


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_type: RequestType
    status: RequestStatus
    is_editable: bool
    is_draft: bool
    is_cancelled: bool
    cancel_reason: str | None = None

    project_id: int
    project_name: str
    project_code: str

    requester_id: int
    requester_name: str

    mode: TravelMode | None = None
    origin: str | None = None
    destination: str | None = None
    pickup_city: str | None = None
    drop_city: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    hotel_city: str | None = None
    check_in: date | None = None
    check_out: date | None = None

    travel_reason: str | None = None
    priority: RequestPriority = RequestPriority.MEDIUM
    other_project_name: str | None = None
    origin_state: str | None = None
    destination_state: str | None = None
    hotel_state: str | None = None
    notes: str | None = None
    submitted_at: UTCInstant | None = None
    created_at: UTCInstant
    updated_at: UTCInstant

    travellers: list[TravellerRead]
    #: How many amendments have been saved since submission. The admin queue
    #: shows this as "edited N times" - addendum A1.
    edit_count: int = 0
    conflicts: list[ConflictRead] = Field(default_factory=list)
    costay_matches: list[CoStayMatchRead] = Field(default_factory=list)

    #: True once every traveller has been decided one way or the other. The
    #: admin queue uses it to drop a request out of the "awaiting" tab.
    is_decided: bool = False


class RequestListResponse(BaseModel):
    items: list[RequestRead]
    total: int
    page: int
    page_size: int


class QueueExport(BaseModel):
    """Every request in one admin queue tab, for the CSV export.

    Not paged: an export that silently stops at page one is worse than none.
    Capped instead, with `truncated` saying so, the same way the travel log is.
    """

    status: RequestStatus
    total: int
    truncated: bool
    items: list[RequestRead]


class ConflictCheckRequest(RequestBody):
    """A dry run of the conflict and co-stay checks, before anything is saved."""

    request_id: int | None = None


class ConflictCheckResponse(BaseModel):
    conflicts: list[ConflictRead]
    costay_matches: list[CoStayMatchRead]


class ColleagueRead(BaseModel):
    """A person as the co-traveller picker sees them.

    Deliberately thin. Ground staff cannot read the employee directory, and a
    picker needs a name to choose from - not an email, a phone number or an
    account state. Gender is absent too: the co-stay matcher needs it, the person
    choosing a colleague does not.
    """

    id: int
    full_name: str
    designation: Designation | None = None


# --- Phase 4: admin decisions ---------------------------------------------


class DecisionPayload(BaseModel):
    """One admin decision on one traveller.

    `reason` is mandatory on every decision - an approval as much as a
    rejection. An approval with no stated reason is the one most likely to be
    questioned months later, and "it was approved" is not an answer.

    `conflict_override_reason` is mandatory when approving someone who has a live
    clash, and is recorded separately as OVERRIDE_CONFLICT (addendum B6).

    `notify_employee` lets the admin suppress the email - a decision taken while
    the traveller is standing next to them does not need one, and a batch being
    tidied up retrospectively should not fill inboxes. The in-app notice and the
    ledger entry are written regardless: the record is not optional, only the
    email is.
    """

    to_status: TravellerStatus
    reason: str | None = Field(default=None, max_length=500)
    booking_reference: str | None = Field(default=None, max_length=120)
    conflict_override_reason: str | None = Field(default=None, max_length=500)
    notify_employee: bool = True

    @model_validator(mode="after")
    def _target_is_a_decision(self):
        if self.to_status is TravellerStatus.PENDING:
            raise ValueError("A decision cannot put a traveller back to pending.")
        return self


class BatchDecisionItem(DecisionPayload):
    traveller_id: int


class BatchDecisionPayload(BaseModel):
    """Several travellers on one request, decided in one transaction.

    This is what partial approval looks like from the admin queue: tick three
    people, reject the fourth, press once. Either the whole set lands or none of
    it does, so the queue can never show a half-applied decision.
    """

    decisions: list[BatchDecisionItem] = Field(min_length=1)


class QueueCounts(BaseModel):
    """Headline counts for the admin queue tabs."""

    awaiting: int
    partially_approved: int
    approved: int
    booked: int
    rejected: int
    cancelled: int
    expired: int
    with_conflicts: int
    edited: int
    #: HIGH-priority requests still waiting on someone (awaiting or partly
    #: approved). The queue's banner, so urgent work is not lost in a long tab.
    high_priority: int = 0
    #: The same, split by tab, so the banner opens the one the work is on.
    high_priority_awaiting: int = 0
    high_priority_partial: int = 0
