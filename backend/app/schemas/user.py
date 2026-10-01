"""Request and response bodies for user administration."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import Designation, Gender, Role


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: Role = Role.GROUND_STAFF
    designation: Designation | None = None
    gender: Gender = Gender.UNDISCLOSED
    phone: str | None = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_location: str | None = Field(default=None, max_length=120)

    @field_validator("full_name")
    @classmethod
    def _tidy_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.strip().lower()


class UserUpdate(BaseModel):
    """Every field optional - this is a partial update."""

    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    role: Role | None = None
    designation: Designation | None = None
    gender: Gender | None = None
    phone: str | None = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_location: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    #: The day they left. Starts the 90-day ID-proof retention clock (C4).
    exited_on: date | None = None

    @field_validator("full_name")
    @classmethod
    def _tidy_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    employee_code: str | None = None
    role: Role
    designation: Designation | None = None
    gender: Gender
    phone: str | None = None
    base_location: str | None = None
    is_active: bool
    exited_on: date | None = None
    last_login_at: datetime | None = None
    created_at: datetime

    #: False while an invite is outstanding, so the UI can show "Invited"
    #: rather than pretending the account is ready.
    has_password: bool
    is_locked: bool


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_email: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    action: str
    entity_type: str
    entity_id: int | None = None
    summary: str
    changes: dict | None = None
    reason: str | None = None
    ip_address: str | None = None
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditRead]
    total: int
    page: int
    page_size: int
