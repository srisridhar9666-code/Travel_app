"""Request and response bodies for cost entry and reporting (SOW §2 and §6)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class TravellerCost(BaseModel):
    """One person's share, set explicitly.

    Amounts arrive as strings and stay Decimal all the way to the column. A JSON
    number would be a float by the time Pydantic saw it, and a float is how a
    financial report starts disagreeing with an invoice.
    """

    traveller_id: int
    amount: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    note: str | None = Field(default=None, max_length=200)

    @field_validator("amount")
    @classmethod
    def _two_places(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value.as_tuple().exponent < -2:
            raise ValueError("Amounts are in rupees and paise - two decimal places.")
        return value


class CostEntry(BaseModel):
    """Explicit amounts, one per traveller. The manual-override path of C1."""

    amounts: list[TravellerCost] = Field(min_length=1)


class CostSplit(BaseModel):
    """One total shared evenly across several people - the shared cab or room.

    The split is computed server-side so it sums to exactly the total; see
    `services/costs.split_evenly`.
    """

    total_amount: Decimal = Field(gt=0, le=Decimal("10000000"))
    traveller_ids: list[int] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("total_amount")
    @classmethod
    def _two_places(cls, value: Decimal) -> Decimal:
        if value.as_tuple().exponent < -2:
            raise ValueError("Amounts are in rupees and paise - two decimal places.")
        return value


class CostPreviewRow(BaseModel):
    traveller_id: int
    traveller_name: str
    amount: Decimal


class CostPreview(BaseModel):
    """What a split would do, before anything is saved."""

    total_amount: Decimal
    rows: list[CostPreviewRow]
    #: Proof the apportionment is exact. The UI shows it; the tests assert it.
    sums_to_total: bool


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Overview(BaseModel):
    window_days: int
    spent: Decimal
    committed: Decimal
    average_per_traveller: Decimal
    booked_travellers: int
    pending_travellers: int
    #: Booked rows with no cost recorded - the honesty check on every other figure.
    uncosted: int
    people_travelling: int
    trips: int
    currency: str


class CampaignSpend(BaseModel):
    project_id: int
    code: str
    name: str
    status: str
    spent: Decimal
    committed: Decimal
    trips: int
    travellers: int
    uncosted: int


class TypeSpend(BaseModel):
    request_type: str
    spent: Decimal
    travellers: int


class MonthSpend(BaseModel):
    month: str
    spent: Decimal
    travellers: int


class DeploymentRow(BaseModel):
    location: str
    people: int
    trips: int


class UncostedRow(BaseModel):
    traveller_id: int
    request_id: int
    traveller_name: str
    project_code: str
    request_type: str
    booking_reference: str | None = None
    trip_date: str | None = None


class AnalyticsBundle(BaseModel):
    """Everything the dashboard needs, in one round trip.

    One call rather than six: the figures have to agree with each other on
    screen, and six independent requests can land either side of a booking being
    confirmed.
    """

    overview: Overview
    by_campaign: list[CampaignSpend]
    by_type: list[TypeSpend]
    by_month: list[MonthSpend]
    deployment: list[DeploymentRow]
    uncosted: list[UncostedRow]
