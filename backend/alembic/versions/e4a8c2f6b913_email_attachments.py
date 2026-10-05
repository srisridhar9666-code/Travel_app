"""a file sent with an email

Revision ID: e4a8c2f6b913
Revises: b7e3c5d9f142
Create Date: 2026-10-05 09:00:00.000000

The booking email carries the traveller's ticket. The notification row keeps
the file's storage path, name and type, and the file is read when the message
goes, so a retried email sends it too. Checks first, so an interrupted run can
be repeated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e4a8c2f6b913'
down_revision: Union[str, None] = 'b7e3c5d9f142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ('attachment_path', sa.String(500)),
    ('attachment_name', sa.String(255)),
    ('attachment_type', sa.String(100)),
)


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('notifications')}
    for name, kind in COLUMNS:
        if name not in have:
            op.add_column('notifications', sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column('notifications', name)
