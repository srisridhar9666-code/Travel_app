"""ORM models. Importing this package registers every table on Base.metadata,
which is what Alembic's autogenerate walks."""
from app.models.audit import GENESIS_HASH, AuditLog
from app.models.auth_token import AuthToken
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime, naive_utcnow, utcnow
from app.models.department import Department
from app.models.id_proof import IdProof
from app.models.location import Location
from app.models.preference import NotificationPreference
from app.models.project import Project
from app.models.request import (
    Notification,
    RequestRevision,
    RequestTraveller,
    TravelRequest,
)
from app.models.team_change import TeamChange
from app.models.ticket import TicketDocument
from app.models.user import User

__all__ = [
    "AuditLog",
    "AuthToken",
    "Department",
    "GENESIS_HASH",
    "IdProof",
    "Location",
    "Notification",
    "NotificationPreference",
    "Project",
    "RequestRevision",
    "RequestTraveller",
    "TravelRequest",
    "TeamChange",
    "TenantMixin",
    "TicketDocument",
    "TimestampMixin",
    "UTCDateTime",
    "User",
    "naive_utcnow",
    "utcnow",
]
