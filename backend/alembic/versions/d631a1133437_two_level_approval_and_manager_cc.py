"""two level approval and manager cc

Revision ID: d631a1133437
Revises: 7e44f1e5f91e
Create Date: 2026-10-04 09:35:37.317415

- request_travellers: the traveller's manager's recommendation (recommended or
  not), their comment, who gave it and when. Advice an admin sees before the
  final decision; nullable, because most rows predate it and some travellers
  have no manager.
- notifications.cc_addresses: who an email was copied to - the traveller's
  manager on a decision.

Every step checks whether it is already there first. MySQL commits each DDL
statement on its own, so a run interrupted half-way would otherwise leave a
database that every later "alembic upgrade head" fails on ("Duplicate column
name"), with no way forward but hand-written SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'd631a1133437'
down_revision: Union[str, None] = '7e44f1e5f91e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REVIEWER_FK = 'fk_request_travellers_manager_reviewed_by'


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c['name'] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    if 'cc_addresses' not in _columns('notifications'):
        op.add_column('notifications', sa.Column('cc_addresses', sa.String(length=500), nullable=True))

    have = _columns('request_travellers')
    if 'manager_recommendation' not in have:
        op.add_column('request_travellers', sa.Column('manager_recommendation', sa.Enum('RECOMMENDED', 'NOT_RECOMMENDED', name='managerrecommendation', native_enum=False, length=20), nullable=True))
    if 'manager_comment' not in have:
        op.add_column('request_travellers', sa.Column('manager_comment', sa.String(length=500), nullable=True))
    if 'manager_reviewed_by_id' not in have:
        op.add_column('request_travellers', sa.Column('manager_reviewed_by_id', sa.Integer(), nullable=True))
    if 'manager_reviewed_at' not in have:
        op.add_column('request_travellers', sa.Column('manager_reviewed_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))

    if REVIEWER_FK not in {fk['name'] for fk in _inspector().get_foreign_keys('request_travellers')}:
        op.create_foreign_key(REVIEWER_FK, 'request_travellers', 'users', ['manager_reviewed_by_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # The recommendations go with the columns; decisions themselves are
    # untouched, so the older code reads every request as before.
    op.drop_constraint(REVIEWER_FK, 'request_travellers', type_='foreignkey')
    op.drop_column('request_travellers', 'manager_reviewed_at')
    # MySQL drops the index it made for the foreign key along with its column.
    op.drop_column('request_travellers', 'manager_reviewed_by_id')
    op.drop_column('request_travellers', 'manager_comment')
    op.drop_column('request_travellers', 'manager_recommendation')
    op.drop_column('notifications', 'cc_addresses')
