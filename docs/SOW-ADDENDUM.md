# SOW Addendum — Field Logistics & Travel Management System

Companion to `Scope of Work_ Field Logistics & Travel Management System.pdf` (2 pages, 7 sections).
This document records gaps found during the pre-build review, the decisions taken against them,
and the items still open. Where this addendum and the PDF disagree, **this document wins**.

Status: **All eight phases complete** · Last reviewed 2026-09-22

**Built so far:** B8 (ID numbers encrypted at rest, masked in lists, `VIEW_SENSITIVE` on
every read of a number or scan, scans stored outside the web root), B9 (hash-chained
append-only ledger), B10 (admin-invited accounts, no self-registration, lockout, self-serve
reset), B11's role/designation, theme and retention items, and **C4** — ID proofs are purged
90 days after the employee's exit date. All covered by tests.

**Phase 3 added:** **A1** in full (the edit window, the lock on first admin action, and a
revision row with a field-level diff behind an "edited N times" marker), **B1** and **B2**
exercised end to end (per-traveller status rows; `derive_request_status` is the only source
of a request-level value), **B4**'s cancellation path with reason and actor, **B6** (the
conflict rules, written out explicitly and warning-only — see the note below), and **B7**
(exact-gender-match room sharing). **C2 is built to the recommendation, not to an answer** —
see below.

**Phase 4 added:** the admin queue and per-traveller decisions — **B1** as the working
decision unit (a group of four can leave the queue two approved, one rejected, one pending),
**B2**'s missing `APPROVED` state as a real second step before `BOOKED`, **B4**'s
traveller-level cancellation, and **B6**'s admin half: approving over a clash is *blocked*
until a reason is typed, and that reason is written as `OVERRIDE_CONFLICT` before the
approval. `EXPIRED`, the one status in §4's vocabulary nothing could previously reach, is now
derived. Decisions also write the first per-traveller notification rows, which Phase 6 gives
a delivery channel.

**Phase 5 added:** **B3** in full — the highest-risk item in this document. Ticket upload,
Gemini extraction and the human review step that stands between them and a booking. Also the
notification ledger (**B11**'s "via email integration" correction) with real SMTP delivery,
which answers **C3** to its recommendation.

**Phase 6 added:** the notification inbox with read state, per-person email preferences by
category, the deduplicated reminder jobs and the background scheduler that runs them, and the
admin delivery ledger. This completes **C3** as recommended.

**Phase 7 added:** cost capture and the reporting that depends on it - **B5** resolved by
implementing **C1**'s recommendation, plus Campaign Financials (§2) and Cost Analytics (§6).

**Phase 8 added:** the audit viewer, the hardening pass, and the accessibility pass. **B9**
is now complete on both halves - the chain was built in Phase 1, and the database grant that
makes rewriting *impossible* rather than merely detectable ships as
`scripts/grant_append_only.py`, with `GET /audit/grants` probing whether it is actually in
force. Also: per-address rate limiting on every unauthenticated endpoint, security headers
on every response, a startup check that refuses to run in production with the defaults this
repository ships, and a contrast fix for the smallest text in the product.

**Still open:** C1 and C2 are both **built to their recommendations and still want the
client's answer**; neither is expensive to change. C3 is **answered as recommended**.

---

## A. New requirement not present in the SOW

### A1. Requests stay editable until an admin acts — and every edit is recorded

The SOW has no draft state, no edit window and no revision history. §4's status list
(Pending, Partially Approved, Booked, Rejected) has nowhere to represent "the requester
amended this." Added as scope:

- A request is **freely editable while every traveller on it is `PENDING`**.
- The moment an admin approves, rejects or books **any** traveller, the request **locks**.
- Every save writes a **revision row**: revision number, editor, timestamp, and a
  field-level before/after diff.
- The admin queue shows an **"edited N×"** marker with the full diff on expand, so an
  admin never approves a version they did not read.
- After lock, changes go through cancel-and-reraise. An in-place amendment flow is V2.

Encoded in `backend/app/core/enums.py` as `is_editable()` and `_DECIDED`.

---

## B. Corrections to the SOW as written

### B1. Status belongs to the traveller, not the request *(structural)*

§4 grants selective per-person approval while describing one status per request. Approval,
conflict state, ticket and notification are all per-person. **Resolved:** status lives on
the traveller row; the request-level value is derived by `derive_request_status()` and is
never set directly.

### B2. A missing `APPROVED` state *(structural)*

§3 refers to an "approved booking", implying approved is not the same as booked, but §4's
list has no such state. **Resolved:** the full set is `DRAFT`, `SUBMITTED`,
`PARTIALLY_APPROVED`, `APPROVED`, `BOOKED`, `REJECTED`, `CANCELLED`, `EXPIRED`.

### B3. AI extraction must not auto-book or auto-notify *(highest risk item)*

§4 says the system populates fields "instantly" and sets status to Booked on upload, then
emails staff. A single model misparse would silently book a wrong PNR and mail it out.
**Resolved:** the flow becomes `upload → extract → admin review & confirm → Booked → notify`.
The raw extraction JSON, model id and per-field confidence are stored for audit. No
notification ever fires on unconfirmed extracted data.

**Built in Phase 5** (`backend/app/services/extraction.py`, `app/routers/tickets.py`, tests
in `tests/test_extraction.py`). The separation is structural rather than procedural:
extraction writes onto a `ticket_documents` row and has no code path to a traveller's status.
Only `POST /tickets/{id}/confirm` books anyone, it is admin-only, and it refuses if the
traveller has not been approved first.

| Guard | Why |
|---|---|
| The reference is *pre-filled*, not saved | A person types or accepts the value that gets booked. The model never has the last word. |
| What the admin saved is stored beside the proposal | Separates "the model misread it" from "the admin changed it", months later. |
| Per-field confidence, with a 0.75 review threshold | The reviewer is told where to look first. A confidently wrong model is the real failure mode, so the document is shown beside the fields regardless. |
| Mismatches against the request are flagged | Wrong route, wrong date, wrong name on the ticket. Advisory — occasionally a deliberate change — but never silent. |
| An unreadable document is `FAILED`, not empty fields | The dangerous outcome is a form that looks extracted and contains nothing. |
| `notify` is a per-confirmation choice | A correction should not re-mail someone. |

The model call is not perfectly reliable in practice — Vertex dropped a connection during
Phase 5 testing — so every ticket has a re-read path and a failure is a visible state rather
than a lost upload.

### B4. No cancellation or post-booking change path

Trips get cancelled, dates move, people drop out; the SOW covers none of it. **Resolved:**
a `CANCELLED` status plus reason and actor, at both request and traveller level. Refund
tracking and in-place amendment are explicitly V2.

### B5. Cost has no defined source - §2 and §6 are unbuildable as written

Campaign Financials and Cost Analytics both need money, but nothing says who enters it or
when. **OPEN - see C1.**

**Built in Phase 7 to C1's recommendation** (`backend/app/services/costs.py` and
`analytics.py`, tests in `tests/test_costs.py`). Cost lives on the **traveller** row, for the
same reason status does: a shared cab is one payment and several people, and every report in
§6 is per person or per campaign. The rules that make the numbers trustworthy:

| Rule | Why |
|---|---|
| Every amount is `Decimal`, and crosses the wire as a string | A float is how a report starts disagreeing with an invoice. |
| A split sums to **exactly** the total | 1,000 across three is 333.34 + 333.33 + 333.33. The odd paisa goes to the requester, not a colleague. |
| Only `BOOKED` counts as spend | Approved-but-unticketed is reported separately as *committed* - a forecast, not money. |
| A missing cost is `uncosted`, never zero | A campaign with a blank fare must not look cheaper than one booked properly. |
| Cost is admin-only to read | Ground staff seeing a colleague's fare is a personnel problem nothing in §6 needs. |
| A rejected traveller cannot be charged | It would inflate a campaign total with money nobody paid. |

### B6. Conflict detection rules are undefined

"Validates the dates against existing itineraries" leaves open whether a same-day airport
cab conflicts with its own flight, whether hotel overlap is counted by night or by
timestamp, and which statuses occupy the calendar. Read literally, §3 hard-blocks and would
reject legitimate cab-plus-flight pairs on day one. **Resolved:** conflicts **warn**; the
requester may still submit; the admin sees a prominent flag and must type a reason to
approve anyway, which is written to the audit log as `OVERRIDE_CONFLICT`. Statuses that
occupy the calendar are `PENDING`, `APPROVED` and `BOOKED` (`ACTIVE_TRAVELLER_STATUSES`).

**Built in Phase 3** (`backend/app/services/conflicts.py`, tests in `tests/test_conflicts.py`).
The rules, in full:

| Rule | Decision |
|---|---|
| What occupies a calendar | An active traveller status on a request that is neither a draft nor cancelled. A draft never warns anyone — it is private, and a warning would disclose it. |
| Hotel against hotel | By **night**: `[check_in, check_out)`. Checking out on the 5th and into another hotel on the 5th is a move, not a clash. Any shared night conflicts, whatever the cities. |
| Journey against journey | By **closed datetime window** `[start_at, end_at]`, with a missing arrival collapsing it to the instant of departure. Closed, because two journeys that merely touch still put one person in two places. |
| Cab against that person's own long-distance leg | **Never a conflict**, exempted by shared calendar day in both directions. Any cab whose window overlaps the journey necessarily shares a day with it, so the exemption is total in practice — stated here because it is a deliberately broad rule. |
| Hotel against journey | **Never a conflict.** You fly somewhere in order to sleep there. |
| Same shape, same dates | Reported as `DUPLICATE_REQUEST` rather than an overlap — same city and identical nights, or same route on the same departure day. Nearly always a double submission, and saying so is more use to an admin than "overlapping travel". |
| Severity | `WARNING`, always. `BLOCKING` exists in the enum and is never emitted in V1. |

Every co-traveller is checked separately, and an edit is never compared against its own
stored row.

**The admin half, built in Phase 4** (`backend/app/services/decisions.py`, tests in
`tests/test_decisions.py`). Where the requester is only warned, the admin is stopped:
approving a traveller who has a live clash returns 409 until a reason is typed. The clash is
**recomputed at decision time** rather than taken from the request the queue rendered, and
the `OVERRIDE_CONFLICT` row — carrying the typed reason and the exact warnings overridden —
is written *before* the `APPROVE` row, so the append-only ledger reads in the right order.
Rejecting a clashing traveller needs no override.

### B7. Co-stay matching has a privacy and consent gap

§3's prompt discloses a colleague's city and dates to the requester, and lets the requester
unilaterally opt into sharing that person's room. Gender is also treated as binary.
**Resolved:** any pairing that is not an exact `Gender` match falls back to separate rooms —
covering `OTHER` and `UNDISCLOSED`, not just cross-gender. **OPEN — see C2** for whether the
other employee must consent.

**Built in Phase 3** (`backend/app/services/costay.py`). `OTHER` paired with `OTHER` is
deliberately refused as well: identical labels are not consent, and the fallback costs a
room rather than anything that matters. The filter runs in the matcher *and* again on save,
so a hand-rolled POST cannot slip past it. A candidate removed on gender grounds is never
mentioned to the requester. Candidates are drawn from `PENDING` as well as `BOOKED` stays —
by the time both are booked, the saving has already been missed.

### B8. ID proofs are sensitive PII with no stated handling

§5 collects them; §7 says nothing about encryption, access, retention or masking.
**Resolved:** the number is **encrypted at rest** (Fernet) and never appears in a list
response; scans are stored outside the web root and are never web-served; both are admin-only;
and reading either — the full number or the scan — writes a `VIEW_SENSITIVE` audit row before
the data is returned. A keyed fingerprint catches the same document filed against two people
without decrypting anything. Retention is **90 days from the exit date** (C4, answered).

### B9. "Immutable" audit log needs a mechanism

§7 asserts immutability without saying how. **Resolved:** an append-only table; the
application DB user is granted `INSERT` and `SELECT` only, with no `UPDATE` or `DELETE`;
each row carries the prior row's hash so tampering is detectable.

**Completed in Phase 8.** The chain shipped in Phase 1; the grant now ships as
`backend/scripts/grant_append_only.py`, which creates a dedicated application user with
full rights on every table *except* `audit_logs`, and `INSERT`/`SELECT` on that one. The
per-table form matters: a schema-wide `ALL PRIVILEGES ON travel_ops.*` would silently
outrank the narrow grant and `SHOW GRANTS` would still look plausible.

`GET /audit/grants` reports whether it is in force, and it does **not** parse grant text -
it attempts an `UPDATE` inside a transaction it always rolls back. Either the database
refuses, which is the answer we want, or it does not, which is the answer we need. The
Activity log screen shows the result, so an administrator can see at a glance whether the
ledger is merely tamper-*evident* or actually tamper-*proof*.

### B10. Authentication is one line of scope

No password policy, session length, reset, lockout or onboarding path for 100 staff.
**Resolved:** admin-created accounts, one-time emailed invite to set a password, and **no
self-registration**. bcrypt, JWT, 8-hour sessions, lockout on repeated failures, self-serve
reset. MFA and Google SSO are V2.

### B11. Smaller corrections

| Ref | SOW text | Correction |
|---|---|---|
| §5 | "minimum 2+ months lookback" | Retain **all** history; default the *view* to 90 days. The cap was artificial. |
| §5 | Designation enables "future routing of approval workflows" | Confirmed **out of scope for V1**. Approval is admin-only; designation is reporting metadata. |
| §1 | "Light/Dark mode toggle" | Three-way: Light / Dark / **System**, persisted per user in the DB so it follows them across devices. |
| §1 | 2 roles vs §5's 3 designations | Role and designation are **orthogonal**. `Role` is permissions; `Designation` is hierarchy. |
| §4 | "via email integration" | Needs a notification ledger with delivery status and retries. **Built in Phase 5**: every notice is a row carrying channel, address, attempts, sent time and the server's refusal. `SUPPRESSED` (nobody tried, on purpose) is kept distinct from `FAILED` (we tried, we were refused) — collapsing them would make "was this person told?" unanswerable. |
| — | (absent) | Ground staff work from phones. The UI is committed to **mobile-first responsive web**. |
| — | (absent) | No SLA, data volumes, browser support or backup/DR. Low risk at ~110 users; flagged for the contract, not the build. |

---

## C. Open — still needs a decision from the client

| # | Question | Blocks | Recommendation |
|---|---|---|---|
| **C1** | Who enters cost, and when? Admin at booking, AI-extracted from the ticket, or both? Plus currency, taxes, and how a shared cab or room splits across travellers. | Phase 7 (§2, §6) — **built to the recommendation, still needs the client's answer** | Admin confirms cost on the booking screen, pre-filled by extraction. INR. Shared costs split evenly with a manual override. **Built exactly this.** The extraction now reads a fare and offers it; an admin confirms or corrects it, as with the PNR. Single currency - the column stores the code but nothing converts, and nothing pretends to. **Taxes are not itemised**: the amount recorded is the total the traveller was charged, which is what §6 reports on. If the client needs tax broken out for reclaim, that is one more column and a second input, not a redesign. |
| **C2** | Must the *other* employee consent before a co-stay is confirmed, or is requester plus admin enough? And how much identity does the prompt reveal — full name, or just "1 colleague available"? | Phase 3 (§3) — **built to the recommendation, still needs the client's answer** | Show name and designation (this is an internal ops tool), require **admin** confirmation, and notify the other employee. **Built exactly this.** The colleague's own consent is *not* required; `SHARE_EXISTING` leaves `share_confirmed_at` null until an admin signs it off, and the colleague gets an in-app notice the moment the request is made. If the client wants their consent too, it is one more column on `request_travellers` and one more precondition in `confirm_share` — the matching and disclosure rules do not move. |
| ~~C3~~ | ~~Email only, or also WhatsApp/SMS? Field staff may not read email.~~ | ~~Phase 6 (§4)~~ | **Answered as recommended, and built.** Email in V1 over Gmail SMTP/TLS, every attempt recorded per row. Adding SMS is one `NotificationChannel` value plus one entry in `notifications.SENDERS` — no migration, no branching. Staff can switch off bookings, room-sharing and reminder email for themselves; decisions cannot be switched off. Delivery is **off by default** and confined by `EMAIL_ALLOWLIST` in development, because the smoke scripts invent addresses at a real domain. |
| ~~C4~~ | ~~How long are ID proofs retained after an employee leaves?~~ | ~~Phase 2 (§5)~~ | **Answered: 90 days from the exit date.** Implemented in `app/services/retention.py`; the window is configurable via `ID_PROOF_RETENTION_DAYS`. |

---

## D. Decisions already confirmed

| Decision | Choice |
|---|---|
| Edit window | Editable until first admin action, every revision recorded |
| Decisions | Per traveller, not per request. No undo — a wrong decision is cancelled and reraised |
| Extraction | Proposes only. A human confirms the reference before anyone is booked or emailed |
| Email | Off unless switched on; allowlisted in development; every attempt recorded |
| Reminders | Deduplicated by event, so a job may run on any schedule without repeating itself |
| Opt-outs | Per person, per category, email only — the in-app record is never hidden |
| Cost | On the traveller, entered by an admin at booking, INR, split evenly with a manual override |
| Spend | Only `BOOKED` rows. Approved-but-unticketed is reported separately as committed |
| Ledger | Hash-chained *and* append-only at the database. The app refuses to start in production with shipped defaults |
| Booking | A second step after approval, and it needs a reference (typed in V1, extracted in Phase 5) |
| Tenancy | Single-tenant (`travel_ops`), but every core table carries `tenant_id` so a second company needs config, not migration |
| Authentication | Email plus password, admin-invited, no self-registration |
| Conflicts | Warn the requester; admin may override with a typed, audited reason |
| ID proof retention | 90 days from the employee's exit date, not from deactivation |

---

## E. Brand asset findings

| Finding | Detail | Action taken |
|---|---|---|
| Dark-mode logo had a pink halo | `db_logo_white.png` stored pink in its fully transparent pixels. Correct renderers ignore RGB at alpha 0, but every bilinear scaler interpolates it, producing a visible fringe. **84,888 pixels affected.** | Cleaned to `web/public/brand/logo-dark.png`. `db_logo_black.png` was already clean (0 affected). |
| Brand red fails AA as body text on light | `#FE0024` on white is about **4.0:1**, under the 4.5:1 AA bar. On near-black it is about **4.9:1** and passes. | `--brand` (`#FE0024`) for fills and the logo; `--brand-strong` (`#D70021`, **5.4:1**) for brand-coloured text in light mode, `#FF4D63` (**6.1:1**) in dark. |
| Brand red collides with destructive red | The product's main verbs are Approve and Reject. Brand red and danger red being the same colour makes every screen read as an alarm. | **Red is given to destructive.** The interactive primary is ink — near-black on light, near-white on dark. Brand red is reserved for the logo, the nav rail and active indicators. |
| No icon-only asset | Neither file works at favicon size; both are portrait (roughly 1:1.5) and awkward in a horizontal header. | Generated `mark.png` (512), `apple-touch-icon.png` (180) and `favicon.png` (64) from the red mark, with the TM badge dropped — its clipped arc read as an artefact at small sizes. |
| Rasters only | No SVG; the dark logo is just 495px wide, marginal at 2x DPI on a login hero. | **Request SVG originals plus a horizontal lockup** from the brand owner. Rasters are adequate for now. |
