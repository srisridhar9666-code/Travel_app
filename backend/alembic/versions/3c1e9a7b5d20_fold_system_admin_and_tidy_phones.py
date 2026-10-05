"""fold system admin into admin; store staff mobiles as ten digits

Revision ID: 3c1e9a7b5d20
Revises: 8200f5eadcd9
Create Date: 2026-10-04 15:10:00.000000

- Admin and system admin had become the same job (the one difference, data
  retention, is now the super admin's), so every system admin becomes an
  admin. The value stays in the code only so a row written by old code loads.
- Staff phone numbers are now stored as a bare ten-digit Indian mobile. Numbers
  saved before ("+91 98765 43210", "098765 43210") are rewritten to that form
  where they clearly are one; anything else is left for an admin to correct.

Data only, and each statement is safe to run twice, so an interrupted run can
simply be repeated.
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3c1e9a7b5d20'
down_revision: Union[str, None] = '8200f5eadcd9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MOBILE = re.compile(r"[6-9]\d{9}")


def _ten_digits(value: str) -> str | None:
    digits = re.sub(r"[\s\-()]", "", value or "")
    if digits.startswith("+91") and len(digits) == 13:
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if _MOBILE.fullmatch(digits) else None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE users SET role = 'ADMIN' WHERE role = 'SYSTEM_ADMIN'"))

    rows = bind.execute(sa.text("SELECT id, phone FROM users WHERE phone IS NOT NULL")).all()
    for user_id, phone in rows:
        tidy = _ten_digits(phone)
        if tidy and tidy != phone:
            bind.execute(
                sa.text("UPDATE users SET phone = :phone WHERE id = :id"),
                {"phone": tidy, "id": user_id},
            )


def downgrade() -> None:
    # Nothing to undo: who used to be a system admin is not recorded anywhere,
    # and the ten-digit numbers are valid under the old rules too.
    pass
