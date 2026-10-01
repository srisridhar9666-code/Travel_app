"""Request and response bodies for projects and campaigns."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import ProjectStatus


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    code: str = Field(min_length=2, max_length=40)
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("code")
    @classmethod
    def _normalise_code(cls, value: str) -> str:
        # Codes get typed, spoken and pasted, so store one canonical form.
        return "-".join(value.strip().upper().split())

    @model_validator(mode="after")
    def _dates_make_sense(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date cannot be before the start date.")
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    code: str | None = Field(default=None, min_length=2, max_length=40)
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    status: ProjectStatus | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else value

    @field_validator("code")
    @classmethod
    def _normalise_code(cls, value: str | None) -> str | None:
        return "-".join(value.strip().upper().split()) if value else value


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: str | None = None
    client_name: str | None = None
    location: str | None = None
    status: ProjectStatus
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime

    #: Whether this campaign still appears in the request dropdowns.
    accepts_requests: bool


class ProjectListResponse(BaseModel):
    items: list[ProjectRead]
    total: int
    page: int
    page_size: int
