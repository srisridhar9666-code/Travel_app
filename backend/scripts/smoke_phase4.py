"""
Phase 4 end-to-end smoke test.

Covers the admin queue and per-traveller decisions against a running server and
a real MySQL: selective approval on a group request, the conflict override and
its ledger entry, the approve-then-book sequence, rejection reasons reaching the
traveller, and the queue counts.

The section that matters most is the conflict override (addendum B6, the admin
half). Conflicts warn the requester and *stop* the admin - approving anyway costs
a typed reason, and that reason must be on file before the approval it justifies.

    ./.venv/Scripts/python.exe scripts/smoke_phase4.py

Creates throwaway accounts, campaigns and requests, so point it at a dev database.
"""
import sys
import uuid
from datetime import date, datetime, timedelta

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=30)
failures = []

STAMP = uuid.uuid4().hex[:8]
PASSWORD = "GroundStaff@123"
BASE = date.today() + timedelta(days=150)

# Co-stay and conflict data is tenant-wide, so each run works in its own city.
CITY = f"Indore {STAMP[:4].upper()}"


def day(offset: int) -> str:
    return (BASE + timedelta(days=offset)).isoformat()


def at(offset: int, hour: int, minute: int = 0) -> str:
    return datetime.combine(
        BASE + timedelta(days=offset), datetime.min.time()
    ).replace(hour=hour, minute=minute).isoformat()


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


# --- sign in ---------------------------------------------------------------
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_staff(name):
    email = f"p4.{name.split()[0].lower()}.{STAMP}@designboxed.com"
    r = c.post("/users", headers=AH, json={
        "email": email, "full_name": name, "role": "GROUND_STAFF",
        "designation": "EXECUTIVE", "gender": "MALE", "base_location": "Hyderabad",
    })
    if r.status_code != 201:
        check(f"created {name}", False, r.text[:200])
        sys.exit(1)
    token = r.json()["invite_url"].rsplit("=", 1)[-1]
    c.post("/auth/set-password", json={"token": token, "password": PASSWORD})
    login = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["user"]["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


section("cast")
ravi_id, RAVI = make_staff("Ravi Kumar")
arjun_id, ARJUN = make_staff("Arjun Nair")
deepak_id, DEEPAK = make_staff("Deepak Shah")
check("three ground-staff accounts created", all([ravi_id, arjun_id, deepak_id]))

r = c.post("/projects", headers=AH, json={
    "name": "Phase 4 Fulfilment", "code": f"P4-{STAMP[:6].upper()}", "state": "Madhya Pradesh",
})
check("campaign created", r.status_code == 201, r.text[:200])
project_id = r.json()["id"]


def raise_group():
    return c.post("/requests", headers=RAVI, json={
        "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
        "origin": "Hyderabad", "destination": CITY,
        "start_at": at(0, 8), "end_at": at(0, 10),
        "traveller_ids": [arjun_id, deepak_id],
    })


def decide(request_id, traveller_id, body, headers=None):
    return c.post(
        f"/requests/{request_id}/travellers/{traveller_id}/decide",
        headers=headers or AH,
        json=body,
    )


# =========================================================================
section("permissions")
# =========================================================================
r = raise_group()
check("a group request is raised", r.status_code == 201, r.text[:300])
group = r.json()
group_id = group["id"]
by_user = {t["user_id"]: t["id"] for t in group["travellers"]}
check("three traveller rows", len(group["travellers"]) == 3, len(group["travellers"]))

r = decide(group_id, by_user[ravi_id], {"to_status": "APPROVED"}, headers=RAVI)
check("ground staff cannot decide", r.status_code == 403, r.status_code)

r = decide(group_id, by_user[ravi_id], {"to_status": "APPROVED"}, headers=ARJUN)
check("a co-traveller cannot decide either", r.status_code == 403, r.status_code)

r = c.get("/requests/queue/counts", headers=RAVI)
check("ground staff cannot read the queue counts", r.status_code == 403, r.status_code)

# =========================================================================
section("selective approval (SOW 4, addendum B1)")
# =========================================================================
r = decide(group_id, by_user[ravi_id], {"to_status": "APPROVED"})
check("admin approves one traveller", r.status_code == 200, r.text[:300])
after = r.json()
check("the request becomes PARTIALLY_APPROVED", after["status"] == "PARTIALLY_APPROVED", after["status"])
states = {t["user_id"]: t["status"] for t in after["travellers"]}
check("only that person moved", states[ravi_id] == "APPROVED" and states[arjun_id] == "PENDING", states)
check("the decision records who decided", any(t["decided_by_name"] for t in after["travellers"]))

check("one decision locks the request", after["is_editable"] is False, after["is_editable"])
r = c.put(f"/requests/{group_id}", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": "Bhopal", "start_at": at(0, 8),
})
check("the requester can no longer edit it", r.status_code == 409, r.status_code)

r = decide(group_id, by_user[arjun_id], {"to_status": "REJECTED"})
check("rejecting without a reason is refused", r.status_code == 400, r.status_code)
check("the refusal explains why", "reason" in r.json().get("detail", ""), r.json().get("detail"))

r = decide(group_id, by_user[arjun_id], {
    "to_status": "REJECTED", "reason": "Needed on the Nagpur site that week",
})
check("rejecting with a reason succeeds", r.status_code == 200, r.text[:200])
rejected = next(t for t in r.json()["travellers"] if t["user_id"] == arjun_id)
check("the reason is stored on the traveller", rejected["decision_reason"].startswith("Needed"))

notes = c.get("/notifications/mine", headers=ARJUN).json()
check("the rejected traveller is notified", any(n["kind"] == "REQUEST_REJECTED" for n in notes), len(notes))
check(
    "the notice carries the reason",
    any("Nagpur" in n["body"] for n in notes),
    [n["body"][:60] for n in notes],
)
check(
    "the approved traveller was not told they were rejected",
    all(n["kind"] != "REQUEST_REJECTED" for n in c.get("/notifications/mine", headers=RAVI).json()),
)

r = decide(group_id, by_user[ravi_id], {"to_status": "APPROVED"})
check("approving twice is refused", r.status_code == 409, r.status_code)
check("and says so plainly", "already approved" in r.json().get("detail", ""), r.json().get("detail"))

r = decide(group_id, by_user[arjun_id], {"to_status": "APPROVED"})
check("a rejection cannot be reversed", r.status_code == 409, r.status_code)

# =========================================================================
section("booking is a second step (addendum B2)")
# =========================================================================
r = decide(group_id, by_user[deepak_id], {"to_status": "BOOKED", "booking_reference": "PNR-XYZ"})
check("booking someone who is not approved is refused", r.status_code == 409, r.status_code)

r = decide(group_id, by_user[deepak_id], {"to_status": "APPROVED"})
check("approve first", r.status_code == 200, r.text[:200])

r = decide(group_id, by_user[deepak_id], {"to_status": "BOOKED"})
check("booking without a reference is refused", r.status_code == 400, r.status_code)

r = decide(group_id, by_user[deepak_id], {
    "to_status": "BOOKED", "booking_reference": "6E-4412 / PNR QK8T2M",
})
check("booking with a reference succeeds", r.status_code == 200, r.text[:200])
booked = next(t for t in r.json()["travellers"] if t["user_id"] == deepak_id)
check("the reference is stored", booked["booking_reference"] == "6E-4412 / PNR QK8T2M", booked)
check(
    "approved plus booked plus rejected still reads as partly approved",
    r.json()["status"] == "PARTIALLY_APPROVED",
    r.json()["status"],
)
check("every traveller is now decided", r.json()["is_decided"] is True)

# =========================================================================
section("conflict override (addendum B6, the admin half)")
# =========================================================================
r = c.post("/requests", headers=ARJUN, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(20), "check_out": day(24),
})
check("a first stay is raised", r.status_code == 201, r.text[:200])
first_stay = r.json()
first_id = first_stay["id"]

r = c.post("/requests", headers=ARJUN, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(22), "check_out": day(26),
})
check("an overlapping stay is still accepted from the requester", r.status_code == 201, r.status_code)
second = r.json()
second_id = second["id"]
check("the requester was warned", len(second["conflicts"]) >= 1, second["conflicts"])
check("the warning never blocked them", second["status"] == "SUBMITTED", second["status"])

second_traveller = second["travellers"][0]["id"]
r = decide(second_id, second_traveller, {"to_status": "APPROVED"})
check("the admin IS blocked without a reason", r.status_code == 409, r.status_code)
check(
    "the block names the clash and asks for a reason",
    "typed reason" in r.json().get("detail", ""),
    r.json().get("detail"),
)

r = decide(second_id, second_traveller, {
    "to_status": "APPROVED",
    "conflict_override_reason": "Hotel confirmed the first booking was released",
})
check("approving with a typed reason succeeds", r.status_code == 200, r.text[:300])
check("the traveller is approved", r.json()["travellers"][0]["status"] == "APPROVED")

ledger = c.get("/audit", headers=AH, params={"page_size": 200}).json()["items"]
overrides = [e for e in ledger if e["action"] == "OVERRIDE_CONFLICT"]
check("an OVERRIDE_CONFLICT row was written", len(overrides) >= 1, len(overrides))
check(
    "the override carries the typed reason",
    any("released" in (e["reason"] or "") for e in overrides),
    [e["reason"] for e in overrides[:3]],
)
check(
    "the override records what was overridden",
    any("conflicts" in (e["changes"] or {}) for e in overrides),
    overrides[0].get("changes"),
)

# The ledger is append-only and ordered, so the justification must appear before
# the approval it justifies.
relevant = [e for e in sorted(ledger, key=lambda e: e["id"]) if e["entity_id"] == second_traveller and e["entity_type"] == "request_traveller"]
check(
    "the override precedes the approval in the ledger",
    [e["action"] for e in relevant][:2] == ["OVERRIDE_CONFLICT", "APPROVE"],
    [e["action"] for e in relevant],
)

r = decide(first_id, first_stay["travellers"][0]["id"], {
    "to_status": "REJECTED", "reason": "Superseded by the later booking",
})
check("rejecting a clashing traveller needs no override", r.status_code == 200, r.text[:200])

# =========================================================================
section("deciding a whole group in one go")
# =========================================================================
r = raise_group()
batch_id = r.json()["id"]
batch_rows = {t["user_id"]: t["id"] for t in r.json()["travellers"]}

r = c.post(f"/requests/{batch_id}/decide", headers=AH, json={"decisions": [
    {"traveller_id": batch_rows[ravi_id], "to_status": "APPROVED",
     "conflict_override_reason": "Same trip, already cleared"},
    {"traveller_id": batch_rows[arjun_id], "to_status": "REJECTED", "reason": "Double booked"},
    {"traveller_id": batch_rows[deepak_id], "to_status": "APPROVED",
     "conflict_override_reason": "Same trip, already cleared"},
]})
check("a batch decision applies", r.status_code == 200, r.text[:300])
batch_states = {t["user_id"]: t["status"] for t in r.json()["travellers"]}
check("two approved, one rejected", batch_states[ravi_id] == "APPROVED" and batch_states[arjun_id] == "REJECTED", batch_states)

r = raise_group()
rollback_id = r.json()["id"]
rollback_rows = {t["user_id"]: t["id"] for t in r.json()["travellers"]}
r = c.post(f"/requests/{rollback_id}/decide", headers=AH, json={"decisions": [
    {"traveller_id": rollback_rows[ravi_id], "to_status": "APPROVED",
     "conflict_override_reason": "Cleared"},
    # No reason, so this one fails - and must take the first one down with it.
    {"traveller_id": rollback_rows[arjun_id], "to_status": "REJECTED"},
]})
check("a batch with one bad decision is refused", r.status_code == 400, r.status_code)
after = c.get(f"/requests/{rollback_id}", headers=AH).json()
check(
    "nothing from the failed batch was applied",
    all(t["status"] == "PENDING" for t in after["travellers"]),
    [t["status"] for t in after["travellers"]],
)
check("the request is still editable after a rolled-back batch", after["is_editable"] is True)

r = c.post(f"/requests/{rollback_id}/decide", headers=AH, json={"decisions": [
    {"traveller_id": rollback_rows[ravi_id], "to_status": "APPROVED", "conflict_override_reason": "x"},
    {"traveller_id": rollback_rows[ravi_id], "to_status": "REJECTED", "reason": "y"},
]})
check("the same traveller twice in one batch is refused", r.status_code == 400, r.status_code)

# =========================================================================
section("the queue")
# =========================================================================
r = c.get("/requests/queue/counts", headers=AH)
check("queue counts load", r.status_code == 200, r.text[:200])
counts = r.json()
check("counts cover every tab", set(counts) >= {"awaiting", "partially_approved", "booked", "expired"}, list(counts))
check("something is awaiting a decision", counts["awaiting"] >= 1, counts)
check("partially approved requests are counted", counts["partially_approved"] >= 1, counts)

r = c.get("/requests", headers=AH, params={"mine": False, "status": "PARTIALLY_APPROVED", "page_size": 100})
check(
    "the queue filters by derived status",
    all(item["status"] == "PARTIALLY_APPROVED" for item in r.json()["items"]) and r.json()["total"] >= 1,
    r.json()["total"],
)
check(
    "queue rows carry the conflict flag and the edit counter",
    all("conflicts" in item and "edit_count" in item for item in r.json()["items"]),
)

r = c.get(f"/requests/{group_id}/revisions", headers=AH)
check("an admin can read the edit history before deciding", r.status_code == 200 and len(r.json()) >= 1, r.status_code)

# =========================================================================
section("cancelling a single traveller")
# =========================================================================
r = raise_group()
drop_id = r.json()["id"]
drop_rows = {t["user_id"]: t["id"] for t in r.json()["travellers"]}

r = decide(drop_id, drop_rows[arjun_id], {"to_status": "CANCELLED", "reason": "Dropped out"})
check("one traveller can be cancelled off a group", r.status_code == 200, r.text[:200])
check(
    "the rest are untouched",
    sum(1 for t in r.json()["travellers"] if t["status"] == "PENDING") == 2,
    [t["status"] for t in r.json()["travellers"]],
)
check(
    "a cancelled co-traveller does not lock the request",
    r.json()["is_editable"] is True,
    r.json()["is_editable"],
)

r = c.post("/requests/check", headers=ARJUN, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": CITY, "start_at": at(0, 8), "end_at": at(0, 10),
})
check(
    "a cancelled traveller frees that person's calendar",
    all(x["other_request_id"] != drop_id for x in r.json()["conflicts"]),
    [x["other_request_id"] for x in r.json()["conflicts"]],
)

# =========================================================================
section("a cancelled request cannot be decided")
# =========================================================================
r = raise_group()
dead_id = r.json()["id"]
dead_rows = {t["user_id"]: t["id"] for t in r.json()["travellers"]}
c.post(f"/requests/{dead_id}/cancel", headers=RAVI, json={"reason": "Client cancelled the visit"})

r = decide(dead_id, dead_rows[ravi_id], {"to_status": "APPROVED"})
check("deciding a cancelled request is refused", r.status_code == 409, r.status_code)

# =========================================================================
section("the ledger")
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
actions = {e["action"] for e in r.json()["items"] if e["entity_type"] == "request_traveller"}
check("approvals are audited", "APPROVE" in actions, actions)
check("rejections are audited", "REJECT" in actions, actions)
check("bookings are audited", "BOOK" in actions, actions)
check("traveller cancellations are audited", "CANCEL" in actions, actions)
check("conflict overrides are audited", "OVERRIDE_CONFLICT" in actions, actions)

r = c.get("/audit/verify", headers=AH)
check("the audit chain is still intact", r.json()["ok"] is True, r.json())

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All Phase 4 checks passed.")
