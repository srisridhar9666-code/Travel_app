"""
Writing and verifying the activity ledger.

This is the only module allowed to write `audit_logs`, and it never updates or
deletes. Every row is chained to the one before it, so the log can be *checked*
rather than merely trusted - see `verify_chain`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditAction
from app.models.audit import GENESIS_HASH, AuditLog
from app.models.base import naive_utcnow
from app.models.user import User

logger = logging.getLogger(__name__)


def jsonable(value: Any) -> Any:
    """Coerce a value into something the JSON `changes` column can hold.

    Diffs carry whatever the ORM had: dates, enums, Decimals. MySQL's JSON type
    serialises with the stdlib encoder, which raises on all three - and because
    the audit row shares a transaction with the change it describes, that
    exception rolls back the edit itself. A user's update would vanish with a
    500 and no record of why. So everything is flattened on the way in.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def _canonical(payload: dict[str, Any]) -> str:
    """Stable JSON. Sorted keys and no incidental whitespace, so the same logical
    row always hashes identically regardless of dict ordering."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(*, prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{prev_hash}{_canonical(payload)}".encode("utf-8")).hexdigest()


def _hash_payload(entry: AuditLog) -> dict[str, Any]:
    """The subset of a row that the chain commits to.

    Deliberately excludes `id`, which the database assigns after the hash is
    computed, and includes everything a tamperer would want to change.
    """
    return {
        "tenant_id": entry.tenant_id,
        "actor_user_id": entry.actor_user_id,
        "actor_email": entry.actor_email,
        "actor_role": entry.actor_role,
        "action": str(entry.action),
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "summary": entry.summary,
        "changes": entry.changes,
        "reason": entry.reason,
        "ip_address": entry.ip_address,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _client_context(request: Request | None) -> tuple[str | None, str | None]:
    """Best-effort caller identity. Behind a proxy, X-Forwarded-For's first hop
    is the client; trust it only because this runs behind our own ingress."""
    if request is None:
        return None, None

    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )
    agent = request.headers.get("user-agent")
    return (ip[:45] if ip else None), (agent[:255] if agent else None)


def record(
    db: Session,
    *,
    action: AuditAction,
    entity_type: str,
    summary: str,
    tenant_id: str,
    actor: User | None = None,
    entity_id: int | None = None,
    changes: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None = None,
    flush: bool = True,
) -> AuditLog:
    """Append one row to the ledger.

    The caller's transaction owns the commit, so an audit row and the change it
    describes land together or not at all. A write that cannot be audited is a
    write that does not happen.
    """
    ip, agent = _client_context(request)

    # Lock the tail of this tenant's chain so two concurrent writers cannot
    # both claim the same predecessor.
    prev_hash = db.execute(
        select(AuditLog.row_hash)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.id.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none() or GENESIS_HASH

    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        actor_name=actor.full_name if actor else None,
        actor_role=str(actor.role) if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:500],
        changes=jsonable(changes) if changes else None,
        reason=reason,
        ip_address=ip,
        user_agent=agent,
        created_at=naive_utcnow(),
        prev_hash=prev_hash,
    )
    entry.row_hash = compute_row_hash(prev_hash=prev_hash, payload=_hash_payload(entry))

    db.add(entry)
    if flush:
        db.flush()
    return entry


def diff(before: dict[str, Any], after: dict[str, Any], *, redact: set[str] | None = None) -> dict:
    """Field-level change set, in the shape the ledger stores.

    Only changed keys appear. Anything named in `redact` records that it changed
    without recording what it changed to - used for password hashes and PII.
    """
    redact = redact or set()
    out: dict[str, dict[str, Any]] = {}
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        out[key] = (
            {"from": "***", "to": "***"}
            if key in redact
            else {"from": jsonable(old), "to": jsonable(new)}
        )
    return out


def verify_chain(db: Session, tenant_id: str, *, limit: int | None = None) -> dict[str, Any]:
    """Walk the ledger and confirm every link still holds.

    Returns the first break rather than a list, because after one broken link
    every subsequent hash is meaningless anyway.
    """
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.id)
    if limit:
        stmt = stmt.limit(limit)

    expected_prev = GENESIS_HASH
    checked = 0

    for entry in db.execute(stmt).scalars():
        if entry.prev_hash != expected_prev:
            return {
                "ok": False,
                "checked": checked,
                "broken_at_id": entry.id,
                "detail": "predecessor hash does not match - a row was removed or reordered",
            }
        recomputed = compute_row_hash(prev_hash=entry.prev_hash, payload=_hash_payload(entry))
        if recomputed != entry.row_hash:
            return {
                "ok": False,
                "checked": checked,
                "broken_at_id": entry.id,
                "detail": "row hash does not match its contents - this row was edited",
            }
        expected_prev = entry.row_hash
        checked += 1

    return {"ok": True, "checked": checked, "broken_at_id": None, "detail": "chain intact"}
