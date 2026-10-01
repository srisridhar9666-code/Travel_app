"""
Single-use tokens for invites and password resets.

Only a hash of the token is stored. The raw value exists once, in the link that
goes to the user, so a database leak cannot be replayed into account takeover.
"""
from datetime import datetime

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import TokenPurpose
from app.database import Base
from app.models.base import TenantMixin, UTCDateTime, naive_utcnow


class AuthToken(Base, TenantMixin):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # This table has two FKs to users (the subject and the admin who issued it),
    # so the join has to say which one it means.
    user = relationship("User", foreign_keys=[user_id], lazy="joined")

    purpose: Mapped[TokenPurpose] = mapped_column(
        SAEnum(TokenPurpose, native_enum=False, length=24, validate_strings=True),
        nullable=False,
    )

    # SHA-256 of the raw token. Indexed unique so lookup is a single hit and a
    # collision cannot silently authenticate the wrong person.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=naive_utcnow, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > naive_utcnow()
