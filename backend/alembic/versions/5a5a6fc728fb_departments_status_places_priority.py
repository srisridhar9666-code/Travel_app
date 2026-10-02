"""departments, employee status, campaign and base places, request priority

Revision ID: 5a5a6fc728fb
Revises: 6424d2d65f2e
Create Date: 2026-10-02 09:02:10.881531

- departments, and users.department_id
- users.status (ACTIVE / DEACTIVATED / LEFT / DELETED), filled from the old
  is_active and exited_on; users.status_changed_at
- users.password_changed_at, so a password change signs out other devices
- users.base_state, beside base_location (now the city)
- projects.state and projects.city, filled from the free-text location where
  it names a known place
- travel_requests.priority, MEDIUM for every existing request
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '5a5a6fc728fb'
down_revision: Union[str, None] = '6424d2d65f2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEPARTMENT_FK = 'fk_users_department_id'


def upgrade() -> None:
    op.create_table('departments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('tenant_id', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_departments_tenant_id'), 'departments', ['tenant_id'], unique=False)
    op.create_index('uq_departments_tenant_name', 'departments', ['tenant_id', 'name'], unique=True)
    op.add_column('projects', sa.Column('state', sa.String(length=80), nullable=True))
    op.add_column('projects', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('travel_requests', sa.Column('priority', sa.Enum('HIGH', 'MEDIUM', 'LOW', name='requestpriority', native_enum=False, length=20), server_default='MEDIUM', nullable=False))
    op.add_column('users', sa.Column('base_state', sa.String(length=80), nullable=True))
    op.add_column('users', sa.Column('department_id', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('status', sa.Enum('ACTIVE', 'DEACTIVATED', 'LEFT', 'DELETED', name='userstatus', native_enum=False, length=20), server_default='ACTIVE', nullable=False))
    op.add_column('users', sa.Column('status_changed_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))
    op.create_index('ix_users_tenant_status', 'users', ['tenant_id', 'status'], unique=False)
    op.create_foreign_key(DEPARTMENT_FK, 'users', 'departments', ['department_id'], ['id'], ondelete='SET NULL')

    # Status from what was recorded before it existed. Someone with an exit
    # date has left, whatever is_active says; then is_active is made to agree,
    # because from here on it only mirrors status.
    op.execute(
        "UPDATE users SET status = CASE "
        "WHEN exited_on IS NOT NULL THEN 'LEFT' "
        "WHEN is_active = 0 THEN 'DEACTIVATED' "
        "ELSE 'ACTIVE' END"
    )
    op.execute("UPDATE users SET is_active = (status = 'ACTIVE')")

    # A campaign's free-text location becomes a state (and city) where it
    # names a known place; anything else stays as legacy text. A no-op on a
    # fresh database, whose place list is only seeded at start-up.
    op.execute(
        "UPDATE projects p "
        "JOIN (SELECT DISTINCT tenant_id, state FROM locations) l "
        "ON l.tenant_id = p.tenant_id AND l.state = TRIM(p.location) "
        "SET p.state = l.state "
        "WHERE p.state IS NULL AND p.location IS NOT NULL AND TRIM(p.location) <> ''"
    )
    op.execute(
        "UPDATE projects p "
        "JOIN (SELECT DISTINCT tenant_id, state FROM locations) l "
        "ON l.tenant_id = p.tenant_id AND l.state = TRIM(SUBSTRING_INDEX(p.location, ',', -1)) "
        "SET p.state = l.state, p.city = NULLIF(TRIM(SUBSTRING_INDEX(p.location, ',', 1)), '') "
        "WHERE p.state IS NULL AND p.location LIKE '%,%'"
    )
    op.execute(
        "UPDATE projects p "
        "JOIN (SELECT tenant_id, city, MIN(state) AS state FROM locations "
        "      GROUP BY tenant_id, city HAVING COUNT(DISTINCT state) = 1) l "
        "ON l.tenant_id = p.tenant_id AND l.city = TRIM(p.location) "
        "SET p.state = l.state, p.city = l.city "
        "WHERE p.state IS NULL AND p.location IS NOT NULL"
    )

    # A base location that is a city in exactly one state gets that state.
    op.execute(
        "UPDATE users u "
        "JOIN (SELECT tenant_id, city, MIN(state) AS state FROM locations "
        "      GROUP BY tenant_id, city HAVING COUNT(DISTINCT state) = 1) l "
        "ON l.tenant_id = u.tenant_id AND l.city = TRIM(u.base_location) "
        "SET u.base_state = l.state, u.base_location = l.city "
        "WHERE u.base_state IS NULL AND u.base_location IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint(DEPARTMENT_FK, 'users', type_='foreignkey')
    op.drop_index('ix_users_tenant_status', table_name='users')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'status_changed_at')
    op.drop_column('users', 'status')
    op.drop_column('users', 'department_id')
    op.drop_column('users', 'base_state')
    op.drop_column('travel_requests', 'priority')
    op.drop_column('projects', 'city')
    op.drop_column('projects', 'state')
    op.drop_index('uq_departments_tenant_name', table_name='departments')
    op.drop_index(op.f('ix_departments_tenant_id'), table_name='departments')
    op.drop_table('departments')
