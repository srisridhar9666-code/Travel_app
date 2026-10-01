"""
Private file storage for identity document scans and ticket documents.

Nothing here is ever web-served. Files live under the upload directory, outside
any static mount, and reach a browser only through an authenticated endpoint that
writes a VIEW_SENSITIVE audit row first.

The filename a browser sends is attacker-controlled - `../../.env` is a valid
string - so it is used only to pick an extension and to show the admin what they
uploaded. The path on disk is always a server-generated UUID.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

#: Scans and photographs of documents. Deliberately narrow: no archives, no
#: Office formats, nothing that executes.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}

#: Leading bytes that must match the declared type. A browser's Content-Type is a
#: claim, not evidence, so we check the file actually is what it says.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF-",),
}


# ---------------------------------------------------------------------------
# Backends
#
# Everything above and below this block works in relative paths like
# "id_proofs/7/a1b2.png". Only these three functions know whether that resolves
# to a file on disk or an object in a bucket, so moving to Cloud Run - where the
# container filesystem is per-instance and dies with the revision - is a config
# change rather than a rewrite. The stored paths stay valid across the move.
# ---------------------------------------------------------------------------

def _bucket():
    """Lazily resolve the GCS bucket. Imported here so a local deployment never
    pays for the dependency."""
    try:
        from google.cloud import storage as gcs
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "STORAGE_BACKEND is 'gcs' but google-cloud-storage is not installed."
        ) from exc

    if not settings.storage_bucket:
        raise RuntimeError("STORAGE_BACKEND is 'gcs' but STORAGE_BUCKET is empty.")

    return gcs.Client(project=settings.gemini_project_id or None).bucket(
        settings.storage_bucket
    )


def _write(relative_path: str, data: bytes, content_type: str | None = None) -> None:
    if settings.uses_object_storage:
        _bucket().blob(relative_path).upload_from_string(
            data, content_type=content_type or "application/octet-stream"
        )
        return

    target = resolve(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _read(relative_path: str) -> bytes:
    if settings.uses_object_storage:
        blob = _bucket().blob(relative_path)
        if not blob.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That file is no longer in storage.",
            )
        return blob.download_as_bytes()

    path = resolve(relative_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That file is no longer on disk.",
        )
    return path.read_bytes()


def _remove(relative_path: str) -> bool:
    if settings.uses_object_storage:
        # Missing is success here: retention and delete both want the object
        # gone, and an object that was never written satisfies that.
        _bucket().blob(relative_path).delete(if_generation_match=None)
        return True

    path = resolve(relative_path)
    path.unlink(missing_ok=True)
    # Tidy the per-user directory once its last file goes.
    parent = path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    return True


def validate(upload: UploadFile, data: bytes) -> str:
    """Check size, declared type and leading bytes. Returns the extension."""
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That file is larger than the {settings.max_upload_mb} MB limit.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="That file is empty."
        )

    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload a JPEG, PNG, WebP, HEIC or PDF.",
        )

    signatures = _MAGIC.get(content_type)
    if signatures and not any(data.startswith(sig) for sig in signatures):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file's contents do not match its type.",
        )

    return extension


def save(user_id: int, upload: UploadFile, data: bytes, extension: str) -> str:
    """Write the file and return its path, relative to the upload directory.

    Relative, so the stored value survives the application moving between a
    laptop, a container and a mounted volume.
    """
    name = f"{uuid.uuid4().hex}{extension}"
    relative = str(Path("id_proofs") / str(user_id) / name).replace("\\", "/")
    _write(relative, data, upload.content_type)
    return relative


def save_in(category: str, key: int, data: bytes, extension: str) -> str:
    """Write a file under `<category>/<key>/` and return its relative path.

    The category is caller-supplied and never user-supplied - a request id or a
    user id picks the subdirectory, and the filename is always a server-generated
    UUID, so nothing an uploader controls reaches the filesystem.
    """
    name = f"{uuid.uuid4().hex}{extension}"
    relative = str(Path(category) / str(key) / name).replace("\\", "/")
    _write(relative, data)
    return relative


def resolve(relative_path: str) -> Path:
    """Turn a stored path back into an absolute one, refusing to escape.

    Even though every stored path is server-generated, a row could be tampered
    with in the database. Containment is re-checked on the way out, not assumed.
    """
    root = settings.upload_path.resolve()
    candidate = (root / relative_path).resolve()

    if not candidate.is_relative_to(root):
        logger.error("Blocked path traversal attempt: %r", relative_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found."
        )
    return candidate


def read(relative_path: str) -> bytes:
    return _read(relative_path)


def delete(relative_path: str | None) -> bool:
    """Remove a stored file. Missing is success - the goal is that it is gone."""
    if not relative_path:
        return False
    try:
        return _remove(relative_path)
    except HTTPException:
        return False
    except OSError:
        logger.exception("Could not delete %s", relative_path)
        return False
    except Exception:
        # A bucket that is unreachable must not take down a retention run or a
        # user-initiated delete. The row still gets emptied; the object is
        # reported here and can be swept separately.
        logger.exception("Could not delete %s from object storage", relative_path)
        return False
