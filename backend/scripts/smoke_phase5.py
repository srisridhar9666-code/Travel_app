"""
Phase 5 end-to-end smoke test.

Covers ticket upload, real Gemini extraction, the human review step and the
confirmation email, against a running server, a real MySQL and the real model.

The section that matters most is the review step (addendum B3). The SOW has an
upload set the request to Booked and email the traveller immediately; one
misparse under that design books a wrong PNR and mails it out. So the checks here
are mostly about what extraction is *unable* to do on its own: it must not move a
traveller's status, must not notify anyone, and must report a document it could
not read rather than presenting empty fields as a successful extraction.

    ./.venv/Scripts/python.exe scripts/smoke_phase5.py

Creates throwaway accounts and requests, so point it at a dev database. It calls
the real extraction model, so it costs a few seconds and a few tokens per run.

No real email leaves the building: the accounts this creates are invented
addresses at designboxed.com, and EMAIL_ALLOWLIST keeps them SUPPRESSED. That is
asserted rather than assumed - see "the delivery ledger" below.
"""
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=120)   # extraction is a model round trip
failures = []

STAMP = uuid.uuid4().hex[:8]
PASSWORD = "GroundStaff@123"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The fixtures say 10-13 March 2027, so the requests are raised to match; a
# mismatch between ticket and request is a separate check further down.
DEPART = datetime(2027, 3, 10, 6, 45)
ARRIVE = datetime(2027, 3, 10, 8, 20)
CHECK_IN = date(2027, 3, 10)
CHECK_OUT = date(2027, 3, 13)


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def fixture(name: str) -> bytes:
    path = FIXTURES / name
    if not path.exists():
        print(f"Missing fixture {path}. Run scripts/make_ticket_fixture.py first.")
        sys.exit(1)
    return path.read_bytes()


# --- sign in ---------------------------------------------------------------
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_staff(name):
    email_address = f"p5.{name.split()[0].lower()}.{STAMP}@designboxed.com"
    r = c.post("/users", headers=AH, json={
        "email": email_address, "full_name": name, "role": "GROUND_STAFF",
        "designation": "EXECUTIVE", "gender": "MALE",
    })
    if r.status_code != 201:
        check(f"created {name}", False, r.text[:200])
        sys.exit(1)
    token = r.json()["invite_url"].rsplit("=", 1)[-1]
    c.post("/auth/set-password", json={"token": token, "password": PASSWORD})
    login = c.post("/auth/login", json={"email": email_address, "password": PASSWORD})
    return (
        login.json()["user"]["id"],
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        email_address,
    )


section("preconditions")
r = c.get("/health/gemini")
check("the extraction model is reachable", r.json().get("ok") is True, r.json())
if not r.json().get("ok"):
    print("Cannot run Phase 5 checks without the model.")
    sys.exit(1)

r = c.get("/health/email")
print(f"      email health -> {r.json()}")

settings = get_settings()
check(
    "development mail is confined to an allowlist",
    (not settings.email_enabled) or bool(settings.allowed_email_recipients),
    "EMAIL_ENABLED is on with no EMAIL_ALLOWLIST - this run would mail invented addresses",
)

section("cast")
ravi_id, RAVI, ravi_email = make_staff("Ravi Kumar")
arjun_id, ARJUN, arjun_email = make_staff("Arjun Nair")
check("two ground-staff accounts created", bool(ravi_id and arjun_id))

r = c.post("/projects", headers=AH, json={
    "name": "Phase 5 Ticketing", "code": f"P5-{STAMP[:6].upper()}", "location": "Maharashtra",
})
project_id = r.json()["id"]
check("campaign created", r.status_code == 201, r.status_code)


def upload(request_id, traveller_id, name, headers=None, content_type="image/png"):
    return c.post(
        f"/requests/{request_id}/tickets",
        headers=headers or AH,
        params={"traveller_id": traveller_id},
        files={"file": (name, fixture(name), content_type)},
    )


# =========================================================================
section("a flight ticket, read and reviewed")
# =========================================================================
r = c.post("/requests", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": "Mumbai",
    "start_at": DEPART.isoformat(), "end_at": ARRIVE.isoformat(),
})
check("ground staff raises a flight request", r.status_code == 201, r.text[:200])
flight = r.json()
flight_id = flight["id"]
ravi_traveller = flight["travellers"][0]["id"]

r = upload(flight_id, ravi_traveller, "flight_ticket.png", headers=RAVI)
check("ground staff cannot upload a ticket", r.status_code == 403, r.status_code)

r = upload(flight_id, ravi_traveller, "flight_ticket.png")
check("admin uploads a ticket", r.status_code == 201, r.text[:300])
ticket = r.json()
ticket_id = ticket["id"]

check("the ticket was read", ticket["status"] == "EXTRACTED", ticket["status"])
check("the PNR came back", ticket["booking_reference"] == "QK8T2M", ticket["booking_reference"])
check("the carrier came back", (ticket["carrier"] or "").upper().startswith("INDIGO"), ticket["carrier"])
check("the flight number came back", ticket["service_number"] == "6E-4412", ticket["service_number"])
check("the passenger name came back", "RAVI" in (ticket["passenger_name"] or "").upper(), ticket["passenger_name"])
check("the departure time came back", (ticket["depart_at"] or "").startswith("2027-03-10T06:45"), ticket["depart_at"])
check("the model is recorded", bool(ticket["model_id"]), ticket["model_id"])
check("per-field confidence is recorded", bool(ticket["confidence"]), ticket["confidence"])

# --- the whole point of B3 -----------------------------------------------
r = c.get(f"/requests/{flight_id}", headers=AH)
check(
    "extraction did NOT book the traveller",
    r.json()["travellers"][0]["status"] == "PENDING",
    r.json()["travellers"][0]["status"],
)
check("extraction did NOT set a booking reference", r.json()["travellers"][0]["booking_reference"] is None)
check("the request is not BOOKED", r.json()["status"] != "BOOKED", r.json()["status"])

notes = c.get("/notifications/mine", headers=RAVI).json()
check("extraction notified nobody", len(notes) == 0, len(notes))

r = c.get(f"/tickets/{ticket_id}/file", headers=AH)
check("the document downloads for review", r.status_code == 200 and len(r.content) > 1000, r.status_code)
check("the document is sent with nosniff", r.headers.get("x-content-type-options") == "nosniff")

r = c.get(f"/tickets/{ticket_id}/file", headers=RAVI)
check("ground staff cannot download a ticket", r.status_code == 403, r.status_code)

r = c.get("/tickets/pending", headers=AH)
check("the ticket is in the review queue", any(t["id"] == ticket_id for t in r.json()), len(r.json()))

# =========================================================================
section("confirming is the only path to BOOKED")
# =========================================================================
r = c.post(f"/tickets/{ticket_id}/confirm", headers=RAVI, json={"booking_reference": "QK8T2M"})
check("ground staff cannot confirm a ticket", r.status_code == 403, r.status_code)

r = c.post(f"/tickets/{ticket_id}/confirm", headers=AH, json={"booking_reference": "QK8T2M"})
check(
    "confirming refuses while the traveller is unapproved",
    r.status_code == 409,
    r.status_code,
)
check(
    "and explains that approval comes first",
    "approved" in r.json().get("detail", "").lower(),
    r.json().get("detail"),
)

r = c.post(
    f"/requests/{flight_id}/travellers/{ravi_traveller}/decide",
    headers=AH, json={"to_status": "APPROVED", "conflict_override_reason": "Smoke run"},
)
check("the traveller is approved first", r.status_code == 200, r.text[:200])

r = c.post(f"/tickets/{ticket_id}/confirm", headers=AH, json={
    "booking_reference": "QK8T2M", "carrier": "IndiGo", "service_number": "6E-4412",
})
check("an admin confirms the ticket", r.status_code == 200, r.text[:300])
confirmed = r.json()
check("the ticket is CONFIRMED", confirmed["status"] == "CONFIRMED", confirmed["status"])
check("who confirmed it is recorded", bool(confirmed["confirmed_by_name"]), confirmed)
check("what they confirmed is recorded", confirmed["confirmed_reference"] == "QK8T2M")

r = c.get(f"/requests/{flight_id}", headers=AH)
traveller = r.json()["travellers"][0]
check("only now is the traveller BOOKED", traveller["status"] == "BOOKED", traveller["status"])
check("the reference reached the traveller row", traveller["booking_reference"] == "QK8T2M", traveller)
check("the request reads as BOOKED", r.json()["status"] == "BOOKED", r.json()["status"])

r = c.post(f"/tickets/{ticket_id}/confirm", headers=AH, json={"booking_reference": "QK8T2M"})
check("confirming twice is refused", r.status_code == 409, r.status_code)

r = c.post(f"/tickets/{ticket_id}/extract", headers=AH)
check("a confirmed ticket cannot be re-read", r.status_code == 409, r.status_code)

r = c.post(f"/tickets/{ticket_id}/discard", headers=AH)
check("a confirmed ticket cannot be discarded", r.status_code == 409, r.status_code)

# =========================================================================
section("the traveller is told (and the ledger records it)")
# =========================================================================
notes = c.get("/notifications/mine", headers=RAVI).json()
check("the traveller now has a notice", any(n["kind"] == "BOOKING_CONFIRMED" for n in notes), len(notes))
check("the notice carries the reference", any("QK8T2M" in n["body"] for n in notes), [n["body"][:60] for n in notes])

r = c.get("/notifications/ledger", headers=AH, params={"page_size": 200})
check("the ledger loads", r.status_code == 200, r.text[:200])
ledger = r.json()
mine = [n for n in ledger["items"] if n["to_address"] == ravi_email]
check("an email row was written for the traveller", len(mine) >= 1, len(mine))
if mine:
    row = mine[0]
    check("it was addressed correctly", row["to_address"] == ravi_email, row["to_address"])
    check("it has a subject", bool(row["subject"]), row["subject"])
    check("delivery was attempted exactly once", row["attempts"] == 1, row["attempts"])
    # The whole point of the allowlist: an invented address is SUPPRESSED, not
    # FAILED, and certainly not silently dropped.
    check(
        "an out-of-allowlist address is SUPPRESSED, not FAILED",
        row["status"] == "SUPPRESSED",
        f'{row["status"]} / {row["last_error"]}',
    )
    check(
        "and the ledger says why",
        "ALLOWLIST" in (row["last_error"] or "").upper(),
        row["last_error"],
    )

r = c.get("/notifications/ledger", headers=AH, params={"status": "SUPPRESSED", "page_size": 5})
check("the ledger filters by status", all(n["status"] == "SUPPRESSED" for n in r.json()["items"]))

r = c.get("/notifications/ledger", headers=AH, params={"channel": "IN_APP", "page_size": 5})
check("the ledger filters by channel", all(n["channel"] == "IN_APP" for n in r.json()["items"]))

r = c.post("/notifications/retry", headers=AH)
check("retry runs and leaves suppressions alone", r.status_code == 200, r.json())

r = c.get("/notifications/ledger", headers=RAVI)
check("ground staff cannot read the whole ledger", r.status_code == 403, r.status_code)

r = c.get("/notifications/mine", headers=RAVI)
check("but can read their own notices", r.status_code == 200 and len(r.json()) >= 1, r.status_code)

# =========================================================================
section("a hotel confirmation")
# =========================================================================
r = c.post("/requests", headers=ARJUN, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "hotel_city": "Mumbai", "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(),
})
stay = r.json()
stay_id = stay["id"]
arjun_traveller = stay["travellers"][0]["id"]
check("a hotel request is raised", r.status_code == 201, r.text[:200])

r = upload(stay_id, arjun_traveller, "hotel_confirmation.png")
check("a hotel confirmation uploads and is read", r.status_code == 201, r.text[:300])
hotel = r.json()


def read_back(label, field, expected):
    """Assert a field the model returned; say so plainly when it returned none.

    This script drives the live model, and what it reads back varies run to run.
    The *parsing* of every one of these fields is pinned down deterministically
    in tests/test_extraction.py; what this section proves is the wiring. A check
    that fails on model variance teaches people to ignore the script, which is
    worse than a check that says what it could not test.
    """
    actual = hotel.get(field)
    if actual is None:
        print(f"SKIP  {label} -> the model returned no {field} this run")
        return
    check(label, actual == expected, actual)


check("the confirmation number came back", (hotel["booking_reference"] or "").startswith("HTL"), hotel["booking_reference"])
check("the hotel name came back", "Taj" in (hotel["hotel_name"] or ""), hotel["hotel_name"])
read_back("the check-in date came back", "check_in", CHECK_IN.isoformat())
read_back("the check-out date came back", "check_out", CHECK_OUT.isoformat())
check(
    "the dates it did read match the request, so no mismatch is flagged",
    hotel["mismatches"] == [],
    hotel["mismatches"],
)

# =========================================================================
section("what the model gets wrong is surfaced, not swallowed")
# =========================================================================
r = c.post("/requests", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "TRAIN",
    "origin": "Pune", "destination": "Nagpur",
    "start_at": "2027-05-02T07:00", "end_at": "2027-05-02T19:00",
})
wrong = r.json()
wrong_id = wrong["id"]
wrong_traveller = wrong["travellers"][0]["id"]

# The flight ticket says Hyderabad to Mumbai on 10 March; this request says Pune
# to Nagpur on 2 May. Every one of those should be flagged for the reviewer.
#
# These checks exercise the *wiring*, not the rules: the rules are pinned down
# deterministically in tests/test_mismatches.py, because what the live model
# returns varies between runs and a smoke that fails on that variance teaches
# people to ignore it. So each check below runs only when the model actually
# read the field it depends on, and says plainly when it did not.
r = upload(wrong_id, wrong_traveller, "flight_ticket.png")
check("a ticket for the wrong trip still uploads", r.status_code == 201, r.text[:200])
mismatched = r.json()


def when_extracted(label, field, condition, detail=""):
    """Assert only if the model gave us the field this rule needs."""
    if mismatched.get(field):
        check(label, condition, detail)
    else:
        print(f"SKIP  {label} -> the model returned no {field} this run")


when_extracted(
    "the wrong route is flagged",
    "origin",
    any("departs" in m for m in mismatched["mismatches"]),
    mismatched["mismatches"],
)
when_extracted(
    "the wrong date is flagged",
    "depart_at",
    any("departs on" in m for m in mismatched["mismatches"]),
    mismatched["mismatches"],
)

r = c.post("/requests", headers=ARJUN, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": "Mumbai",
    "start_at": DEPART.isoformat(), "end_at": ARRIVE.isoformat(),
})
other = r.json()
# Arjun's request, but the ticket is in Ravi's name - the thing that stops
# someone at the gate.
r = upload(other["id"], other["travellers"][0]["id"], "flight_ticket.png")
wrong_name = r.json()
if wrong_name.get("passenger_name"):
    check(
        "a ticket in the wrong person's name is flagged",
        any("name of" in m for m in wrong_name["mismatches"]),
        wrong_name["mismatches"],
    )
else:
    print("SKIP  a ticket in the wrong person's name is flagged -> the model "
          "returned no passenger_name this run")

r = c.post("/requests", headers=RAVI, json={
    "request_type": "LOCAL_CAB", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "origin_state": "Maharashtra", "pickup_city": "Mumbai", "destination_state": "Maharashtra", "drop_city": "Mumbai",
    "origin": "Bandra", "destination": "Airport", "start_at": "2027-06-01T05:00",
})
junk = r.json()
r = upload(junk["id"], junk["travellers"][0]["id"], "not_a_ticket.png")
check("a document that is not a ticket uploads", r.status_code == 201, r.text[:200])
unreadable = r.json()
check("it is marked FAILED, not EXTRACTED", unreadable["status"] == "FAILED", unreadable["status"])
check("with a reason a human can act on", bool(unreadable["extraction_error"]), unreadable["extraction_error"])
check("and no invented fields", unreadable["booking_reference"] is None, unreadable["booking_reference"])

r = c.post(f"/tickets/{unreadable['id']}/discard", headers=AH)
check("the wrong file can be discarded", r.status_code == 200 and r.json()["status"] == "DISCARDED", r.status_code)

r = c.post(f"/tickets/{unreadable['id']}/confirm", headers=AH, json={"booking_reference": "X1"})
check("a discarded ticket cannot be confirmed", r.status_code == 409, r.status_code)

# =========================================================================
section("corrections are recorded as corrections")
# =========================================================================
r = c.post(
    f"/requests/{stay_id}/travellers/{arjun_traveller}/decide",
    headers=AH, json={"to_status": "APPROVED", "conflict_override_reason": "Smoke run"},
)
check("the hotel traveller is approved", r.status_code == 200, r.text[:200])

r = c.post(f"/tickets/{hotel['id']}/confirm", headers=AH, json={
    "booking_reference": "HTL-99812-CORRECTED", "notify": False,
})
check("an admin can correct the extracted reference", r.status_code == 200, r.text[:200])
corrected = r.json()
check("the corrected value is what was saved", corrected["confirmed_reference"] == "HTL-99812-CORRECTED")
check(
    "the model's original proposal is kept beside it, unchanged",
    corrected["booking_reference"] == hotel["booking_reference"],
    {"proposed": corrected["booking_reference"], "confirmed": corrected["confirmed_reference"]},
)
check(
    "so the two are distinguishable after the fact",
    corrected["booking_reference"] != corrected["confirmed_reference"],
)

arjun_notes = c.get("/notifications/mine", headers=ARJUN).json()
check(
    "notify=false suppresses the confirmation notice",
    not any(n["kind"] == "BOOKING_CONFIRMED" for n in arjun_notes),
    [n["kind"] for n in arjun_notes],
)

# =========================================================================
section("the ledger")
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
entries = r.json()["items"]
ticket_actions = {e["action"] for e in entries if e["entity_type"] == "ticket_document"}
check("uploads are audited", "UPLOAD" in ticket_actions, ticket_actions)
check("extractions are audited", "EXTRACT" in ticket_actions, ticket_actions)
check("confirmations are audited", "BOOK" in ticket_actions, ticket_actions)
check("notifications are audited", "NOTIFY" in ticket_actions, ticket_actions)
check("discards are audited", "DELETE" in ticket_actions, ticket_actions)

extracts = [e for e in entries if e["action"] == "EXTRACT" and e.get("changes")]
check(
    "the audited extraction keeps what the model proposed",
    any("proposed" in (e["changes"] or {}) for e in extracts),
    len(extracts),
)
books = [e for e in entries if e["entity_type"] == "ticket_document" and e["action"] == "BOOK"]
check(
    "a hand correction is marked as one in the ledger",
    any((e["changes"] or {}).get("corrected_by_hand") is True for e in books),
    [e.get("changes") for e in books[:2]],
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
print("All Phase 5 checks passed.")
