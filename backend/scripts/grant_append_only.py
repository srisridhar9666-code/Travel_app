"""
Make the activity ledger append-only at the database (addendum B9).

The hash chain makes tampering detectable. This makes it impossible through the
application's own user, which is the other half of section 7's "immutable"
claim. It is a deployment step, not application code, because the application
must not be able to grant itself back what this takes away.

    ./.venv/Scripts/python.exe scripts/grant_append_only.py --show
    ./.venv/Scripts/python.exe scripts/grant_append_only.py --apply

`--show` prints the SQL and changes nothing. `--apply` needs a connection with
`GRANT OPTION` - normally root, which is why it takes its own credentials rather
than reusing the application's.

What it does, and why in this order:

1. Create a dedicated application user if one does not exist. Running the app as
   root is what makes this whole exercise moot.
2. Give it full rights on every table *except* `audit_logs`.
3. Give it `INSERT` and `SELECT` on `audit_logs`, and nothing else.

Step 2 is per-table on purpose. A schema-wide `ALL PRIVILEGES ON travel_ops.*`
would silently outrank the narrow grant in step 3, and `SHOW GRANTS` would still
look plausible - the failure this script exists to prevent. The application's own
`GET /audit/grants` probes an actual UPDATE rather than trusting any of this.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

LEDGER = "audit_logs"


def build_statements(*, database: str, user: str, host: str, password: str, tables: list[str]):
    """The exact SQL, in the order it must run."""
    account = f"'{user}'@'{host}'"
    statements = [
        (
            "create the application user if it is missing",
            f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY '{password}'",
        ),
        (
            "start from nothing, so re-running cannot leave an older grant behind",
            f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {account}",
        ),
    ]

    for table in tables:
        if table == LEDGER:
            continue
        statements.append(
            (
                f"full rights on {table}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{database}`.`{table}` TO {account}",
            )
        )

    statements += [
        (
            f"append-only on {LEDGER} - no UPDATE, no DELETE",
            f"GRANT SELECT, INSERT ON `{database}`.`{LEDGER}` TO {account}",
        ),
        ("apply", "FLUSH PRIVILEGES"),
    ]
    return statements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the statements")
    parser.add_argument("--show", action="store_true", help="print the SQL and exit")
    parser.add_argument("--user", default="travel_ops_app", help="application DB user to create")
    parser.add_argument("--host", default="%", help="host pattern for that user")
    parser.add_argument("--password", default=None, help="password for the application user")
    parser.add_argument(
        "--admin-url",
        default=None,
        help="SQLAlchemy URL with GRANT OPTION (defaults to DATABASE_URL)",
    )
    args = parser.parse_args()

    if not (args.apply or args.show):
        parser.error("choose --show or --apply")

    settings = get_settings()
    admin_url = args.admin_url or settings.database_url
    engine = create_engine(admin_url)
    database = engine.url.database

    with engine.connect() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
                ),
                {"schema": database},
            )
        ]

    if LEDGER not in tables:
        print(f"! {LEDGER} does not exist in {database} - run the migrations first.")
        return 1

    password = args.password or "CHANGE-ME-BEFORE-APPLYING"
    statements = build_statements(
        database=database, user=args.user, host=args.host,
        password=password, tables=tables,
    )

    if args.show:
        print(f"-- {len(tables)} tables in {database}; {LEDGER} is append-only\n")
        for why, sql in statements:
            print(f"-- {why}\n{sql};\n")
        print(
            "-- Then point the application at the new user:\n"
            f"-- DATABASE_URL=mysql+pymysql://{args.user}:<password>@<host>/{database}?charset=utf8mb4"
        )
        return 0

    if args.password is None:
        print("! --apply needs --password for the application user.")
        return 1

    with engine.begin() as conn:
        for why, sql in statements:
            print(f"  {why}")
            conn.execute(text(sql))

    print(
        f"\nDone. {args.user} has INSERT and SELECT on {LEDGER}, and nothing else on it.\n"
        "Point DATABASE_URL at that user, restart, and confirm with GET /audit/grants."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
