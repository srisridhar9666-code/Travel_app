"""
Encryption for identity document numbers.

The SOW asks for ID proofs to be collected (section 5) but says nothing about how
they are held. Aadhaar, PAN and passport numbers sitting in plaintext columns are
the kind of thing that turns a routine database leak into a reportable incident,
so the number is encrypted at rest with Fernet (AES-128-CBC + HMAC) and only
decrypted through an endpoint that writes a VIEW_SENSITIVE audit row.

Two consequences worth stating plainly:

* **Losing PII_ENCRYPTION_KEY loses the numbers.** They are not recoverable from
  a database backup alone. Back the key up separately from the database, or the
  pairing defeats the point.
* The stored ciphertext is not searchable. `number_last4` exists precisely so the
  UI can show and match on a masked value without decrypting anything.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)


class PiiKeyError(RuntimeError):
    """Raised when the configured key is missing or unusable."""


@lru_cache
def _cipher() -> Fernet:
    settings = get_settings()
    raw = (settings.pii_encryption_key or "").strip()

    if not raw:
        if settings.is_production:
            raise PiiKeyError(
                "PII_ENCRYPTION_KEY is not set. Refusing to store identity "
                "documents without it."
            )
        # Development convenience only: a key derived from the app secret so a
        # fresh checkout works, and a loud warning so it never reaches production.
        logger.warning(
            "PII_ENCRYPTION_KEY is not set - deriving a development key from "
            "SECRET_KEY. Set a real key before storing anything that matters."
        )
        derived = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(derived))

    try:
        return Fernet(raw.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise PiiKeyError(
            "PII_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt(value: str) -> str:
    """Encrypt an identity number for storage."""
    return _cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a stored identity number.

    Every caller of this must write a VIEW_SENSITIVE audit row. There is no
    legitimate read of an ID number that should go unrecorded.
    """
    try:
        return _cipher().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise PiiKeyError(
            "Could not decrypt this document number. The encryption key has "
            "changed since it was stored."
        ) from exc


def normalise(number: str) -> str:
    """Strip spaces, dashes and case so the same document stored twice matches."""
    return re.sub(r"[\s\-]", "", number).upper()


def last4(number: str) -> str:
    cleaned = normalise(number)
    return cleaned[-4:] if len(cleaned) >= 4 else cleaned


def mask(number: str) -> str:
    """Render a number for display: last four visible, the rest hidden.

    Grouped in fours because that is how Aadhaar is printed, and a masked value
    nobody can visually match against the physical card is no use to an admin.
    """
    cleaned = normalise(number)
    if len(cleaned) <= 4:
        return cleaned

    # The visible tail is kept as one group. Grouping the whole string in fours
    # would split it - a 10-character PAN renders "XXXX XX23 4F", where the
    # characters an admin is trying to match against the card straddle two
    # groups. Hiding and showing are grouped separately instead.
    hidden = "X" * (len(cleaned) - 4)
    chunks = [hidden[i : i + 4] for i in range(0, len(hidden), 4)]
    chunks.append(cleaned[-4:])
    return " ".join(chunks)


def fingerprint(number: str) -> str:
    """Keyed hash of a number, for duplicate detection without decryption.

    Lets us answer "is this document already on file for someone else" without
    ever decrypting a stored value. Keyed rather than a bare SHA-256, because the
    space of Aadhaar numbers is small enough to brute-force an unkeyed digest.
    """
    settings = get_settings()
    key = (settings.pii_encryption_key or settings.secret_key).encode("utf-8")
    return hashlib.blake2b(normalise(number).encode("utf-8"), key=key[:64], digest_size=32).hexdigest()
