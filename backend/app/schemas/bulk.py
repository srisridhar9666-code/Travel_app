"""
Bulk import of the ground team from a CSV.

Import is two calls, not one: `preview` parses and validates without writing
anything, and `commit` applies it. Creating a hundred accounts - each with an
invitation - is not something an admin should discover the shape of only after
it has happened.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.core.enums import Designation, Gender, Role

#: Accepted headers, in the order the template writes them.
IMPORT_COLUMNS = [
    "full_name",
    "email",
    "role",
    "designation",
    "gender",
    "phone",
    "employee_code",
    "department",
    "base_state",
    "base_location",
]

REQUIRED_COLUMNS = {"full_name", "email"}


class ImportRow(BaseModel):
    """One parsed line, with whatever is wrong with it."""

    line: int
    full_name: str | None = None
    email: str | None = None
    role: Role = Role.GROUND_STAFF
    designation: Designation | None = None
    #: None only on a row that is an error for want of one.
    gender: Gender | None = None
    phone: str | None = None
    employee_code: str | None = None
    #: A department name; created on commit if it does not exist yet.
    department: str | None = None
    base_state: str | None = None
    #: The city or constituency.
    base_location: str | None = None

    #: Hard problems. A row with any of these is skipped on commit.
    errors: list[str] = []
    #: Worth knowing, but not disqualifying.
    warnings: list[str] = []

    @property
    def importable(self) -> bool:
        return not self.errors


class ImportPreview(BaseModel):
    rows: list[ImportRow]
    total: int
    importable: int
    skipped: int
    #: Problems with the file itself rather than any one row.
    file_errors: list[str] = []


class ImportResult(BaseModel):
    created: int
    skipped: int
    invite_urls: dict[str, str]
    errors: list[str] = []
    #: True when the invites are being emailed in the background. When false,
    #: `email_detail` says why, and the links above are the only copy.
    emailing: bool = False
    email_detail: str | None = None
