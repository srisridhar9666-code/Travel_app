"""Request and response bodies for departments."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def _tidy(cls, value: object) -> object:
        # Inner spacing collapsed, so "Field  Operations" cannot sit beside
        # "Field Operations". Case is left as typed: the unique index already
        # ignores it, and "IT" or "HR" should stay as the admin wrote them.
        return " ".join(value.split()) if isinstance(value, str) else value


class DepartmentRead(BaseModel):
    id: int
    name: str
    #: People in it, not counting deleted accounts.
    member_count: int = 0
