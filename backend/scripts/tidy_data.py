"""
Look over the data in your database and, if you ask, tidy it.

    cd backend
    .venv\\Scripts\\python.exe scripts\\tidy_data.py                      (report only)
    .venv\\Scripts\\python.exe scripts\\tidy_data.py --retire-test-accounts
    .venv\\Scripts\\python.exe scripts\\tidy_data.py --fresh-start

It uses the same backend/.env as the API, so it looks at the database the app
uses. Run `alembic upgrade head` first: the migrations already convert old data
to the current rules (roles, phone numbers, cab fields), and this script refuses
to run against a database that is behind.

With no options it changes nothing. It lists:

* people whose details the app now refuses - a phone that is not a 10-digit
  mobile, a number two people share, gender not set, the designation Manager
  without Manager access - for an admin to correct on Team;
* accounts the old test scripts created (field.1a2b3c4d@..., p5.ravi.1a2b3c@...);
* drafts nobody finished, and trips that were never decided before their date.

Options, each asking you to type DELETE before doing anything:

--retire-test-accounts  marks the test-script accounts Deleted, so they leave
                        every list. Their history stays; restore one from Team
                        (Status: Deleted) if a real person was caught.
--fresh-start           removes every trip and everything hanging off it -
                        travellers, edits, tickets and their files,
                        notifications, invoices, team-change requests - and
                        keeps people, departments, campaigns, vendors and places.
--include-activity-log  with --fresh-start, empties the activity log too, so
                        it starts a new chain. Refused if the database has been
                        made append-only (see scripts/grant_append_only.py).
--yes                   skip the typed confirmation (for scripted use).

Never runs when ENVIRONMENT is production.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core import clock  # noqa: E402
from app.core.enums import (  # noqa: E402
    SELECTABLE_GENDERS,
    Designation,
    Role,
    TravellerStatus,
    UserStatus,
)
from app.database import SessionLocal, engine  # noqa: E402
from app.models.audit import AuditLog  # noqa: E402
from app.models.base import naive_utcnow  # noqa: E402
from app.models.invoice import Invoice, InvoiceLine  # noqa: E402
from app.models.request import (  # noqa: E402
    Notification,
    RequestRevision,
    RequestTraveller,
    TravelRequest,
)
from app.models.team_change import TeamChange  # noqa: E402
from app.models.ticket import TicketDocument  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.user import tidy_mobile  # noqa: E402
from app.services import storage  # noqa: E402

#: The addresses the old smoke scripts and the end-to-end script made up:
#: field.1a2b3c4d@, lock.1a2b3c4d@, import.a.1a2b3c@, p5.ravi.1a2b3c@,
#: ravi.1a2b3c@, once.1a2b3c@, nobody.1a2b3c@ - always ending in a run of hex
#: with at least one digit, which real addresses practically never do.
TEST_ADDRESS = re.compile(
    r"^(?:(?:field|lock)\.[0-9a-f]{8}"
    r"|import\.[a-c]\.[0-9a-f]{6,8}"
    r"|(?:p[3-8]\.)?[a-z]+\.(?=[0-9a-f]*\d)[0-9a-f]{6})@designboxed\.com$"
)


def heading(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def database_is_current() -> bool:
    """The database is at the migrations' head - this script reads current columns."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
    )
    heads = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        current = {row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version"))}
    return current == heads


def report(db) -> list[User]:
    people = db.execute(select(User).order_by(User.full_name)).scalars().all()
    live = [p for p in people if p.status is not UserStatus.DELETED]

    heading("People whose details need correcting on Team")
    problems = 0
    numbers = Counter(p.phone for p in live if p.phone)
    for person in live:
        notes = []
        if person.phone:
            try:
                tidy_mobile(person.phone)
            except ValueError:
                notes.append(f"phone {person.phone!r} is not a 10-digit mobile")
            if numbers[person.phone] > 1:
                notes.append(f"phone {person.phone} is shared with someone else")
        if person.gender not in SELECTABLE_GENDERS:
            notes.append("gender not set")
        if person.designation is Designation.MANAGER and person.role is not Role.MANAGER:
            notes.append("designation Manager, but app access is not Manager")
        if notes:
            problems += 1
            print(f"  {person.full_name} <{person.email}>: {'; '.join(notes)}")
    if not problems:
        print("  Nothing to correct.")

    heading("Accounts the old test scripts created")
    test_accounts = [p for p in live if TEST_ADDRESS.match(p.email or "")]
    for person in test_accounts:
        print(f"  {person.full_name} <{person.email}> - {person.role}, {person.status}")
    if not test_accounts:
        print("  None.")

    heading("Trips")
    total = db.execute(select(func.count(TravelRequest.id))).scalar_one()
    stale_drafts = db.execute(
        select(func.count(TravelRequest.id)).where(
            TravelRequest.is_draft.is_(True),
            TravelRequest.created_at < naive_utcnow() - timedelta(days=30),
        )
    ).scalar_one()
    today = clock.local_today()
    undecided_past = 0
    for row in db.execute(
        select(TravelRequest).where(
            TravelRequest.is_draft.is_(False), TravelRequest.is_cancelled.is_(False)
        )
    ).scalars():
        day = row.check_in if row.check_in else (row.start_at.date() if row.start_at else None)
        if day and day < today and any(t.status is TravellerStatus.PENDING for t in row.travellers):
            undecided_past += 1
    print(f"  {total} trip request(s) in all.")
    print(f"  {stale_drafts} draft(s) older than 30 days that nobody submitted.")
    print(f"  {undecided_past} trip(s) whose date passed while still waiting for a decision.")
    return test_accounts


def confirm(what: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    print(f"\nThis will {what}.")
    return input("Type DELETE to go ahead, anything else to stop: ").strip() == "DELETE"


def retire(db, accounts: list[User]) -> None:
    for person in accounts:
        person.status = UserStatus.DELETED
        person.status_changed_at = naive_utcnow()
    db.commit()
    print(f"Marked {len(accounts)} test account(s) Deleted. Restore one from Team if needed.")


def fresh_start(db, include_log: bool) -> None:
    files = [t.file_path for t in db.execute(select(TicketDocument)).scalars() if t.file_path]
    counts = {}
    # Children first: every one of these points at a trip or a traveller on it.
    for label, model in (
        ("invoice lines", InvoiceLine),
        ("invoices", Invoice),
        ("tickets", TicketDocument),
        ("notifications", Notification),
        ("request edits", RequestRevision),
        ("travellers", RequestTraveller),
        ("trip requests", TravelRequest),
        ("team-change requests", TeamChange),
    ):
        counts[label] = db.execute(delete(model)).rowcount
    if include_log:
        counts["activity log entries"] = db.execute(delete(AuditLog)).rowcount
    db.commit()
    removed_files = sum(1 for path in files if storage.delete(path))
    for label, count in counts.items():
        print(f"  removed {count} {label}")
    print(f"  removed {removed_files} uploaded ticket file(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retire-test-accounts", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--include-activity-log", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        print("Refusing to run: ENVIRONMENT is production.")
        return 2
    if args.include_activity_log and not args.fresh_start:
        print("--include-activity-log only goes with --fresh-start.")
        return 2
    if not database_is_current():
        print("The database is behind the code. Run `alembic upgrade head` first, then try again.")
        return 2

    db = SessionLocal()
    try:
        test_accounts = report(db)
        if args.retire_test_accounts and test_accounts:
            if confirm(f"mark {len(test_accounts)} test account(s) Deleted", args.yes):
                retire(db, test_accounts)
            else:
                print("Stopped - nothing changed.")
        if args.fresh_start:
            what = "delete every trip, ticket, notification, invoice and team-change request"
            if args.include_activity_log:
                what += ", and empty the activity log"
            if confirm(what + " (people, departments, campaigns, vendors and places stay)", args.yes):
                fresh_start(db, args.include_activity_log)
            else:
                print("Stopped - nothing changed.")
        if not (args.retire_test_accounts or args.fresh_start):
            print("\nReport only - nothing was changed. See --help for the tidy-up options.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
