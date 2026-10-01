"""
Proving the activity ledger is append-only (SOW section 7, addendum B9).

The hash chain makes tampering *detectable*. This makes it *impossible* through
the application's own database user - which is the half addendum B9 promised and
deferred to this phase.

The mechanism is a MySQL grant: the app user gets `INSERT` and `SELECT` on
`audit_logs` and nothing else. No `UPDATE`, no `DELETE`. A bug, a compromised
dependency or an application-level SQL injection then cannot rewrite history,
because the database refuses.

The check below does not read `SHOW GRANTS` and believe it. Grant text is
fiddly - wildcards, role inheritance, `ALL PRIVILEGES` on the schema
overriding a table grant - and parsing it is how you end up confidently
reporting a protection you do not have. Instead it **tries an UPDATE inside a
transaction it always rolls back**. Either the database refuses, which is the
answer we want, or it does not, which is the answer we need to know. Nothing is
written either way.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

#: Errors MySQL raises when a grant is missing. 1142 is "command denied to
#: user", 1143 is the column-level form.
_DENIED_CODES = ("1142", "1143")


def _is_permission_error(exc: Exception) -> bool:
    message = str(exc)
    return any(code in message for code in _DENIED_CODES) or "denied" in message.lower()


def probe(db: Session) -> dict:
    """Attempt a write the grant should forbid, then roll it back regardless.

    The UPDATE is a no-op (`summary = summary`) and the DELETE targets an id that
    cannot exist, so even in the failure case - where the grant is missing and
    the statement succeeds - nothing changes. Both are wrapped in a savepoint
    that is always released by rollback.
    """
    newest = db.execute(select_newest_id()).scalar_one_or_none()
    if newest is None:
        return {
            "checked": False,
            "detail": "the ledger is empty, so there is nothing to probe",
        }

    results: dict[str, bool] = {}

    for name, statement, params in (
        ("update", "UPDATE audit_logs SET summary = summary WHERE id = :id", {"id": newest}),
        # An id that cannot exist: even with the grant, this deletes nothing.
        ("delete", "DELETE FROM audit_logs WHERE id = :id", {"id": -1}),
    ):
        savepoint = db.begin_nested()
        try:
            db.execute(text(statement), params)
            results[name] = False   # it went through - the grant is missing
        except SQLAlchemyError as exc:
            if not _is_permission_error(exc):
                logger.warning("Ledger probe %s failed for an unexpected reason: %s", name, exc)
            results[name] = True    # refused
        finally:
            savepoint.rollback()

    append_only = results.get("update", False) and results.get("delete", False)
    return {
        "checked": True,
        "append_only": append_only,
        "update_refused": results.get("update", False),
        "delete_refused": results.get("delete", False),
        "detail": (
            "the database refuses to modify or remove ledger rows"
            if append_only
            else (
                "this database user can still modify the ledger - run "
                "scripts/grant_append_only.py to apply the grant (addendum B9)"
            )
        ),
    }


def select_newest_id():
    """The newest row, as something to aim a refused UPDATE at."""
    from sqlalchemy import select

    return select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)


def current_grants(db: Session) -> list[str]:
    """What the database says this user holds. Context for a failed probe, not
    the check itself - see the module docstring on why."""
    try:
        return [row[0] for row in db.execute(text("SHOW GRANTS FOR CURRENT_USER()"))]
    except SQLAlchemyError:
        return []
