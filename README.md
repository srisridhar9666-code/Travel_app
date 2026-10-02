# Field Logistics & Travel Management System

Travel, cab and accommodation requests for ~100 ground staff, fulfilled by a ~10 person
admin team. React + FastAPI + MySQL, with Gemini for ticket extraction.

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript, Vite 7, Tailwind 3, TanStack Query, Zustand |
| Backend | FastAPI, SQLAlchemy 2, Alembic, PyJWT, bcrypt |
| Database | MySQL 8 (`travel_ops`, utf8mb4) |
| AI | `gemini-3.1-pro-preview` via Vertex AI, service account `db-data-team` |

Scope lives in `Scope of Work_ Field Logistics & Travel Management System.pdf`.
**Read [`docs/SOW-ADDENDUM.md`](docs/SOW-ADDENDUM.md) alongside it** — it records the gaps
found in the SOW, the decisions taken, and what is still open. Where the two disagree, the
addendum wins.

For deploying and running it, see **[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)** — the
pre-flight checklist, backups, health endpoints and the path to Cloud Run.

> **Before any production deploy:** back up `PII_ENCRYPTION_KEY` somewhere other than
> alongside the database. Identity numbers are encrypted with it, and a backup holding both
> is a backup of neither. Lose the key and every stored Aadhaar, PAN and passport number is
> unrecoverable.

---

## Running it

MySQL must be running on `127.0.0.1:3306` before either server starts.

### In VS Code

Open the `V1` folder itself as the workspace — not its parent, or the paths in
`.vscode/` will not resolve. Then:

**Ctrl+Shift+B** starts the API and the web app together.

Everything else is under **Ctrl+Shift+P → Tasks: Run Task**:

| Task | What it does |
|---|---|
| Run everything | Both servers, in two dedicated terminals |
| API: serve | Just the backend, on :8000 |
| Web: dev server | Just the frontend, on :5173 |
| DB: apply migrations | `alembic upgrade head` |
| DB: new migration | Autogenerate from the models, prompts for a message |
| Test: backend unit tests | `pytest -q` |
| Test: smoke suites | Pick a phase; the API must already be running |
| Web: typecheck / production build | |
| Free port 8000 | Kills a wedged uvicorn worker still holding the port |

To debug rather than just run, use the **Run and Debug** panel (Ctrl+Shift+D):
*API (debug)* for breakpoints in FastAPI, *API tests (debug)* to step through a failing
test, or *Full stack (debug)* for both the API and a Chrome instance attached to the
frontend.

The first time you open the workspace, VS Code will offer the extensions in
`.vscode/extensions.json` — the Python and Tailwind ones are the two that matter.

### In containers, the shape that deploys

```bash
docker compose up --build
```

Then http://localhost:8080. Production build, served by nginx, API proxied at `/api`.
MySQL is not in the compose file — it uses the one already on your host. No hot reload,
so this is for checking the deployable shape, not for day-to-day work.

### From a terminal

Two terminals, from the repository root.

> **Windows PowerShell 5.1 has no `&&`** — it is a parser error, not a warning. Each
> command below is therefore two lines. In Git Bash or PowerShell 7 you can join them with
> `&&` as usual.

**Terminal 1 — the API:**

```powershell
cd "D:\Travel Management System\V1\backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

> `--reload` is deliberately omitted: on Windows the watcher has been seen to wedge, leaving
> an orphaned worker holding port 8000 under a parent PID that no longer exists. Restart the
> API by hand after backend edits. To free a stuck port:
>
> ```powershell
> Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { taskkill /PID $_.OwningProcess /F /T }
> ```

**Terminal 2 — the web app:**

```powershell
cd "D:\Travel Management System\V1\web"
npm run dev
```

Leave both running. Stop either with Ctrl+C.

Then open http://localhost:5173. Vite proxies `/api` to the backend, so the browser only
ever sees one origin — CORS and cookies behave the same in dev as in production.

API docs are at http://127.0.0.1:8000/docs.

### First-time setup

```powershell
cd "D:\Travel Management System\V1\backend"
uv sync
```

> `uv sync` is the one to use — it installs exactly what `uv.lock` pins, including the dev
> tools. `requirements.txt` and `requirements-dev.txt` also exist for anything that expects
> the conventional file (`pip install -r requirements.txt`), but they are **generated** from
> the lockfile, not edited by hand:
>
> ```powershell
> uv export --format requirements-txt --no-dev --no-hashes --no-emit-project -o requirements.txt
> ```
>
> CI regenerates and diffs them, so editing one by hand fails the build rather than drifting
> quietly.

```powershell
cd "D:\Travel Management System\V1\web"
npm install
```

The database is created by hand once:

```sql
CREATE DATABASE travel_ops CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

Schema comes from Alembic, never from `create_all`:

```powershell
cd "D:\Travel Management System\V1\backend"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

---

## Health checks

| Endpoint | Proves |
|---|---|
| `GET /health` | API is up and MySQL answers a real query |
| `GET /health/gemini` | The service account actually reaches `gemini-3.1-pro-preview` |
| `GET /audit/verify` | Walks the ledger's hash chain and reports the first break |
| `GET /health/email` | Authenticates against SMTP without sending anything |
| `GET /audit/grants` | Tries a write the ledger grant should forbid, and rolls it back |

Each one runs against the live stack, so a green answer means that layer is genuinely wired
rather than stubbed. The email one is also on screen for admins: **Notifications → Delivery
ledger → Email delivery**.

---

## Tests

```powershell
cd "D:\Travel Management System\V1\backend"
.\.venv\Scripts\python.exe -m pytest -q
```

**Tests run against real MySQL**, in a scratch database called `travel_ops_test` that the
suite creates on first run — the name is derived from `DATABASE_URL` by appending `_test`,
and `conftest.py` refuses to run against anything not ending that way. Each test runs
inside a transaction that is rolled back, so the schema is built once and nothing leaks
between tests.

They used to run on in-memory SQLite, which was quicker to set up and wrong: the worst
defect this project has had was MySQL's `DATETIME` silently truncating the microseconds the
audit hash chain commits to, and no SQLite test could ever have found it.

The smoke suites go over real HTTP against a running server, and create throwaway accounts —
so start the API first and point them at a dev database:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_phase1.py
```

There is one per phase, `smoke_phase1.py` through `smoke_phase8.py`, plus
`scripts\test_end_to_end.py` which walks a request from raised to booked.

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase3.py
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase4.py
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase5.py
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase6.py
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase7.py
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/smoke_phase8.py
```

### Linting

```bash
cd backend && ./.venv/Scripts/python.exe -m ruff check app scripts tests
```

Narrow on purpose - `E9`, `F`, `B` only. It is a guard against mistakes that
reach production silently, not a style argument. It was added after exactly one
of those: a handler referenced `costs` without importing it, all 375 tests
passed, and the endpoint 500'd the first time anyone used that field. `F821`
finds that in milliseconds.

### The whole system, end to end

The eight phase smokes each prove their own slice against whatever the dev
database already holds. This one proves what none of them can - that the product
**deploys from nothing**, that one continuous journey crosses every phase
boundary, that the screens agree with each other, and that the permission matrix
holds for every role against every guarded endpoint.

```sql
DROP DATABASE IF EXISTS travel_ops_e2e;
CREATE DATABASE travel_ops_e2e CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

```bash
cd backend && DATABASE_URL='mysql+pymysql://root:PASSWORD@127.0.0.1:3306/travel_ops_e2e?charset=utf8mb4' ./.venv/Scripts/python.exe -m alembic upgrade head
```

```bash
cd backend && DATABASE_URL='mysql+pymysql://root:PASSWORD@127.0.0.1:3306/travel_ops_e2e?charset=utf8mb4' ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001
```

```bash
cd backend && ./.venv/Scripts/python.exe scripts/test_end_to_end.py
```

Roughly 130 checks following one field team from onboarding to a cost report. It
found two real defects the phase tests could not, because each of those sets up
its own fixtures and this one makes every step consume what the last produced.

Phase 5's smoke calls the real extraction model, so it costs a few seconds and a
few tokens per run. It reads the sample tickets in `backend/scripts/fixtures/`,
regenerated with `scripts/make_ticket_fixture.py`.

---

## Signing in

On first run the API creates one system administrator from `ADMIN_EMAIL` / `ADMIN_PASSWORD`
in `.env` and logs a warning. **Change that password immediately** — it is sitting in a
config file. The bootstrap is idempotent and never touches an existing account, so
restarting cannot silently reset it.

Everyone else is created from **Team**, which issues a single-use invite link. The link is
emailed when email is turned on (below), and the dialog always shows it too, along with why
it was not emailed if it was not. Links are valid for 72 hours and can be used once.

### Turning email on

Email is off until `backend/.env` says otherwise. It must be `backend/.env`: the API reads
that file and only that file, and the `.env` beside `docker-compose.yml` holds database
credentials for compose and nothing else.

```dotenv
EMAIL_ENABLED=true
SMTP_PORT=587                           # 587 (STARTTLS); use 465 (SSL) if 587 is blocked
SMTP_USERNAME="you@gmail.com"
SMTP_APP_PASSWORD="abcdefghijklmnop"   # a Gmail App Password, not your account password
EMAIL_FROM="you@gmail.com"
EMAIL_ALLOWLIST=""                      # empty, or only those addresses receive mail
```

Restart the API afterwards; settings are read once at start-up. Then, as an admin, open
**Notifications → Delivery ledger → Email delivery** and press **Send test email**. It shows
the settings the running API is actually using (the password only as "set (16 characters)"),
warns about a restart that is still needed, a misspelt key or an allowlist that holds mail
back, and sends one real message, reporting where it stopped and the mail server's own words
if it fails. The start-up log says the same in one line. A Gmail App Password needs 2-Step
Verification on the account: Google Account, Security, App passwords.

`backend/.env` saved by Windows Notepad as UTF-16 or with a byte-order mark is read either
way.

Admins are emailed when someone raises a request ("New requests to approve" in their
notification settings turns that off). The email goes out after the response, so a slow
mail server never slows down Submit; one stranded by a restart is sent by the retry job.

---

## Layout

```
V1/
├── backend/
│   ├── app/
│   │   ├── config.py          # settings, resolved relative to backend/ not CWD
│   │   ├── database.py        # engine, session factory, declarative Base
│   │   ├── main.py            # app factory, CORS, health probes
│   │   ├── core/enums.py      # domain vocabulary + status derivation
│   │   ├── models/            # SQLAlchemy models
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── routers/           # HTTP endpoints
│   │   └── services/          # gemini, email, storage, audit
│   ├── alembic/               # migrations
│   └── .env                   # secrets, git-ignored
├── web/
│   ├── public/brand/          # generated logo set — see docs/SOW-ADDENDUM.md §E
│   └── src/
│       ├── index.css          # design tokens, light + dark
│       ├── store/theme.ts     # light / dark / system
│       ├── components/        # Logo, ThemeToggle, shared UI
│       ├── lib/               # api client, utils
│       └── pages/
├── docs/SOW-ADDENDUM.md
└── gemini_credentials.json    # service account, git-ignored
```

---

## Things worth knowing before you change them

**Status lives on the traveller, not the request.** Selective approval means the decision
unit is one person on one request. `derive_request_status()` in `app/core/enums.py`
collapses traveller statuses into the value shown on a request; nothing sets that value
directly.

**A request locks the moment an admin acts.** `is_editable()` returns false once any
traveller leaves `PENDING`. Every edit before that writes a revision row with a field-level
diff.

**Brand red is not the UI's red.** `#FE0024` scores 4.0:1 on white, under the AA bar for
body text, and this product's main verbs are Approve and Reject. So red belongs to
destructive actions, the interactive primary is ink, and brand red is reserved for the logo
and active indicators. Brand-coloured *text* uses `--brand-strong`, which is AA-safe in
both themes.

**Light tokens are declared under both `:root` and `[data-theme='light']`** so a nested
light island really does go light while the rest of the app stays dark.

**Gemini never auto-books.** Extraction populates a review screen; an admin confirms before
anything reaches a traveller's inbox. See addendum §B3.

**The audit ledger is append-only and hash-chained.** `app/services/audit.py` is the only
module that writes it, and it never updates or deletes. Each row commits to the hash of the
row before it, so an edit, deletion or reordering is detectable — `verify_chain` reports the
first break. In production the app's DB user gets `INSERT` and `SELECT` only; that grant is
the real enforcement and lands in Phase 8.

**Every datetime column is `DATETIME(6)`.** Plain MySQL `DATETIME` silently drops the
microseconds Python wrote, which breaks the audit chain outright: the hash is computed
before the insert, so a truncated read-back never rehashes to the same value. Use
`UTCDateTime` from `app/models/base.py`, never bare `DateTime`.

**Passwords are SHA-256'd before bcrypt.** Plain bcrypt stops at 72 bytes, so two
passphrases sharing a 72-byte prefix would open the same account. The pre-hash folds any
length into 44 bytes. Hashes here are therefore not interchangeable with plain-bcrypt ones.

**Login never reveals whether an account exists.** Unknown address, wrong password and
forgot-password all answer identically, and the unknown-address path still burns a bcrypt
round so timing does not leak it either.

**Anything written into an audit `changes` dict must survive JSON serialisation.**
`audit.jsonable()` handles this and is already applied inside `diff()` and `record()`. It
exists because a raw `date` in a diff raised at INSERT time, and since the audit row shares
a transaction with the change it describes, that rolled back the user's edit. The update
vanished with a 500 and no record of why.

**Identity numbers are encrypted at rest** (`app/core/pii.py`, Fernet). Only the mask and
last four are readable without decrypting. Exactly one endpoint decrypts, and it writes its
`VIEW_SENSITIVE` audit row *before* returning the plaintext. **Losing `PII_ENCRYPTION_KEY`
loses the numbers** — back it up separately from the database.

**ID proof retention is 90 days from `User.exited_on`, not from deactivation.** Suspending
someone for a fortnight must never start a deletion clock. A purge empties the record but
keeps the row, so the ledger's references to it still resolve.

**Projects are archived, never deleted.** A deleted campaign would orphan its requests and
erase the history that section 6's reporting depends on.

**Conflicts warn; they never block.** `app/services/conflicts.py` is a pure rule set over an
`Itinerary` dataclass, so the rules are testable without a database. Hotels overlap by
*night* (`[check_in, check_out)`, so checking out and into another hotel on the same day is
fine); journeys overlap by *closed datetime window*; hotels and journeys never conflict with
each other; and **a local cab never conflicts with that person's own long-distance leg** —
the airport run is the most common thing ground staff raise, and warning on it would train
admins to ignore the warning. Nothing in that module can stop a submission.

**A shared room is only ever a request until an admin confirms it.** Room sharing needs an
*exact* `Gender` match between two people who stated a binary value; `OTHER` and
`UNDISCLOSED` never share, even with an identical value, because identical labels are not
consent. The requester is never told that a candidate was filtered out. See addendum C2 for
the open question this is built against.

**Moving a stay drops its room share, including an admin's confirmation.** A confirmation
was given against particular dates in a particular city; carrying it silently onto different
ones would put two people in a room neither agreed to.

**Request times are wall-clock, not UTC.** Staff type "09:30" meaning 09:30 where they are,
everyone is in one country, and comparing wall-clock values is exactly what the conflict
rules need. `UTCDateTime` is used on those columns for its microsecond precision only.

**Recorded moments are UTC, and shown in India time.** When something was submitted,
decided, emailed or signed in is stored as naive UTC and sent to the browser with a `Z`
(`schemas/common.py`, `UTCInstant`); the web app converts it to `Asia/Kolkata` whatever the
device's clock says (`web/src/lib/time.ts`). Without the `Z` a browser reads UTC as its own
local time, which put every log entry 5h30m early. "Today" - for expiry, reminders,
retention and date presets - is India's today (`core/clock.py`, `APP_TIMEZONE`), and the
console log and the activity-log CSV are in India time too. Trip times are the other kind of
time and are never converted.

**Revisions begin at submission, not at creation.** Revision 1 is the request as the admin
queue first sees it; edits to a private draft write nothing, because draft churn would bury
the amendments that matter. `edit_count` is therefore `max(revision_number) - 1`.

**Ground staff cannot read `/users`.** The co-traveller picker uses
`GET /requests/colleagues`, which returns id, name and designation and nothing else —
tagging a colleague does not need their email, phone, gender or account state.

**Approving over a clash costs a typed reason.** This is the admin half of B6. The conflict
is *recomputed at decision time*, not trusted from the screen — the queue may have been open
an hour and someone else's trip may have landed since. The reason is written as
`OVERRIDE_CONFLICT` **before** the `APPROVE` row it justifies, so reading the append-only
ledger in order shows the justification already on file. Rejecting a clashing traveller needs
no override: the override exists to justify going ahead anyway.

**Approved and booked are two steps.** `ALLOWED_TRAVELLER_TRANSITIONS` in `core/enums.py` is
the only definition of what may follow what, and booking requires a reference. There is no
undo: every decided state leads only to `CANCELLED`, and correcting a decision goes through
cancel-and-reraise, exactly like an edit after the lock.

**A batch decision is one transaction.** `POST /requests/{id}/decide` takes several travellers
at once — tick three, reject the fourth, press once. One bad decision rolls the whole set
back, so the queue can never show a half-applied decision and the ledger never records one.

**Extraction proposes; a human books.** `app/services/extraction.py` can write onto a
`ticket_documents` row and nothing else — it is structurally incapable of changing a
traveller's status. Only `POST /tickets/{id}/confirm` books anyone, and only an admin can
call it. The model's raw response, its id and its per-field confidence are all kept, so a
booking that turns out wrong is traceable to what was actually proposed rather than to what
someone remembers approving. What the admin saves is stored *beside* the proposal, not over
it, which separates "the model misread it" from "the admin changed it". See addendum §B3.

**A document the model cannot read is `FAILED`, never empty fields.** The failure that
matters is not a refusal, it is a form that looks successfully extracted and contains
nothing, waved through by a tired reviewer. An extraction with no usable field is rejected
with a reason. The model call itself is not perfectly reliable either — Vertex drops
connections occasionally — so every ticket has a **Read again** path.

**Email is off by default, and confined in development.** Three guards, in
`app/services/email.py`: `EMAIL_ENABLED` is false unless set; `EMAIL_ALLOWLIST` means only
those addresses actually leave the building; and `tests/conftest.py` installs an outbox for
the whole test session so no test can open a socket. This matters because the smoke scripts
invent addresses at `@designboxed.com`, which is a **real domain** — mailing a hundred of
them would bounce off one Gmail account and take its sending reputation with it.

**`SUPPRESSED` and `FAILED` are different facts.** Suppressed means nobody tried, on
purpose. Failed means we tried and were refused. Collapsing them would make "was this person
told?" unanswerable, which is the whole reason the notification ledger exists. Retry picks up
`FAILED` only (plus a `QUEUED` email left behind by a restart), and gives up after three
attempts.

**Ticket documents are as private as ID proof scans.** They carry a PNR and a passenger name,
so they are stored outside any static mount, are admin-only, and reach a browser only through
an authenticated endpoint — never a public URL.

**Run the linter before believing the tests.** A green suite means the paths the tests
take work. It says nothing about a name that is only referenced on a path nothing exercises -
which is how `NameError: name 'costs' is not defined` shipped in the ticket-confirmation
handler and sat there through a whole phase. `ruff check app scripts tests` is seconds and
catches that class outright.

**Rate limiting is per address, and layered on top of account lockout.** Phase 1's lockout
stops one password being ground against one account; it does nothing about one address trying
one password against the whole directory, which never trips a single account's counter.
`app/core/ratelimit.py` closes that with a sliding window — a deque of timestamps rather than
a fixed bucket, because a bucket's boundary can be straddled to get double the quota. It is
**in-process**, so two replicas each allow the quota; the fix when that matters is Redis
behind the same call, not a redesign. Only unauthenticated endpoints are limited.

**The security headers are not boilerplate — `nosniff` is load-bearing.** An ID scan or a
ticket must never be sniffed into `text/html` and rendered. The CSP is `default-src 'none'`
because this service returns JSON and files and renders no HTML of its own, so a payload that
somehow reached a response body has nowhere to execute. `/docs` is exempted, or Swagger
white-screens. HSTS is sent only over real TLS — on a plain-HTTP dev server it teaches the
browser to refuse `http://localhost` for two years.

**`text-subtle` was below the AA contrast bar and is not any more.** It measured 3.24:1 on
light and 3.84:1 on dark against a 4.5:1 requirement, and it carries the *smallest* text in
the product — hints, timestamps, metadata at 10–11px. Darkened to 4.98:1 and 5.08:1. If you
change it, measure it; the type scale was doing its job and the colour was not.

**Money is `Decimal` from the form field to the column, never a float.** `Decimal(0.1)` is
0.1000000000000000055, and a report that drifts by a paisa a row is a report nobody trusts by
the end of the quarter. Amounts cross the wire as *strings* for the same reason - a JSON
number is a float by the time it is parsed. `app/services/costs.py` is the only module that
apportions money.

**A split sums to exactly what was entered.** 1,000 rupees across three people is 333.34 +
333.33 + 333.33 - the leftover paise are handed out explicitly rather than rounded away, and
the first traveller (always the requester) absorbs the odd one rather than a colleague being
charged more than the person who booked the trip. The UI *previews* a split before saving it,
so that extra paisa never looks like a bug.

**Only `BOOKED` travellers count as spend.** A cost on an approved-but-unticketed row is a
forecast, reported separately as *committed*. Otherwise a dashboard reconciled against
invoices differs by trips that never happened.

**A missing cost is reported as missing, never as zero.** `uncosted` rides on every analytics
response, and the screen says out loud that the figures understate the truth until it is
zero. A campaign with a blank fare must not look cheaper than one that was booked properly.

**Cost is admin-only to read as well as to write.** Ground staff seeing what a colleague's
flight cost is a personnel problem nobody asked for, and nothing in section 6 needs it.
`to_read()` nulls the cost fields for non-admins rather than relying on the UI to hide them.

**The monthly chart is centred on the current month, not looking backwards.** Field teams
book weeks ahead, so most booked spend at any moment is in the future; a backwards-only
window showed an empty chart for a team that was planning perfectly well.

**Charts use one sequential hue, never a categorical palette.** Every chart in the product
compares magnitude, so none needs one - which means none can suffer the failure a categorical
palette brings: two series a colourblind reader cannot separate. `--chart-1` is a blue chosen
because the other colours are spoken for (brand red is identity, danger red is destructive,
ink is the interactive primary), stepped separately for each surface and validated at 3:1 or
better against both. Marks are thin with a rounded data end; gridlines are hairline and
solid; values are labelled selectively and everything else is reachable on hover and in a
table view.

**Reminders are deduplicated by event, not by run.** Every notice a scheduled job writes
carries a `dedupe_key` naming the thing it is about (`travel-reminder:<traveller id>`), and a
unique index holds the guarantee — not the code, which two workers could race past. The hard
part of a reminder system is not sending; a team that gets the same nudge twice learns to
filter the sender. Running the jobs by hand is therefore safe to press repeatedly.

**Notification categories can be switched off, except the ones that matter.**
`OPTIONAL_CATEGORIES` in `core/enums.py` lists what a person may silence: bookings, room
sharing, reminders, and (admins only) new requests. `DECISIONS` is deliberately absent — an opt-out there produces staff who
turn up at airports. The guard is in `notifications.wants()` as well as the setter, so a
hand-edited preferences table cannot silence a decision either.

**An opt-out stops the email, never the record.** The in-app row is always written, because
it *is* the audit trail and hiding it would hide the trail from the person it is about.

**Channels are a registry, not a branch.** `notifications.SENDERS` maps a channel to a
transport; adding SMS is an entry there plus a value on `NotificationChannel`, which is what
addendum C3 means by channel-agnostic. The email entry is late-bound on purpose — a registry
holding the original function object would silently ignore a test swapping the transport,
which is exactly the bug that lets a suite send real mail.

**The scheduler is an asyncio task in the app lifespan, and it is off by default.** It waits
before its first cycle so a restart loop cannot become a send loop, it runs the jobs in a
thread so a slow query cannot stall the API, and a failing cycle is logged rather than
allowed to end the loop. `SCHEDULER_ENABLED` must be set deliberately, so a developer pointed
at a production database does not start mailing people from their laptop.

**`EXPIRED` is derived, not stored, and it is narrow.** A request is expired only when the
travel date has passed and *every* live traveller is still pending. If two of three were
approved, `PARTIALLY_APPROVED` says more about what happened than "expired" does.
`svc.status_of()` is the single place a request-level status is produced.

---

## Before deploying

Two steps that are deliberately **not** application code, because the application
must not be able to undo them.

### Make the ledger append-only (addendum B9)

The hash chain makes tampering detectable. This makes it impossible through the
app's own database user, which is the other half of section 7's claim.

```bash
cd backend && ./.venv/Scripts/python.exe scripts/grant_append_only.py --show
```

That prints the SQL and changes nothing. `--apply --password '<secret>'` runs it,
using a connection that holds `GRANT OPTION` (normally root). Then point
`DATABASE_URL` at the new `travel_ops_app` user and restart.

Confirm it took with `GET /audit/grants`, which is on the Activity log screen.
That endpoint does not read `SHOW GRANTS` and believe it — grant text is fiddly
enough that parsing it is how you confidently report a protection you do not
have. It **attempts an UPDATE inside a transaction it always rolls back**: either
the database refuses, or you needed to know.

> In development the app connects as root, so this reports "can still rewrite the
> ledger". That is correct and expected — the warning on the Activity log screen
> is telling the truth about that database.

### Check the configuration

The app **refuses to start** with `ENVIRONMENT=production` and any of the
defaults this repository ships — the example signing key, the bootstrap admin
password printed above, an unset `PII_ENCRYPTION_KEY`, a CORS list pointing at
localhost, or a plain-HTTP `FRONTEND_BASE_URL`. In development the same checks
log a warning and let you carry on.

A misconfigured production deploy that boots anyway is worse than one that does
not: it looks healthy, serves traffic, and has a password published in a README.

---

## Build phases

| # | Phase | State |
|---|---|---|
| 0 | Foundations, design system, Gemini spike | **Done** |
| 1 | Identity, RBAC, app shell, audit infrastructure | **Done** |
| 2 | Projects/campaigns, employee directory, ID proofs | **Done** |
| 3 | Requests, co-travellers, edit window, conflicts, co-stay | **Done** |
| 4 | Admin queue, partial approvals | **Done** |
| 5 | Gemini ticket extraction, review step, email delivery | **Done** |
| 6 | Notification inbox, preferences, reminders, ledger | **Done** |
| 7 | Cost capture, campaign financials, cost analytics | **Done** |
| 8 | Audit viewer, hardening, accessibility | **Done** |
