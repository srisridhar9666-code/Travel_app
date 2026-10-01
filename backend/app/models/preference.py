"""
Per-person notification preferences (SOW section 4, addendum C3).

Opting out is stored as *rows that exist*: a person with no rows gets
everything, which is the right default for a system whose notices are mostly
operational. Only categories in `OPTIONAL_CATEGORIES` can be switched off -
being told your own travel was rejected is not something to unsubscribe from,
and an opt-out there would produce staff who turn up at airports.
"""
from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import NotificationCategory, NotificationChannel
from app.database import Base
from app.models.base import TimestampMixin


class NotificationPreference(Base, TimestampMixin):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "channel", name="uq_preference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[NotificationCategory] = mapped_column(
        SAEnum(NotificationCategory, native_enum=False, length=20, validate_strings=True),
        nullable=False,
    )
    #: Which transport this preference governs. In-app is never switchable: the
    #: row is the record, and hiding it would hide the audit trail from the
    #: person it is about.
    channel: Mapped[NotificationChannel] = mapped_column(
        SAEnum(NotificationChannel, native_enum=False, length=20, validate_strings=True),
        default=NotificationChannel.EMAIL,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user = relationship("User", lazy="noload")
