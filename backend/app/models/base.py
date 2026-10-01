"""
Shared column mixins.

Every timestamp in this system is UTC. MySQL's DATETIME carries no timezone, so
the discipline is enforced in Python: write with `utcnow()`, and treat anything
read back as UTC.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings


#: MySQL's plain DATETIME stores whole seconds and silently drops the
#: microseconds Python wrote. That is merely untidy for most columns, but it
#: breaks the audit chain outright: the hash is computed over the timestamp
#: before the insert, so a truncated read-back never rehashes to the same value.
#: Every datetime in the schema therefore keeps full precision.
UTCDateTime = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    """Timezone-aware now, in UTC. The single source of time for the app."""
    return datetime.now(timezone.utc)


def naive_utcnow() -> datetime:
    """UTC now, stripped of tzinfo, to match what the DATETIME columns hold."""
    return utcnow().replace(tzinfo=None)


class TimestampMixin:
    """created_at / updated_at on every table that is not append-only."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=naive_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=naive_utcnow, onupdate=naive_utcnow, nullable=False
    )


class TenantMixin:
    """The product ships single-tenant, but carrying the tenant from day one means
    onboarding a second company is a config change rather than a data migration.
    Every query goes through a scoped dependency, never a bare `session.query`."""

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        default=lambda: get_settings().default_tenant,
        nullable=False,
        index=True,
    )
