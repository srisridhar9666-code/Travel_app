"""Request and response bodies for projects and campaigns."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import ProjectStatus
from app.schemas.common import UTCInstant


def _dates_in_order(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValueError("End date cannot be before the start date.")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=160)
    #: Both optional; a campaign can cover a whole state. `location` is no
    #: longer accepted - an old client that sends it is ignored.
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def _dates_make_sense(self):
        _dates_in_order(self.start_date, self.end_date)
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=160)
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    status: ProjectStatus | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        # A campaign cannot lose its name: the column is NOT NULL, so an
        # explicit null would otherwise surface as a 500.
        if value is None:
            raise ValueError("A campaign needs a name.")
        return " ".join(value.split())

    @field_validator("status")
    @classmethod
    def _status_given(cls, value: ProjectStatus | None) -> ProjectStatus:
        if value is None:
            raise ValueError("Pick a status.")
        return value

    @model_validator(mode="after")
    def _dates_make_sense(self):
        # Only when both are in this request; the router checks the merged
        # values against what is already stored.
        _dates_in_order(self.start_date, self.end_date)
        return self


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: str | None = None
    client_name: str | None = None
    state: str | None = None
    #: City or assembly constituency.
    city: str | None = None
    #: Free text from before state/city. Read-only; shown until a state is picked.
    location: str | None = None
    status: ProjectStatus
    start_date: date | None = None
    end_date: date | None = None
    created_at: UTCInstant

    #: Whether this campaign still appears in the request dropdowns.
    accepts_requests: bool
    #: Requests of any status raised against it. Only one with none can be
    #: deleted.
    request_count: int = 0
    #: The built-in "Other / not yet listed" campaign: cannot be archived,
    #: deleted, recoded or paused.
    is_fallback: bool = False


class ProjectListResponse(BaseModel):
    items: list[ProjectRead]
    total: int
    page: int
    page_size: int
