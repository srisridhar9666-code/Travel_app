"""booking details on each traveller

Revision ID: b7e3c5d9f142
Revises: 9d2f4b6a8c31
Create Date: 2026-10-04 17:05:00.000000

What a traveller needs on the day beside the booking reference - airline or
operator, flight/train/bus number, departure and arrival, seat, or the hotel's
name and address - typed when marking booked or taken from a confirmed ticket.
Checks first, so an interrupted run can be repeated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7e3c5d9f142'
down_revision: Union[str, None] = '9d2f4b6a8c31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('request_travellers')}
    if 'booking_details' not in have:
        op.add_column('request_travellers', sa.Column('booking_details', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('request_travellers', 'booking_details')
