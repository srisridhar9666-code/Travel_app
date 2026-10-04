"""
Identity documents (SOW section 5).

The rules from addendum B8 and C4, enforced here:

* Admin-only. Ground staff cannot read anyone's documents, including their own -
  there is no workflow in this product that needs them to.
* The number is stored encrypted and never appears in a list response. Exactly
  one endpoint decrypts it, and it writes a VIEW_SENSITIVE audit row first.
* Scans are streamed from private storage through an authenticated, audited
  endpoint. Nothing is web-served.
* Records are purged 90 days after the holder's exit date.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select

from app.config import get_settings
from app.core import pii
from app.core.deps import AdminUser, DbSession, SystemAdminUser
from app.core.enums import AuditAction, IdProofType
from app.models.id_proof import IdProof
from app.models.user import User
from app.schemas.id_proof import IdProofRead, IdProofReveal, IdProofUpdate, RetentionStatus
from app.services import audit, retention, storage

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["id proofs"])


def _to_read(proof: IdProof) -> IdProofRead:
    return IdProofRead(
        id=proof.id,
        user_id=proof.user_id,
        proof_type=proof.proof_type,
        label=proof.label,
        # Read straight from storage - no decryption on a list response. The
        # fallback covers rows written before number_masked existed; anything
        # created since always has it.
        masked_number=(
            proof.number_masked
            or (f"XXXX {proof.number_last4}" if proof.number_last4 else None)
        ),
        issued_on=proof.issued_on,
        expires_on=proof.expires_on,
        is_expired=proof.is_expired,
        has_file=proof.has_file,
        file_name=proof.file_name,
        file_size=proof.file_size,
        is_purged=proof.is_purged,
        purged_at=proof.purged_at,
        created_at=proof.created_at,
    )


def _proof_or_404(db: DbSession, proof_id: int, tenant_id: str) -> IdProof:
    proof = db.get(IdProof, proof_id)
    if proof is None or proof.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return proof


def _user_or_404(db: DbSession, user_id: int, tenant_id: str) -> User:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


# ---------------------------------------------------------------------------
# Retention. Declared before /id-proofs/{proof_id} so the literal path wins.
# ---------------------------------------------------------------------------

@router.get("/id-proofs/retention", response_model=RetentionStatus)
def retention_status(actor: AdminUser, db: DbSession) -> RetentionStatus:
    """What the next purge would remove. Shown in the UI so the deletion is
    visible before it happens, not only in the ledger afterwards."""
    return RetentionStatus(
        retention_days=settings.id_proof_retention_days,
        cutoff=retention.cutoff_date().isoformat(),
        due_now=retention.count_due(db, actor.tenant_id),
    )


@router.post("/id-proofs/retention/purge")
def run_retention_purge(actor: SystemAdminUser, db: DbSession) -> dict:
    """Purge everything past its window. Idempotent, and safe to schedule."""
    result = retention.purge_expired(db, actor.tenant_id, actor=actor)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Per-user documents
# ---------------------------------------------------------------------------

@router.get("/users/{user_id}/id-proofs", response_model=list[IdProofRead])
def list_id_proofs(user_id: int, actor: AdminUser, db: DbSession) -> list[IdProofRead]:
    _user_or_404(db, user_id, actor.tenant_id)
    rows = (
        db.execute(
            select(IdProof)
            .where(IdProof.tenant_id == actor.tenant_id, IdProof.user_id == user_id)
            .order_by(IdProof.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_read(proof) for proof in rows]


@router.post(
    "/users/{user_id}/id-proofs",
    response_model=IdProofRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_id_proof(
    user_id: int,
    actor: AdminUser,
    request: Request,
    db: DbSession,
    proof_type: Annotated[IdProofType, Form()],
    number: Annotated[str, Form(min_length=4, max_length=40)],
    label: Annotated[str | None, Form(max_length=80)] = None,
    issued_on: Annotated[date | None, Form()] = None,
    expires_on: Annotated[date | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> IdProofRead:
    """Record a document, optionally with a scan.

    Multipart rather than JSON because the scan usually arrives with the number.
    """
    user = _user_or_404(db, user_id, actor.tenant_id)

    if issued_on and expires_on and expires_on < issued_on:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expiry date cannot be before the issue date.",
        )

    cleaned = number.strip()
    if not pii.normalise(cleaned):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter the document number.",
        )

    # Catch the same document filed against two people, without decrypting.
    marker = pii.fingerprint(cleaned)
    duplicate = db.execute(
        select(IdProof).where(
            IdProof.tenant_id == actor.tenant_id,
            IdProof.fingerprint == marker,
            IdProof.purged_at.is_(None),
        )
    ).scalars().first()
    if duplicate is not None:
        if duplicate.user_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That document is already on file for this person.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That document number is already on file for a different employee.",
        )

    stored_path = stored_name = stored_type = None
    stored_size = None
    if file is not None and file.filename:
        data = await file.read()
        extension = storage.validate(file, data)
        stored_path = storage.save(user_id, file, data, extension)
        # Shown to the admin, never used as a path.
        stored_name = file.filename[:255]
        stored_type = (file.content_type or "").split(";")[0].strip()[:100]
        stored_size = len(data)

    proof = IdProof(
        tenant_id=actor.tenant_id,
        user_id=user_id,
        proof_type=proof_type,
        label=label,
        number_encrypted=pii.encrypt(cleaned),
        number_last4=pii.last4(cleaned),
        number_masked=pii.mask(cleaned),
        fingerprint=marker,
        file_path=stored_path,
        file_name=stored_name,
        file_size=stored_size,
        content_type=stored_type,
        issued_on=issued_on,
        expires_on=expires_on,
        uploaded_by_id=actor.id,
    )
    db.add(proof)
    db.flush()

    audit.record(
        db,
        action=AuditAction.UPLOAD,
        entity_type="id_proof",
        entity_id=proof.id,
        summary=(
            f"{actor.full_name} added a {proof_type} for {user.full_name}"
            + (" with a scan" if stored_path else "")
        ),
        # The masked value only. The ledger must never carry the real number.
        changes={"number": {"from": None, "to": pii.mask(cleaned)}},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(proof)
    return _to_read(proof)


# ---------------------------------------------------------------------------
# Single document
# ---------------------------------------------------------------------------

@router.patch("/id-proofs/{proof_id}", response_model=IdProofRead)
def update_id_proof(
    proof_id: int,
    payload: IdProofUpdate,
    actor: AdminUser,
    request: Request,
    db: DbSession,
) -> IdProofRead:
    """Amend the metadata. The number itself is immutable - a wrong number is a
    different document, so it gets deleted and re-added rather than edited."""
    proof = _proof_or_404(db, proof_id, actor.tenant_id)
    if proof.is_purged:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This record was purged under the retention policy.",
        )

    updates = payload.model_dump(exclude_unset=True)
    before = {key: getattr(proof, key) for key in updates}
    for key, value in updates.items():
        setattr(proof, key, value)
    after = {key: getattr(proof, key) for key in updates}

    changes = audit.diff(before, after)
    if changes:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="id_proof",
            entity_id=proof.id,
            summary=f"{actor.full_name} updated a {proof.proof_type} record",
            changes=changes,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    db.commit()
    db.refresh(proof)
    return _to_read(proof)


@router.get("/id-proofs/{proof_id}/reveal", response_model=IdProofReveal)
def reveal_id_proof(
    proof_id: int, actor: AdminUser, request: Request, db: DbSession
) -> IdProofReveal:
    """The only endpoint that decrypts a document number.

    The audit row is written and committed *before* the plaintext is returned,
    so a read cannot happen without the record of it surviving.
    """
    proof = _proof_or_404(db, proof_id, actor.tenant_id)
    if proof.is_purged or not proof.number_encrypted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This record was purged under the retention policy.",
        )

    owner = db.get(User, proof.user_id)
    audit.record(
        db,
        action=AuditAction.VIEW_SENSITIVE,
        entity_type="id_proof",
        entity_id=proof.id,
        summary=(
            f"{actor.full_name} viewed the full {proof.proof_type} number for "
            f"{owner.full_name if owner else f'user {proof.user_id}'}"
        ),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    try:
        number = pii.decrypt(proof.number_encrypted)
    except pii.PiiKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return IdProofReveal(id=proof.id, proof_type=proof.proof_type, number=number)


@router.get("/id-proofs/{proof_id}/file")
def download_id_proof(
    proof_id: int, actor: AdminUser, request: Request, db: DbSession
) -> Response:
    """Stream a scan from private storage. Audited, like the reveal."""
    proof = _proof_or_404(db, proof_id, actor.tenant_id)
    if not proof.has_file or not proof.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No scan on this record."
        )

    owner = db.get(User, proof.user_id)
    audit.record(
        db,
        action=AuditAction.VIEW_SENSITIVE,
        entity_type="id_proof",
        entity_id=proof.id,
        summary=(
            f"{actor.full_name} downloaded the {proof.proof_type} scan for "
            f"{owner.full_name if owner else f'user {proof.user_id}'}"
        ),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    data = storage.read(proof.file_path)
    return Response(
        content=data,
        media_type=proof.content_type or "application/octet-stream",
        headers={
            # `inline` so the admin can look without a download, and
            # nosniff so a mislabelled file cannot be reinterpreted as HTML.
            "Content-Disposition": f'inline; filename="{proof.file_name or "document"}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/id-proofs/{proof_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_id_proof(
    proof_id: int, actor: AdminUser, request: Request, db: DbSession
) -> Response:
    """Remove a document ahead of its retention window."""
    proof = _proof_or_404(db, proof_id, actor.tenant_id)
    owner = db.get(User, proof.user_id)

    storage.delete(proof.file_path)

    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="id_proof",
        entity_id=proof.id,
        summary=(
            f"{actor.full_name} deleted a {proof.proof_type} for "
            f"{owner.full_name if owner else f'user {proof.user_id}'}"
        ),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.delete(proof)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
