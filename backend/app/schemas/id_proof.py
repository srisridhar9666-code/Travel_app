"""
Request and response bodies for identity documents.

Note what is absent: no schema here carries a full document number outward except
`IdProofReveal`, which is returned by exactly one endpoint, and only after that
endpoint has written a VIEW_SENSITIVE audit row.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import IdProofType
from app.schemas.common import UTCInstant


class IdProofCreate(BaseModel):
    proof_type: IdProofType
    number: str = Field(min_length=4, max_length=40)
    label: str | None = Field(default=None, max_length=80)
    issued_on: date | None = None
    expires_on: date | None = None

    @field_validator("number")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _dates_make_sense(self):
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            raise ValueError("Expiry date cannot be before the issue date.")
        return self


class IdProofUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    issued_on: date | None = None
    expires_on: date | None = None


class IdProofRead(BaseModel):
    """The safe view. Shows enough to identify a document, never the number."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    proof_type: IdProofType
    label: str | None = None

    #: e.g. "XXXX XXXX 9012" - matchable against the physical card, useless alone.
    masked_number: str | None = None

    issued_on: date | None = None
    expires_on: date | None = None
    is_expired: bool
    has_file: bool
    file_name: str | None = None
    file_size: int | None = None

    #: True once retention has emptied this record.
    is_purged: bool
    purged_at: UTCInstant | None = None
    created_at: UTCInstant


class IdProofReveal(BaseModel):
    """Returned by the one endpoint that decrypts. Always audited."""

    id: int
    proof_type: IdProofType
    number: str


class RetentionStatus(BaseModel):
    retention_days: int
    cutoff: str
    due_now: int
