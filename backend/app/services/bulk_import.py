"""
Parsing and validating a bulk-import CSV.

Kept apart from the router so `preview` and `commit` provably see the same
result: both call `parse`, and commit simply writes what preview showed.
"""
from __future__ import annotations

import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import Designation, Gender, Role, UserStatus
from app.models.department import Department
from app.models.user import User
from app.schemas.bulk import IMPORT_COLUMNS, REQUIRED_COLUMNS, ImportPreview, ImportRow
from app.schemas.user import tidy_phone
from app.services import locations

#: Deliberately permissive - the authority on an address is the invite that
#: either arrives or does not. This only catches obvious nonsense.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

MAX_ROWS = 1000


def _decode(raw: bytes) -> tuple[str, list[str]]:
    """Decode a spreadsheet export without tripping over Excel's habits."""
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), errors
        except UnicodeDecodeError:
            continue
    errors.append("Could not read this file as text. Save it as CSV (UTF-8).")
    return "", errors


def _enum_or_error(
    raw: str, enum_cls, field: str, row: ImportRow, *, default=None
):
    value = (raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not value:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        row.errors.append(f"{field} must be one of: {allowed}")
        return default


#: What a spreadsheet may say for gender. Only Male and Female can be recorded;
#: the room-sharing rule depends on it, so a blank is an error, not a default.
_GENDERS = {"M": Gender.MALE, "MALE": Gender.MALE, "F": Gender.FEMALE, "FEMALE": Gender.FEMALE}


def _gender_or_error(raw: str, row: ImportRow) -> Gender | None:
    value = (raw or "").strip().upper()
    if not value:
        row.errors.append("gender is required (MALE or FEMALE)")
        return None
    if value not in _GENDERS:
        row.errors.append("gender must be MALE or FEMALE")
        return None
    return _GENDERS[value]


def parse(raw: bytes, db: Session, tenant_id: str) -> ImportPreview:
    """Turn an uploaded CSV into validated rows. Writes nothing."""
    text, file_errors = _decode(raw)
    if file_errors:
        return ImportPreview(rows=[], total=0, importable=0, skipped=0, file_errors=file_errors)

    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = [(h or "").strip().lower().replace(" ", "_") for h in (reader.fieldnames or [])]
    except csv.Error as exc:
        return ImportPreview(
            rows=[], total=0, importable=0, skipped=0,
            file_errors=[f"Could not parse this CSV: {exc}"],
        )

    missing = REQUIRED_COLUMNS - set(headers)
    if missing:
        return ImportPreview(
            rows=[], total=0, importable=0, skipped=0,
            file_errors=[
                f"Missing required column(s): {', '.join(sorted(missing))}. "
                f"Expected headers: {', '.join(IMPORT_COLUMNS)}."
            ],
        )

    unknown = [h for h in headers if h and h not in IMPORT_COLUMNS]

    # One query for every address already taken, rather than one per row. A
    # deleted account keeps its address, and is restored rather than re-added.
    existing = {
        email.lower(): user_status
        for email, user_status in db.execute(
            select(User.email, User.status).where(User.tenant_id == tenant_id)
        ).all()
    }
    departments = {
        name.casefold()
        for (name,) in db.execute(
            select(Department.name).where(Department.tenant_id == tenant_id)
        ).all()
    }
    new_departments: set[str] = set()
    seen_in_file: set[str] = set()

    rows: list[ImportRow] = []
    for index, raw_row in enumerate(reader, start=2):  # line 1 is the header
        if len(rows) >= MAX_ROWS:
            file_errors.append(f"Only the first {MAX_ROWS} rows were read.")
            break

        clean = {
            (key or "").strip().lower().replace(" ", "_"): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if not any(clean.values()):
            continue  # blank line

        row = ImportRow(line=index)

        name = clean.get("full_name", "")
        if len(name) < 2:
            row.errors.append("full_name is required")
        else:
            row.full_name = " ".join(name.split())

        email = clean.get("email", "").lower()
        if not email:
            row.errors.append("email is required")
        elif not _EMAIL.match(email):
            row.errors.append(f"{email} is not a valid email address")
        elif existing.get(email) is UserStatus.DELETED:
            row.errors.append(
                f"{email} belongs to a deleted account - restore it from the team list"
            )
        elif email in existing:
            row.errors.append(f"{email} already has an account")
        elif email in seen_in_file:
            row.errors.append(f"{email} appears more than once in this file")
        else:
            seen_in_file.add(email)
        row.email = email or None

        row.role = _enum_or_error(clean.get("role", ""), Role, "role", row, default=Role.GROUND_STAFF)
        row.designation = _enum_or_error(
            clean.get("designation", ""), Designation, "designation", row
        )
        row.gender = _gender_or_error(clean.get("gender", ""), row)

        try:
            row.phone = tidy_phone(clean.get("phone"))
        except ValueError as exc:
            row.errors.append(f"phone: {exc}")
        row.employee_code = clean.get("employee_code") or None

        # Matched against the place list, as the form does: "hyd" is Hyderabad.
        # Read-only, so safe inside a preview.
        row.base_state, row.base_location = locations.canonical(
            db, tenant_id, clean.get("base_state"), clean.get("base_location")
        )
        if len(row.base_state or "") > 80:
            row.errors.append("base_state must be at most 80 characters")
        if len(row.base_location or "") > 120:
            row.errors.append("base_location must be at most 120 characters")

        department = " ".join(clean.get("department", "").split())
        if department:
            if not 2 <= len(department) <= 80:
                row.errors.append("department must be 2 to 80 characters")
            else:
                row.department = department
                key = department.casefold()
                if key not in departments and key not in new_departments:
                    # Said once, on the first row that names it.
                    new_departments.add(key)
                    row.warnings.append(f"Department “{department}” will be created")

        if row.role in (Role.ADMIN, Role.SYSTEM_ADMIN):
            row.warnings.append(f"This row grants {row.role} access")

        rows.append(row)

    if unknown:
        file_errors.append(f"Ignored unrecognised column(s): {', '.join(unknown)}")

    importable = sum(1 for row in rows if row.importable)
    return ImportPreview(
        rows=rows,
        total=len(rows),
        importable=importable,
        skipped=len(rows) - importable,
        file_errors=file_errors,
    )


def template_csv() -> str:
    """A starter file with the headers and one worked example."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(IMPORT_COLUMNS)
    writer.writerow(
        [
            "Ravi Kumar",
            "ravi.kumar@designboxed.com",
            "GROUND_STAFF",
            "EXECUTIVE",
            "MALE",
            "+91 98765 43210",
            "DB-1042",
            "Field Operations",
            "Telangana",
            "Hyderabad",
        ]
    )
    return buffer.getvalue()
