"""managers, super admin and team changes

Revision ID: 7e44f1e5f91e
Revises: 5a5a6fc728fb
Create Date: 2026-10-04 08:21:41.340535

- users.manager_id: who a team member reports to (one level)
- team_changes: a manager's add / edit / remove request, held for an admin
- the earliest active system admin becomes the first super admin, so the new
  top role is never empty. Roles are stored as text, so the new values need no
  column change.

Every step checks whether it is already there first. MySQL commits each DDL
statement on its own, so a run interrupted half-way would otherwise leave a
database that every later "alembic upgrade head" fails on ("Table already
exists"), with no way forward but hand-written SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '7e44f1e5f91e'
down_revision: Union[str, None] = '5a5a6fc728fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MANAGER_FK = 'fk_users_manager_id'


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()

    if not inspector.has_table('team_changes'):
        op.create_table('team_changes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.Enum('ADD', 'EDIT', 'REMOVE', name='teamchangekind', native_enum=False, length=10), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED', name='teamchangestatus', native_enum=False, length=12), nullable=False),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
        sa.Column('decision_comment', sa.String(length=500), nullable=True),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
    indexes = {ix['name'] for ix in _inspector().get_indexes('team_changes')}
    if 'ix_team_changes_requested_by' not in indexes:
        op.create_index('ix_team_changes_requested_by', 'team_changes', ['requested_by_id'], unique=False)
    if 'ix_team_changes_tenant_id' not in indexes:
        op.create_index(op.f('ix_team_changes_tenant_id'), 'team_changes', ['tenant_id'], unique=False)
    if 'ix_team_changes_tenant_status' not in indexes:
        op.create_index('ix_team_changes_tenant_status', 'team_changes', ['tenant_id', 'status'], unique=False)

    if 'manager_id' not in {c['name'] for c in inspector.get_columns('users')}:
        op.add_column('users', sa.Column('manager_id', sa.Integer(), nullable=True))
    if 'ix_users_manager_id' not in {ix['name'] for ix in _inspector().get_indexes('users')}:
        op.create_index(op.f('ix_users_manager_id'), 'users', ['manager_id'], unique=False)
    if MANAGER_FK not in {fk['name'] for fk in _inspector().get_foreign_keys('users')}:
        op.create_foreign_key(MANAGER_FK, 'users', 'users', ['manager_id'], ['id'], ondelete='SET NULL')

    # The first super admin, per organisation that has none yet: its earliest
    # active system admin - in practice the bootstrap account.
    op.execute(
        "UPDATE users u JOIN ("
        "  SELECT MIN(id) AS id FROM users"
        "  WHERE role = 'SYSTEM_ADMIN' AND status = 'ACTIVE'"
        "  AND tenant_id NOT IN (SELECT tenant_id FROM ("
        "    SELECT DISTINCT tenant_id FROM users WHERE role = 'SUPER_ADMIN') AS has_one)"
        "  GROUP BY tenant_id"
        ") first_admin ON first_admin.id = u.id "
        "SET u.role = 'SUPER_ADMIN'"
    )


def downgrade() -> None:
    # Back to the three old roles: the top one folds into system admin and
    # managers become ground staff, so no row holds a value the old code
    # cannot read.
    op.execute("UPDATE users SET role = 'SYSTEM_ADMIN' WHERE role = 'SUPER_ADMIN'")
    op.execute("UPDATE users SET role = 'GROUND_STAFF' WHERE role = 'MANAGER'")
    op.drop_constraint(MANAGER_FK, 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_manager_id'), table_name='users')
    op.drop_column('users', 'manager_id')
    # Its indexes go with it; dropping them first fails on the ones its
    # foreign keys still use.
    op.drop_table('team_changes')
