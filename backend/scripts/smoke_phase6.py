"""
Phase 6 end-to-end smoke test.

Covers the notification inbox, read state, per-person email preferences, the
delivery ledger and the reminder jobs, against a running server and a real MySQL.

The checks that matter most are the ones about *not* sending. A reminder system
earns its place by being quiet: running the jobs twice must produce one notice,
a category someone switched off must stop the email without hiding the record,
and a decision must remain unswitchable-off however the request is phrased.

    ./.venv/Scripts/python.exe scripts/smoke_phase6.py

Creates throwaway accounts and requests, so point it at a dev database. No real
email leaves the building: the accounts are invented addresses at designboxed.com
and EMAIL_ALLOWLIST keeps them SUPPRESSED.
"""
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=60)
failures = []

STAMP = uuid.uuid4().hex[:8]
PASSWORD = "GroundStaff@123"
CITY = f"Kochi {STAMP[:4].upper()}"


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_staff(name):
    address = f"p6.{name.split()[0].lower()}.{STAMP}@designboxed.com"
    r = c.post("/users", headers=AH, json={
        "email": address, "full_name": name, "role": "GROUND_STAFF",
        "designation": "EXECUTIVE", "gender": "MALE",
    })
    if r.status_code != 201:
        check(f"created {name}", False, r.text[:200])
        sys.exit(1)
    token = r.json()["invite_url"].rsplit("=", 1)[-1]
    c.post("/auth/set-password", json={"token": token, "password": PASSWORD})
    login = c.post("/auth/login", json={"email": address, "password": PASSWORD})
    return (
        login.json()["user"]["id"],
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        address,
    )


section("preconditions")
settings = get_settings()
check(
    "development mail is confined to an allowlist",
    (not settings.email_enabled) or bool(settings.allowed_email_recipients),
    "EMAIL_ENABLED is on with no EMAIL_ALLOWLIST - this run would mail invented addresses",
)

section("cast")
ravi_id, RAVI, ravi_email = make_staff("Ravi Kumar")
check("a ground-staff account is created", bool(ravi_id))

r = c.post("/projects", headers=AH, json={
    "name": "Phase 6 Notifications", "code": f"P6-{STAMP[:6].upper()}", "location": "Kerala",
})
project_id = r.json()["id"]
check("campaign created", r.status_code == 201, r.status_code)


def raise_and_book(days_out, reference):
    """A request taken all the way to BOOKED, which is what a reminder needs."""
    start = datetime.combine(date.today() + timedelta(days=days_out), datetime.min.time())
    r = c.post("/requests", headers=RAVI, json={
        "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
        "origin": "Hyderabad", "destination": CITY,
        "start_at": start.replace(hour=7).isoformat(),
        "end_at": start.replace(hour=9).isoformat(),
    })
    request = r.json()
    traveller = request["travellers"][0]["id"]
    c.post(f"/requests/{request['id']}/travellers/{traveller}/decide", headers=AH,
           json={"to_status": "APPROVED", "conflict_override_reason": "Smoke run"})
    c.post(f"/requests/{request['id']}/travellers/{traveller}/decide", headers=AH,
           json={"to_status": "BOOKED", "booking_reference": reference})
    return request


# =========================================================================
section("the inbox and read state")
# =========================================================================
r = c.get("/notifications/mine", headers=RAVI)
check("a new account has an empty inbox", r.status_code == 200 and r.json() == [], len(r.json()))

r = c.get("/notifications/unread-count", headers=RAVI)
check("and nothing unread", r.json()["unread"] == 0, r.json())

booked = raise_and_book(1, "6E-7781")
check("a request is raised, approved and booked", booked["id"] > 0)

notices = c.get("/notifications/mine", headers=RAVI).json()
check("decisions land in the inbox", len(notices) >= 2, len(notices))
check("only in-app rows appear there", all(n["channel"] == "IN_APP" for n in notices))
check("each notice carries a category", all(n["category"] for n in notices), notices[0])
kinds = {n["kind"] for n in notices}
check("approval and booking are both recorded", {"REQUEST_APPROVED", "REQUEST_BOOKED"} <= kinds, kinds)

unread_before = c.get("/notifications/unread-count", headers=RAVI).json()["unread"]
check("they start unread", unread_before == len(notices), unread_before)

first = notices[0]["id"]
r = c.post(f"/notifications/{first}/read", headers=RAVI)
check("one notice can be marked read", r.json()["marked"] == 1, r.json())
check(
    "the count drops by one",
    c.get("/notifications/unread-count", headers=RAVI).json()["unread"] == unread_before - 1,
)

r = c.post(f"/notifications/{first}/read", headers=RAVI)
check("marking it again does nothing", r.json()["marked"] == 0, r.json())

r = c.post("/notifications/read", headers=AH)
check("an admin marking all read does not touch someone else's", r.status_code == 200)
check(
    "the other person's count is unchanged",
    c.get("/notifications/unread-count", headers=RAVI).json()["unread"] == unread_before - 1,
)

r = c.post("/notifications/read", headers=RAVI)
check("marking all read clears the bell", r.status_code == 200, r.json())
check("nothing is unread now", c.get("/notifications/unread-count", headers=RAVI).json()["unread"] == 0)

# =========================================================================
section("preferences")
# =========================================================================
r = c.get("/notifications/preferences", headers=RAVI)
check("preferences load", r.status_code == 200, r.text[:200])
prefs = r.json()["email"]
check("everything is on by default", set(prefs.values()) == {True}, prefs)
check("only switchable categories are offered", "DECISIONS" not in prefs, list(prefs))
check(
    "the switchable ones are there",
    {"BOOKINGS", "ROOM_SHARING", "REMINDERS"} == set(prefs),
    list(prefs),
)

r = c.patch("/notifications/preferences", headers=RAVI,
            json={"category": "DECISIONS", "enabled": False})
check("decisions cannot be switched off", r.status_code == 400, r.status_code)
check(
    "and the refusal explains why",
    "your own travel" in r.json().get("detail", ""),
    r.json().get("detail"),
)

r = c.patch("/notifications/preferences", headers=RAVI,
            json={"category": "REMINDERS", "enabled": False})
check("reminders can be switched off", r.status_code == 200, r.text[:200])
check("and the change sticks", r.json()["email"]["REMINDERS"] is False, r.json()["email"])

r = c.patch("/notifications/preferences", headers=RAVI,
            json={"category": "REMINDERS", "enabled": False})
check("switching it off twice is idempotent", r.json()["email"]["REMINDERS"] is False)

# =========================================================================
section("reminder jobs - the quiet ones")
# =========================================================================
r = c.get("/notifications/scheduler", headers=AH)
check("scheduler status loads", r.status_code == 200, r.text[:200])
status_body = r.json()
check("it reports whether the loop is running", "running" in status_body, status_body)
check("and the thresholds it uses", status_body["travel_reminder_days"] >= 1, status_body)

r = c.get("/notifications/scheduler", headers=RAVI)
check("ground staff cannot read scheduler status", r.status_code == 403, r.status_code)

r = c.post("/notifications/run-jobs", headers=RAVI)
check("ground staff cannot run the jobs", r.status_code == 403, r.status_code)

before = len(c.get("/notifications/mine", headers=RAVI).json())
r = c.post("/notifications/run-jobs", headers=AH)
check("an admin can run the jobs", r.status_code == 200, r.text[:300])
jobs = {j["job"]: j for j in r.json()}
check("every job reports", {"remind_travellers", "remind_admins_of_stale_requests",
                            "retry_undelivered"} <= set(jobs), list(jobs))

after = c.get("/notifications/mine", headers=RAVI).json()
reminders_seen = [n for n in after if n["kind"] == "TRAVEL_REMINDER"]
check("the imminent trip produced a reminder", len(reminders_seen) == 1, len(reminders_seen))
check("the reminder carries the booking reference", "6E-7781" in reminders_seen[0]["body"], reminders_seen[0]["body"])

# The opt-out: in-app kept, email skipped.
ledger = c.get("/notifications/ledger", headers=AH, params={"page_size": 200}).json()
mine = [n for n in ledger["items"] if n["user_id"] == ravi_id and n["kind"] == "TRAVEL_REMINDER"]
check("the reminder was recorded in app", any(n["channel"] == "IN_APP" for n in mine), mine)
check(
    "but no email was sent, because reminders are switched off",
    not any(n["channel"] == "EMAIL" for n in mine),
    [n["channel"] for n in mine],
)

r = c.post("/notifications/run-jobs", headers=AH)
again = [n for n in c.get("/notifications/mine", headers=RAVI).json() if n["kind"] == "TRAVEL_REMINDER"]
check("running the jobs again sends nothing new", len(again) == 1, len(again))

r = c.post("/notifications/run-jobs", headers=AH)
third = [n for n in c.get("/notifications/mine", headers=RAVI).json() if n["kind"] == "TRAVEL_REMINDER"]
check("and again, still nothing", len(third) == 1, len(third))

# A second trip is a separate event and must get its own reminder.
raise_and_book(2, "6E-9902")
c.post("/notifications/run-jobs", headers=AH)
two_trips = [n for n in c.get("/notifications/mine", headers=RAVI).json() if n["kind"] == "TRAVEL_REMINDER"]
check("a second trip gets its own reminder", len(two_trips) == 2, len(two_trips))

# A trip beyond the horizon is left alone.
raise_and_book(30, "6E-5550")
c.post("/notifications/run-jobs", headers=AH)
still_two = [n for n in c.get("/notifications/mine", headers=RAVI).json() if n["kind"] == "TRAVEL_REMINDER"]
check("a distant trip is not reminded yet", len(still_two) == 2, len(still_two))

# Turning reminders back on restores the email channel for the next event.
c.patch("/notifications/preferences", headers=RAVI, json={"category": "REMINDERS", "enabled": True})
raise_and_book(1, "6E-3311")
c.post("/notifications/run-jobs", headers=AH)
ledger = c.get("/notifications/ledger", headers=AH, params={"page_size": 200}).json()
emailed = [
    n for n in ledger["items"]
    if n["user_id"] == ravi_id and n["kind"] == "TRAVEL_REMINDER" and n["channel"] == "EMAIL"
]
check("switching reminders back on restores the email", len(emailed) >= 1, len(emailed))
check(
    "and it is SUPPRESSED, not FAILED, for an invented address",
    all(n["status"] == "SUPPRESSED" for n in emailed),
    [n["status"] for n in emailed],
)

# =========================================================================
section("the delivery ledger")
# =========================================================================
r = c.get("/notifications/ledger", headers=AH, params={"page_size": 50})
check("the ledger loads", r.status_code == 200, r.text[:200])
body = r.json()
check("it summarises by status", "by_status" in body["summary"], body["summary"])
check("it counts email separately", body["summary"]["emails"] > 0, body["summary"])

r = c.get("/notifications/ledger", headers=AH, params={"search": ravi_email, "page_size": 50})
check(
    "the ledger can be searched by address",
    r.json()["total"] >= 1 and all(
        ravi_email in (n["to_address"] or "") for n in r.json()["items"]
    ),
    r.json()["total"],
)

r = c.get("/notifications/ledger", headers=AH, params={"channel": "EMAIL", "page_size": 20})
check("and filtered by channel", all(n["channel"] == "EMAIL" for n in r.json()["items"]))

r = c.get("/notifications/ledger", headers=AH, params={"status": "SUPPRESSED", "page_size": 20})
check("and by status", all(n["status"] == "SUPPRESSED" for n in r.json()["items"]))

r = c.get("/notifications/ledger", headers=RAVI)
check("ground staff cannot read the ledger", r.status_code == 403, r.status_code)

r = c.post("/notifications/retry", headers=AH)
check("retry runs", r.status_code == 200, r.json())
check("and leaves suppressions alone", r.json()["attempted"] == 0, r.json())

# =========================================================================
section("stale request nudges")
# =========================================================================
r = c.post("/requests", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY,
    "check_in": (date.today() + timedelta(days=40)).isoformat(),
    "check_out": (date.today() + timedelta(days=43)).isoformat(),
})
stale_id = r.json()["id"]
check("a request is raised and left undecided", r.status_code == 201, r.status_code)

# Age it past the threshold. Phase 6 owns no endpoint for backdating a
# submission, and inventing one only for a test would be worse than reaching
# past the API here.
from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

with engine.begin() as conn:
    conn.execute(
        text("UPDATE travel_requests SET submitted_at = :when WHERE id = :rid"),
        {
            "when": datetime.utcnow() - timedelta(days=10),
            "rid": stale_id,
        },
    )

admin_before = len([
    n for n in c.get("/notifications/mine", headers=AH).json()
    if n["kind"] == "REQUEST_STALE" and n["request_id"] == stale_id
])
c.post("/notifications/run-jobs", headers=AH)
admin_after = [
    n for n in c.get("/notifications/mine", headers=AH).json()
    if n["kind"] == "REQUEST_STALE" and n["request_id"] == stale_id
]
check("the admin is told about the stale request", len(admin_after) == admin_before + 1, len(admin_after))
check("the notice says how long it has waited", "days" in admin_after[0]["title"], admin_after[0]["title"])

c.post("/notifications/run-jobs", headers=AH)
admin_again = [
    n for n in c.get("/notifications/mine", headers=AH).json()
    if n["kind"] == "REQUEST_STALE" and n["request_id"] == stale_id
]
check("it is not repeated on the next run", len(admin_again) == len(admin_after), len(admin_again))

# Deciding it takes it out of the stale set. Measured as a *change*, not as a
# global zero: a dev database that has been used accumulates other undecided
# requests, and asserting "nothing anywhere is stale" makes this check fail for
# reasons that have nothing to do with what it is testing.
before_decide = next(
    j for j in c.post("/notifications/run-jobs", headers=AH).json()
    if j["job"] == "remind_admins_of_stale_requests"
)["stale"]

travellers = c.get(f"/requests/{stale_id}", headers=AH).json()["travellers"]
c.post(f"/requests/{stale_id}/travellers/{travellers[0]['id']}/decide", headers=AH,
       json={"to_status": "REJECTED", "reason": "Not needed after all"})

after_decide = next(
    j for j in c.post("/notifications/run-jobs", headers=AH).json()
    if j["job"] == "remind_admins_of_stale_requests"
)["stale"]
check(
    "deciding a request takes it out of the stale set",
    after_decide == before_decide - 1,
    {"before": before_decide, "after": after_decide},
)

# =========================================================================
section("the ledger")
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
entries = r.json()["items"]
check(
    "preference changes are audited",
    any(e["entity_type"] == "notification_preference" for e in entries),
    {e["entity_type"] for e in entries},
)
check(
    "running the jobs is audited",
    any(e["entity_type"] == "notification" and "reminder jobs" in e["summary"] for e in entries),
)

r = c.get("/audit/verify", headers=AH)
check("the audit chain is still intact", r.json()["ok"] is True, r.json())

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All Phase 6 checks passed.")
