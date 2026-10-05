"""cab type, distance, the car booked and one more day

Revision ID: b4e04150e4dc
Revises: d631a1133437
Create Date: 2026-10-04 10:00:48.959670

- travel_requests: what a cab asks for (cab_type: no preference, Dzire or
  Ertiga; cab_trip: local or outstation; cab_distance_km), the car an admin
  recorded as sent (booked_cab_type, vehicle number, driver name and phone, who
  recorded it and when), and the latest ask to keep it one more day (status,
  reason, who asked and decided, when, the admin's comment) with
  cab_extended_days counting the days approved.
- Cabs raised before this were all local cabs with no preference asked, so
  they are filled in as exactly that; flights and hotels stay null.

Every step checks whether it is already there first. MySQL commits each DDL
statement on its own, so a run interrupted half-way would otherwise leave a
database that every later "alembic upgrade head" fails on ("Duplicate column
name"), with no way forward but hand-written SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'b4e04150e4dc'
down_revision: Union[str, None] = 'd631a1133437'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'travel_requests'
BOOKED_BY_FK = 'fk_travel_requests_cab_booked_by'
REQUESTED_BY_FK = 'fk_travel_requests_cab_ext_requested_by'
DECIDED_BY_FK = 'fk_travel_requests_cab_ext_decided_by'


def _when():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')


def _cab_type():
    return sa.Enum('NO_PREFERENCE', 'SEDAN', 'SUV', name='cabtype', native_enum=False, length=20)


def _new_columns() -> list[sa.Column]:
    """In the order they are added; dropped in reverse. Built afresh on each
    call because a Column can belong to only one table object."""
    return [
        sa.Column('cab_type', _cab_type(), nullable=True),
        sa.Column('cab_trip', sa.Enum('LOCAL', 'OUTSTATION', name='cabtrip', native_enum=False, length=20), nullable=True),
        sa.Column('cab_distance_km', sa.Integer(), nullable=True),
        sa.Column('booked_cab_type', _cab_type(), nullable=True),
        sa.Column('cab_vehicle_number', sa.String(length=20), nullable=True),
        sa.Column('cab_driver_name', sa.String(length=120), nullable=True),
        sa.Column('cab_driver_phone', sa.String(length=32), nullable=True),
        sa.Column('cab_booked_by_id', sa.Integer(), nullable=True),
        sa.Column('cab_booked_at', _when(), nullable=True),
        sa.Column('cab_extension_status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='cabextensionstatus', native_enum=False, length=20), nullable=True),
        sa.Column('cab_extension_reason', sa.String(length=500), nullable=True),
        sa.Column('cab_extension_requested_by_id', sa.Integer(), nullable=True),
        sa.Column('cab_extension_requested_at', _when(), nullable=True),
        sa.Column('cab_extension_decided_by_id', sa.Integer(), nullable=True),
        sa.Column('cab_extension_decided_at', _when(), nullable=True),
        sa.Column('cab_extension_comment', sa.String(length=500), nullable=True),
        sa.Column('cab_extended_days', sa.Integer(), server_default='0', nullable=False),
    ]


#: Foreign key name -> its column. Named, so a resumed run can tell whether
#: each one is there.
FOREIGN_KEYS = {
    BOOKED_BY_FK: 'cab_booked_by_id',
    REQUESTED_BY_FK: 'cab_extension_requested_by_id',
    DECIDED_BY_FK: 'cab_extension_decided_by_id',
}


def _inspector():
    return sa.inspect(op.get_bind())


def _columns() -> set[str]:
    return {c['name'] for c in _inspector().get_columns(TABLE)}


def _foreign_keys() -> set[str]:
    return {fk['name'] for fk in _inspector().get_foreign_keys(TABLE)}


def upgrade() -> None:
    have = _columns()
    for column in _new_columns():
        if column.name not in have:
            op.add_column(TABLE, column)

    present = _foreign_keys()
    for name, column in FOREIGN_KEYS.items():
        if name not in present:
            op.create_foreign_key(name, TABLE, 'users', [column], ['id'], ondelete='SET NULL')

    # Safe to repeat: only rows still empty are touched.
    op.execute(
        "UPDATE travel_requests SET cab_type = 'NO_PREFERENCE' "
        "WHERE request_type = 'LOCAL_CAB' AND cab_type IS NULL"
    )
    op.execute(
        "UPDATE travel_requests SET cab_trip = 'LOCAL' "
        "WHERE request_type = 'LOCAL_CAB' AND cab_trip IS NULL"
    )


def downgrade() -> None:
    # The cab details go with the columns. An approved extension already moved
    # end_at, which stays: the booking really was that long.
    present = _foreign_keys()
    for name in FOREIGN_KEYS:
        if name in present:
            op.drop_constraint(name, TABLE, type_='foreignkey')
    have = _columns()
    for column in reversed(_new_columns()):
        if column.name in have:
            # MySQL drops the index it made for a foreign key along with its column.
            op.drop_column(TABLE, column.name)
