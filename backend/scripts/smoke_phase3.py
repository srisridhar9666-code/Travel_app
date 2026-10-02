"""
Phase 3 end-to-end smoke test.

Covers requests, co-travellers, the edit window, revision history, conflict
warnings and co-stay matching against a running server and a real MySQL.

The two sections that matter most are the edit window and the conflict rules.
Both are decisions taken against gaps in the SOW (addendum A1 and B6), so this
script proves the *behaviour*, not just the status codes: that a conflict warns
without blocking, that the airport cab does not warn at all, and that the first
admin decision genuinely closes the door on editing.

    ./.venv/Scripts/python.exe scripts/smoke_phase3.py

Creates throwaway accounts, campaigns and requests, so point it at a dev database.
"""
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

# Python puts the script directory on the path, not the working directory, and
# one check below reaches past the API into the database.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=30)
failures = []

STAMP = uuid.uuid4().hex[:8]
PASSWORD = "GroundStaff@123"

# Far enough out that nothing already in the dev database sits on these dates.
BASE = date.today() + timedelta(days=120)

# Co-stay matching is tenant-wide by design - it has to be, or it would never
# find the colleague. That means a fixed city would let one run of this script
# match the hotels the previous run left behind, so each run gets its own.
CITY = f"Mumbai {STAMP[:4].upper()}"


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


def make_staff(name, gender):
    """Create a ground-staff account and take it all the way to a usable token,
    because every interesting rule in Phase 3 is a ground-staff rule."""
    email = f"p3.{name.split()[0].lower()}.{STAMP}@designboxed.com"
    r = c.post(
        "/users",
        headers=AH,
        json={
            "email": email,
            "full_name": name,
            "role": "GROUND_STAFF",
            "designation": "EXECUTIVE",
            "gender": gender,
            "base_location": "Hyderabad",
        },
    )
    if r.status_code != 201:
        check(f"created {name}", False, r.text[:200])
        sys.exit(1)
    token = r.json()["invite_url"].rsplit("=", 1)[-1]
    c.post("/auth/set-password", json={"token": token, "password": PASSWORD})
    login = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    user = login.json()["user"]
    return user["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


section("cast")
ravi_id, RAVI = make_staff("Ravi Kumar", "MALE")
arjun_id, ARJUN = make_staff("Arjun Nair", "MALE")
meera_id, MEERA = make_staff("Meera Iyer", "FEMALE")
sam_id, _ = make_staff("Sam Roy", "FEMALE")  # only Male or Female can be recorded
check("four ground-staff accounts created and signed in", all([ravi_id, arjun_id, meera_id, sam_id]))

code = f"P3-{STAMP[:6].upper()}"
r = c.post(
    "/projects",
    headers=AH,
    json={"name": "Phase 3 Field Sweep", "code": code, "state": "Maharashtra"},
)
check("campaign created", r.status_code == 201, r.text[:200])
project_id = r.json()["id"]


def raise_request(headers, body):
    return c.post("/requests", headers=headers, json=body)


LONG_HAUL = {
    "request_type": "LONG_DISTANCE",
    "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "mode": "FLIGHT",
    "origin": "Hyderabad",
    "destination": CITY,
    "start_at": at(0, 11),
    "end_at": at(0, 13),
}

# =========================================================================
section("raising requests")
# =========================================================================
r = raise_request(RAVI, LONG_HAUL)
check("ground staff raises a flight request", r.status_code == 201, r.text[:300])
flight = r.json()
flight_id = flight["id"]
check("status derives to SUBMITTED", flight["status"] == "SUBMITTED", flight["status"])
check("requester is a traveller on their own request", len(flight["travellers"]) == 1)
check("a fresh request is editable", flight["is_editable"] is True)
check("no conflicts on a clear calendar", flight["conflicts"] == [], flight["conflicts"])

r = raise_request(
    RAVI,
    {
        "request_type": "LONG_DISTANCE",
        "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
        "origin": "Hyderabad",
        "destination": "Goa",
        "start_at": at(40, 9),
    },
)
check("long distance without a mode is rejected", r.status_code == 422, r.status_code)

r = raise_request(
    RAVI,
    {"request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "hotel_city": CITY},
)
check("hotel without a check-in is rejected", r.status_code == 422, r.status_code)

r = raise_request(
    RAVI,
    {
        "request_type": "HOTEL",
        "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
        "hotel_city": CITY,
        "check_in": day(10),
        "check_out": day(8),
    },
)
check("check-out before check-in is rejected", r.status_code == 422, r.status_code)

# =========================================================================
section("group requests")
# =========================================================================
r = raise_request(
    ARJUN,
    {
        "request_type": "LONG_DISTANCE",
        "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
        "mode": "TRAIN",
        "origin": "Pune",
        "destination": "Nagpur",
        "start_at": at(20, 7),
        "end_at": at(20, 19),
        "traveller_ids": [meera_id, sam_id],
    },
)
check("a group request is raised", r.status_code == 201, r.text[:300])
group = r.json()
group_id = group["id"]
check("every tagged person gets their own row", len(group["travellers"]) == 3, len(group["travellers"]))
check(
    "each traveller row carries its own status",
    all(t["status"] == "PENDING" for t in group["travellers"]),
)
check(
    "the raiser is marked as the requester",
    sum(1 for t in group["travellers"] if t["is_requester"]) == 1,
)

r = c.get(f"/requests/{group_id}", headers=MEERA)
check("a tagged co-traveller can see the request", r.status_code == 200, r.status_code)

r = c.put(f"/requests/{group_id}", headers=MEERA, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "TRAIN",
    "origin": "Pune", "destination": "Nagpur", "start_at": at(20, 7),
})
check("a co-traveller cannot edit someone else's request", r.status_code == 403, r.status_code)

r = raise_request(RAVI, {**LONG_HAUL, "traveller_ids": [999999]})
check("tagging an unknown colleague is rejected", r.status_code == 400, r.status_code)

# =========================================================================
section("drafts are private")
# =========================================================================
r = raise_request(RAVI, {**LONG_HAUL, "start_at": at(60, 6), "end_at": at(60, 8), "is_draft": True})
check("a draft is created", r.status_code == 201 and r.json()["status"] == "DRAFT", r.status_code)
draft_id = r.json()["id"]

check("the owner can read their draft", c.get(f"/requests/{draft_id}", headers=RAVI).status_code == 200)
check(
    "an admin cannot read someone else's draft",
    c.get(f"/requests/{draft_id}", headers=AH).status_code == 404,
)
check(
    "a draft is absent from the admin list",
    all(
        item["id"] != draft_id
        for item in c.get("/requests", headers=AH, params={"mine": False, "page_size": 100}).json()["items"]
    ),
)
check("a draft has no revisions yet", c.get(f"/requests/{draft_id}/revisions", headers=RAVI).json() == [])

r = c.post(f"/requests/{draft_id}/submit", headers=RAVI)
check("a draft submits", r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.status_code)
check(
    "submission opens the revision trail at 1",
    [rev["revision_number"] for rev in c.get(f"/requests/{draft_id}/revisions", headers=RAVI).json()] == [1],
)
check(
    "a submitted draft becomes visible to admins",
    c.get(f"/requests/{draft_id}", headers=AH).status_code == 200,
)
check("submitting twice is refused", c.post(f"/requests/{draft_id}/submit", headers=RAVI).status_code == 409)

# =========================================================================
section("conflict detection - warns, never blocks (addendum B6)")
# =========================================================================
r = c.post("/requests/check", headers=RAVI, json={**LONG_HAUL, "start_at": at(0, 12), "end_at": at(0, 15)})
check("the dry-run endpoint answers", r.status_code == 200, r.text[:200])
check("an overlapping journey is flagged before saving", len(r.json()["conflicts"]) == 1, r.json())

r = raise_request(RAVI, {**LONG_HAUL, "mode": "TRAIN", "destination": "Pune", "start_at": at(0, 12), "end_at": at(0, 15)})
check("the overlapping request is still accepted", r.status_code == 201, r.status_code)
clash = r.json()
check("the warning comes back with the created request", len(clash["conflicts"]) == 1, clash["conflicts"])
check("the conflict is a warning, not a block", clash["conflicts"][0]["severity"] == "WARNING")
check("the conflict is typed as overlapping travel", clash["conflicts"][0]["kind"] == "OVERLAPPING_TRAVEL")
check("the warning names the other trip", clash["conflicts"][0]["other_request_id"] == flight_id)
overlap_id = clash["id"]

r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "LOCAL_CAB", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "origin_state": "Telangana", "pickup_city": "Hyderabad", "destination_state": "Telangana", "drop_city": "Hyderabad",
    "origin": "Banjara Hills", "destination": "RGIA Airport",
    "start_at": at(0, 8), "end_at": at(0, 10),
})
check(
    "a cab on the day of your own flight does NOT warn",
    r.json()["conflicts"] == [],
    r.json()["conflicts"],
)

r = c.post("/requests/check", headers=RAVI, json={**LONG_HAUL})
check(
    "the same journey on the same day is called a duplicate",
    any(x["kind"] == "DUPLICATE_REQUEST" for x in r.json()["conflicts"]),
    r.json()["conflicts"],
)

r = c.post("/requests/check", headers=ARJUN, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Mumbai", "destination": "Delhi",
    "start_at": at(20, 10), "end_at": at(20, 12),
    "traveller_ids": [meera_id],
})
conflicted = {x["user_id"] for x in r.json()["conflicts"]}
check(
    "every co-traveller is checked, not just the requester",
    conflicted == {arjun_id, meera_id},
    conflicted,
)

r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(0), "check_out": day(3),
})
check("a hotel does not conflict with your own flight", r.json()["conflicts"] == [], r.json()["conflicts"])

# =========================================================================
section("co-stay matching (SOW 3, addendum B7 / C2)")
# =========================================================================
r = raise_request(ARJUN, {
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(0), "check_out": day(4),
})
check("Arjun books a hotel in that city", r.status_code == 201, r.text[:200])
arjun_hotel_id = r.json()["id"]

r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(1), "check_out": day(3),
})
offers = r.json()["costay_matches"]
check("a same-gender colleague in that city is offered", len(offers) == 1, offers)
check("the offer names the colleague (C2 as built)", offers and offers[0]["full_name"] == "Arjun Nair")
check("the offer shows their designation (C2 as built)", offers and offers[0]["designation"] == "EXECUTIVE")
check("the offer counts the shared nights", offers and offers[0]["overlapping_nights"] == 2, offers)

r = c.post("/requests/check", headers=MEERA, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(1), "check_out": day(3),
})
check("a different-gender colleague is never offered", r.json()["costay_matches"] == [], r.json()["costay_matches"])

r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(4), "check_out": day(6),
})
check(
    "a stay starting the day they check out shares no night",
    r.json()["costay_matches"] == [],
    r.json()["costay_matches"],
)

r = raise_request(RAVI, {
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(1), "check_out": day(3),
})
check("Ravi raises his own stay in the same city", r.status_code == 201, r.text[:200])
stay = r.json()
stay_id = stay["id"]
ravi_traveller_id = stay["travellers"][0]["id"]
check("the offer is repeated on the saved request", len(stay["costay_matches"]) == 1)

r = c.post(f"/requests/{stay_id}/room-sharing", headers=RAVI, json={
    "traveller_id": ravi_traveller_id, "choice": "SHARE_EXISTING", "share_with_user_id": arjun_id,
})
check("the requester can choose to share", r.status_code == 200, r.text[:300])
shared = r.json()["travellers"][0]
check("the choice is recorded", shared["room_sharing"] == "SHARE_EXISTING")
check("a share is NOT confirmed by the requester alone (C2)", shared["share_confirmed"] is False)

notes = c.get("/notifications/mine", headers=ARJUN).json()
check("the colleague is notified (C2)", any(n["kind"] == "COSTAY_REQUESTED" for n in notes), notes)

r = c.post(f"/requests/{stay_id}/room-sharing", headers=RAVI, json={
    "traveller_id": ravi_traveller_id, "choice": "SHARE_EXISTING", "share_with_user_id": meera_id,
})
check("a cross-gender share is refused even if posted directly", r.status_code == 400, r.status_code)

r = c.post(f"/requests/{stay_id}/travellers/{ravi_traveller_id}/confirm-share", headers=RAVI)
check("ground staff cannot confirm their own share", r.status_code == 403, r.status_code)

r = c.post(f"/requests/{stay_id}/travellers/{ravi_traveller_id}/confirm-share", headers=AH)
check("an admin confirms the share (C2)", r.status_code == 200, r.text[:200])
check("the share now reads as confirmed", r.json()["travellers"][0]["share_confirmed"] is True)

# =========================================================================
section("the edit window and revision history (addendum A1)")
# =========================================================================
r = c.put(f"/requests/{stay_id}", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(1), "check_out": day(5),
    "notes": "Staying an extra night",
})
check("an unlocked request edits", r.status_code == 200, r.text[:300])
check("the edit counter moves", r.json()["edit_count"] == 1, r.json()["edit_count"])
check(
    "moving the dates drops the confirmed share",
    r.json()["travellers"][0]["room_sharing"] == "NOT_OFFERED",
    r.json()["travellers"][0],
)

history = c.get(f"/requests/{stay_id}/revisions", headers=RAVI).json()
check("the revision history has two entries", len(history) == 2, len(history))
check("revision 1 is the submission", history[-1]["summary"] == "Raised")
latest = history[0]
check("the latest revision names its editor", latest["editor_name"] == "Ravi Kumar", latest)
check(
    "the revision carries a before and after",
    latest["changes"]["check_out"]["from"] == day(3)
    and latest["changes"]["check_out"]["to"] == day(5),
    latest["changes"],
)
check("the revision summary is readable", "check-out" in latest["summary"], latest["summary"])

r = c.put(f"/requests/{stay_id}", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": CITY, "check_in": day(1), "check_out": day(5),
    "notes": "Staying an extra night",
})
check(
    "an edit that changes nothing writes no revision",
    r.json()["edit_count"] == 1,
    r.json()["edit_count"],
)

r = c.put(f"/requests/{group_id}", headers=ARJUN, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "TRAIN",
    "origin": "Pune", "destination": "Nagpur",
    "start_at": at(20, 7), "end_at": at(20, 19),
    "traveller_ids": [meera_id],
})
check("dropping a co-traveller is an edit", r.status_code == 200, r.text[:300])
check("the traveller row goes with them", len(r.json()["travellers"]) == 2, len(r.json()["travellers"]))
group_history = c.get(f"/requests/{group_id}/revisions", headers=ARJUN).json()
check(
    "the diff records who was on it before",
    "travellers" in (group_history[0]["changes"] or {}),
    group_history[0],
)

# The lock: an admin decision closes the window. Phase 4 owns the approval
# endpoints, so until they exist the decision is made straight against the
# database - the only place in this script that goes round the API.
from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

with engine.begin() as conn:
    conn.execute(
        text(
            "UPDATE request_travellers SET status='APPROVED' "
            "WHERE request_id=:rid ORDER BY id LIMIT 1"
        ),
        {"rid": stay_id},
    )

r = c.get(f"/requests/{stay_id}", headers=RAVI)
check("one approval locks the request", r.json()["is_editable"] is False, r.json()["is_editable"])
check("the request status becomes APPROVED", r.json()["status"] == "APPROVED", r.json()["status"])

r = c.put(f"/requests/{stay_id}", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": "Pune", "check_in": day(1), "check_out": day(5),
})
check("editing a locked request is refused", r.status_code == 409, r.status_code)
check("the refusal explains the lock", "locked" in r.json().get("detail", ""), r.json().get("detail"))

r = c.post(f"/requests/{stay_id}/room-sharing", headers=RAVI, json={
    "traveller_id": ravi_traveller_id, "choice": "SEPARATE_ROOM",
})
check("room sharing cannot be changed after the lock either", r.status_code == 409, r.status_code)

check(
    "the locked request kept its history",
    len(c.get(f"/requests/{stay_id}/revisions", headers=RAVI).json()) == 2,
)

# =========================================================================
section("cancellation - the escape hatch from a locked request")
# =========================================================================
r = c.post(f"/requests/{overlap_id}/cancel", headers=RAVI, json={"reason": "Trip called off"})
check("a request cancels with a reason", r.status_code == 200, r.text[:200])
check("status becomes CANCELLED", r.json()["status"] == "CANCELLED", r.json()["status"])
check("every traveller row is cancelled too", all(t["status"] == "CANCELLED" for t in r.json()["travellers"]))

r = c.post(f"/requests/{flight_id}/cancel", headers=RAVI, json={"reason": "x"})
check("a cancellation reason is required to be meaningful", r.status_code == 422, r.status_code)

r = c.post("/requests/check", headers=RAVI, json={**LONG_HAUL, "mode": "TRAIN", "destination": "Pune", "start_at": at(0, 12), "end_at": at(0, 15)})
kinds = {x["other_request_id"] for x in r.json()["conflicts"]}
check(
    "a cancelled trip frees the calendar",
    overlap_id not in kinds,
    kinds,
)

r = c.post(f"/requests/{stay_id}/cancel", headers=RAVI, json={"reason": "Plans changed after approval"})
check("even a locked request can be cancelled", r.status_code == 200, r.status_code)

# =========================================================================
section("listing and visibility")
# =========================================================================
r = c.get("/requests", headers=RAVI, params={"page_size": 100})
check("my requests lists only what I am on", r.status_code == 200, r.status_code)
mine = r.json()["items"]
check("the list carries derived status", all("status" in item for item in mine))
check(
    "the list carries the edit counter for the admin queue",
    any(item["edit_count"] > 0 for item in mine),
)
check(
    "another person's requests are absent",
    all(item["requester_id"] == ravi_id or any(t["user_id"] == ravi_id for t in item["travellers"]) for item in mine),
)

r = c.get("/requests", headers=AH, params={"mine": False, "page_size": 100})
check("an admin sees the whole tenant", r.json()["total"] >= len(mine), r.json()["total"])

r = c.get("/requests", headers=AH, params={"mine": False, "status": "CANCELLED", "page_size": 100})
check(
    "filtering by derived status works",
    all(item["status"] == "CANCELLED" for item in r.json()["items"]) and r.json()["total"] >= 1,
    r.json()["total"],
)

r = c.get("/requests", headers=AH, params={"mine": False, "type": "HOTEL", "page_size": 100})
check("filtering by type works", all(item["request_type"] == "HOTEL" for item in r.json()["items"]))

r = c.get(f"/requests/{arjun_hotel_id}", headers=MEERA)
check("someone not on a request cannot read it", r.status_code == 404, r.status_code)

# =========================================================================
section("the ledger")
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
entries = r.json()["items"]
actions = {e["action"] for e in entries if e["entity_type"] in ("travel_request", "request_traveller")}
check("submissions are audited", "SUBMIT" in actions, actions)
check("edits are audited", "UPDATE" in actions, actions)
check("cancellations are audited", "CANCEL" in actions, actions)

r = c.get("/audit/verify", headers=AH)
check("the audit chain is still intact", r.json()["ok"] is True, r.json())

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All Phase 3 checks passed.")
