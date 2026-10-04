"""asking to cancel a decided trip

Revision ID: 9d2f4b6a8c31
Revises: 3c1e9a7b5d20
Create Date: 2026-10-04 16:20:00.000000

Once an admin has approved or booked someone on a trip, the requester can no
longer withdraw it outright; they ask, and an admin or their manager decides.
These columns hold the ask and its answer.

Each step checks first, so a run cut off half-way can simply be repeated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '9d2f4b6a8c31'
down_revision: Union[str, None] = '3c1e9a7b5d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAMP = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')
COLUMNS = [
    sa.Column('cancellation_status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='cancellationstatus', native_enum=False, length=20), nullable=True),
    sa.Column('cancellation_reason', sa.String(length=500), nullable=True),
    sa.Column('cancellation_requested_by_id', sa.Integer(), nullable=True),
    sa.Column('cancellation_requested_at', _STAMP, nullable=True),
    sa.Column('cancellation_decided_by_id', sa.Integer(), nullable=True),
    sa.Column('cancellation_decided_at', _STAMP, nullable=True),
    sa.Column('cancellation_comment', sa.String(length=500), nullable=True),
]
FKS = {
    'fk_travel_requests_cxl_requested_by': 'cancellation_requested_by_id',
    'fk_travel_requests_cxl_decided_by': 'cancellation_decided_by_id',
}


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('travel_requests')}
    for column in COLUMNS:
        if column.name not in have:
            op.add_column('travel_requests', column.copy())
    fks = {fk['name'] for fk in sa.inspect(op.get_bind()).get_foreign_keys('travel_requests')}
    for name, column in FKS.items():
        if name not in fks:
            op.create_foreign_key(name, 'travel_requests', 'users', [column], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    for name in FKS:
        op.drop_constraint(name, 'travel_requests', type_='foreignkey')
    for column in reversed(COLUMNS):
        op.drop_column('travel_requests', column.name)
