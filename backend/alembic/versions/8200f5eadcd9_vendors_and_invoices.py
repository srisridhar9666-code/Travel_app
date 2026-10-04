"""vendors and invoices

Revision ID: 8200f5eadcd9
Revises: b4e04150e4dc
Create Date: 2026-10-04 13:26:18.677137

- vendors: the travel agents, cab operators and hotels an organisation pays,
  unique by name per organisation, switched off rather than deleted.
- request_travellers.vendor_id: who was paid for that person's trip, recorded
  with the cost. Set null if a vendor row ever goes; nothing else is touched,
  so every existing cost simply has no vendor recorded yet.
- invoices: one vendor, one period, a number INV-<year>-<n> unique per
  organisation, a status (draft, submitted, approved, rejected), the total
  (always the sum of its lines), and who prepared, submitted and decided it.
- invoice_lines: one traveller's cost on one invoice, with the amount and a
  description kept as they were billed. A traveller is on at most one line in
  the whole table (uq_invoice_lines_traveller), so no cost is billed twice.

Every step checks whether it is already there first. MySQL commits each DDL
statement on its own, so a run interrupted half-way would otherwise leave a
database that every later "alembic upgrade head" fails on ("Table already
exists", "Duplicate column name"), with no way forward but hand-written SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '8200f5eadcd9'
down_revision: Union[str, None] = 'b4e04150e4dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRAVELLERS = 'request_travellers'
TRAVELLER_VENDOR_FK = 'fk_request_travellers_vendor'


def _when():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')


def _inspector():
    return sa.inspect(op.get_bind())


def _indexes(table: str) -> set[str]:
    return {ix['name'] for ix in _inspector().get_indexes(table)}


def _index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _inspector().has_table('vendors'):
        op.create_table('vendors',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.Enum('TRAVEL_AGENT', 'CAB', 'HOTEL', 'OTHER', name='vendorkind', native_enum=False, length=20), nullable=False),
        sa.Column('contact_name', sa.String(length=120), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('gstin', sa.String(length=15), nullable=True),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('created_at', _when(), nullable=False),
        sa.Column('updated_at', _when(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    _index(op.f('ix_vendors_tenant_id'), 'vendors', ['tenant_id'])
    _index('uq_vendors_tenant_name', 'vendors', ['tenant_id', 'name'], unique=True)

    if not _inspector().has_table('invoices'):
        op.create_table('invoices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('number', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('status', sa.Enum('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', name='invoicestatus', native_enum=False, length=12), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('vendor_invoice_ref', sa.String(length=80), nullable=True),
        sa.Column('notes', sa.String(length=1000), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('submitted_by_id', sa.Integer(), nullable=True),
        sa.Column('submitted_at', _when(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', _when(), nullable=True),
        sa.Column('decision_comment', sa.String(length=500), nullable=True),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('created_at', _when(), nullable=False),
        sa.Column('updated_at', _when(), nullable=False),
        sa.CheckConstraint('period_start <= period_end', name='ck_invoices_period'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_invoices_created_by', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], name='fk_invoices_decided_by', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['submitted_by_id'], ['users.id'], name='fk_invoices_submitted_by', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_invoices_updated_by', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], name='fk_invoices_vendor', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
        )
    _index(op.f('ix_invoices_tenant_id'), 'invoices', ['tenant_id'])
    _index('ix_invoices_tenant_status', 'invoices', ['tenant_id', 'status'])
    _index(op.f('ix_invoices_vendor_id'), 'invoices', ['vendor_id'])
    _index('uq_invoices_tenant_number', 'invoices', ['tenant_id', 'number'], unique=True)

    if not _inspector().has_table('invoice_lines'):
        op.create_table('invoice_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('request_traveller_id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False),
        sa.Column('travel_date', sa.Date(), nullable=True),
        sa.Column('created_at', _when(), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name='fk_invoice_lines_invoice', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['request_id'], ['travel_requests.id'], name='fk_invoice_lines_request', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['request_traveller_id'], ['request_travellers.id'], name='fk_invoice_lines_traveller', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_traveller_id', name='uq_invoice_lines_traveller')
        )
    _index(op.f('ix_invoice_lines_invoice_id'), 'invoice_lines', ['invoice_id'])
    _index(op.f('ix_invoice_lines_request_id'), 'invoice_lines', ['request_id'])

    if 'vendor_id' not in {c['name'] for c in _inspector().get_columns(TRAVELLERS)}:
        op.add_column(TRAVELLERS, sa.Column('vendor_id', sa.Integer(), nullable=True))
    _index(op.f('ix_request_travellers_vendor_id'), TRAVELLERS, ['vendor_id'])
    if TRAVELLER_VENDOR_FK not in {fk['name'] for fk in _inspector().get_foreign_keys(TRAVELLERS)}:
        op.create_foreign_key(
            TRAVELLER_VENDOR_FK, TRAVELLERS, 'vendors', ['vendor_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    # The vendor recorded against each cost goes with the column; the costs
    # themselves stay. Invoices and vendors go entirely.
    if TRAVELLER_VENDOR_FK in {fk['name'] for fk in _inspector().get_foreign_keys(TRAVELLERS)}:
        op.drop_constraint(TRAVELLER_VENDOR_FK, TRAVELLERS, type_='foreignkey')
    if 'vendor_id' in {c['name'] for c in _inspector().get_columns(TRAVELLERS)}:
        # MySQL drops the column's index along with it.
        op.drop_column(TRAVELLERS, 'vendor_id')
    # Each table's indexes go with it; dropping them first fails on the ones
    # its foreign keys still use. Children before the tables they point at.
    for table in ('invoice_lines', 'invoices', 'vendors'):
        if _inspector().has_table(table):
            op.drop_table(table)
