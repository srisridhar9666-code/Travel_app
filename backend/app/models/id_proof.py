"""
Identity documents (SOW section 5, "ID Proofs").

The most sensitive data this system holds, and the SOW says nothing about how to
hold it. Addendum B8 and C4 set the rules implemented here:

* The number is **encrypted at rest** (`app/core/pii.py`). Only `number_last4`
  and the masked rendering are readable without decrypting.
* Reading the real number, or downloading the scan, writes a `VIEW_SENSITIVE`
  audit row. There is no unlogged read path.
* Scans live outside the web root and are never served statically.
* Records are **purged 90 days after the employee's exit date**. The row survives
  as a tombstone so the audit trail still shows a document once existed; the
  number and the file do not.
"""
from datetime import date, datetime

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import IdProofType
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class IdProof(Base, TenantMixin, TimestampMixin):
    __tablename__ = "id_proofs"
    __table_args__ = (
        Index("ix_id_proofs_user", "tenant_id", "user_id"),
        # Catches the same document being filed against two different people,
        # without decrypting anything.
        Index("ix_id_proofs_fingerprint", "tenant_id", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user = relationship("User", foreign_keys=[user_id], lazy="noload")

    proof_type: Mapped[IdProofType] = mapped_column(
        SAEnum(IdProofType, native_enum=False, length=20, validate_strings=True),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # --- the number ---------------------------------------------------------
    #: Fernet ciphertext. Null once the record has been purged.
    number_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Enough to recognise the document without decrypting it.
    number_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: The display string, rendered once at write time from the real number.
    #: Stored rather than reconstructed so the UI and the ledger cannot drift
    #: into showing two different maskings of the same document.
    number_masked: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Keyed hash, for duplicate detection. Null after purge.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- the scan -----------------------------------------------------------
    #: Path under the private upload directory. Never a URL, never web-served.
    file_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Set when retention emptied this record. The row stays so the ledger's
    #: references to it still resolve; the sensitive parts are gone.
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def is_purged(self) -> bool:
        return self.purged_at is not None

    @property
    def has_file(self) -> bool:
        return bool(self.file_path) and not self.is_purged

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < date.today())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IdProof {self.id} {self.proof_type} user={self.user_id}>"
