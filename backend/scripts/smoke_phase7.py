"""
Phase 7 end-to-end smoke test.

Covers cost entry, even splitting, and the campaign financials and cost
analytics of SOW sections 2 and 6, against a running server and a real MySQL.

The checks that matter most are arithmetic and access. A split must sum to
exactly what was entered - 1,000 rupees across three people is 333.34 + 333.33 +
333.33, never 999.99 - and a booked trip with no fare recorded must be reported
as *uncosted* rather than quietly counted as free. Cost is admin-only to read as
well as to write.

    ./.venv/Scripts/python.exe scripts/smoke_phase7.py

Creates throwaway accounts, campaigns and requests, so point it at a dev database.
"""
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=60)
failures = []

STAMP = uuid.uuid4().hex[:8]
PASSWORD = "GroundStaff@123"
CITY = f"Bhopal {STAMP[:4].upper()}"
BASE = date.today() + timedelta(days=45)


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def money(value) -> Decimal:
    return Decimal(str(value))


r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_staff(name):
    address = f"p7.{name.split()[0].lower()}.{STAMP}@designboxed.com"
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
    return login.json()["user"]["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


section("cast")
ravi_id, RAVI = make_staff("Ravi Kumar")
arjun_id, ARJUN = make_staff("Arjun Nair")
deepak_id, DEEPAK = make_staff("Deepak Shah")
check("three ground-staff accounts created", all([ravi_id, arjun_id, deepak_id]))

code = f"P7-{STAMP[:6].upper()}"
r = c.post("/projects", headers=AH, json={
    "name": "Phase 7 Cost Run", "code": code, "state": f"Madhya Pradesh {STAMP[:4].upper()}",
})
project_id = r.json()["id"]
check("campaign created", r.status_code == 201, r.status_code)


def raise_group(traveller_ids=(), days_out=0, kind="LONG_DISTANCE"):
    start = datetime.combine(BASE + timedelta(days=days_out), datetime.min.time())
    body = {"request_type": kind, "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "traveller_ids": list(traveller_ids)}
    if kind == "HOTEL":
        body |= {
            "hotel_city": CITY,
            "check_in": (BASE + timedelta(days=days_out)).isoformat(),
            "check_out": (BASE + timedelta(days=days_out + 2)).isoformat(),
        }
    else:
        body |= {
            "mode": "FLIGHT", "origin": "Hyderabad", "destination": CITY,
            "start_at": start.replace(hour=7).isoformat(),
            "end_at": start.replace(hour=9).isoformat(),
        }
    return c.post("/requests", headers=RAVI, json=body).json()


def book_all(request, reference="6E-0001"):
    """Approve then book every traveller - spend only counts BOOKED rows."""
    for traveller in request["travellers"]:
        c.post(f"/requests/{request['id']}/travellers/{traveller['id']}/decide", headers=AH,
               json={"to_status": "APPROVED", "conflict_override_reason": "Smoke run"})
        c.post(f"/requests/{request['id']}/travellers/{traveller['id']}/decide", headers=AH,
               json={"to_status": "BOOKED", "booking_reference": reference})
    return c.get(f"/requests/{request['id']}", headers=AH).json()


# =========================================================================
section("splitting a shared cost (addendum C1)")
# =========================================================================
shared = raise_group([arjun_id, deepak_id], days_out=0)
check("a three-person request is raised", len(shared["travellers"]) == 3, len(shared["travellers"]))
shared = book_all(shared, "6E-SHARED")
ids = [t["id"] for t in shared["travellers"]]

r = c.post(f"/requests/{shared['id']}/costs/preview", headers=AH,
           json={"total_amount": "1000.00", "traveller_ids": ids})
check("a split can be previewed before saving", r.status_code == 200, r.text[:200])
preview = r.json()
amounts = [money(row["amount"]) for row in preview["rows"]]
check("the awkward split is exact", [str(a) for a in amounts] == ["333.34", "333.33", "333.33"], amounts)
check("and the server says so", preview["sums_to_total"] is True)
check("the parts add up to the total", sum(amounts) == money("1000.00"), sum(amounts))
check(
    "the odd paisa goes to the requester, not a colleague",
    preview["rows"][0]["traveller_name"] == "Ravi Kumar" and amounts[0] > amounts[1],
    preview["rows"][0],
)

r = c.post(f"/requests/{shared['id']}/costs/split", headers=AH,
           json={"total_amount": "1000.00", "traveller_ids": ids, "note": "Shared cab from the airport"})
check("the split saves", r.status_code == 200, r.text[:300])
saved = r.json()["travellers"]
check(
    "each traveller carries their share",
    sum(money(t["cost_amount"]) for t in saved) == money("1000.00"),
    [t["cost_amount"] for t in saved],
)
check("the note explains the number", all("Shared cab" in (t["cost_note"] or "") for t in saved))
check("who entered it is recorded", all(t["cost_entered_by_name"] for t in saved))

# The manual override on top of the even split - the other half of C1.
r = c.post(f"/requests/{shared['id']}/costs", headers=AH, json={"amounts": [
    {"traveller_id": ids[0], "amount": "500.00", "note": "Took a separate cab"},
    {"traveller_id": ids[1], "amount": "250.00"},
    {"traveller_id": ids[2], "amount": "250.00"},
]})
check("an admin can override the split by hand", r.status_code == 200, r.text[:200])
overridden = {t["id"]: t for t in r.json()["travellers"]}
check("the override sticks", money(overridden[ids[0]]["cost_amount"]) == money("500.00"))
check("and keeps its own note", "separate cab" in (overridden[ids[0]]["cost_note"] or ""))

# =========================================================================
section("what cost entry refuses")
# =========================================================================
r = c.post(f"/requests/{shared['id']}/costs", headers=RAVI,
           json={"amounts": [{"traveller_id": ids[0], "amount": "1.00"}]})
check("ground staff cannot record a cost", r.status_code == 403, r.status_code)

r = c.post(f"/requests/{shared['id']}/costs", headers=AH,
           json={"amounts": [{"traveller_id": ids[0], "amount": "-5.00"}]})
check("a negative amount is rejected", r.status_code == 422, r.status_code)

r = c.post(f"/requests/{shared['id']}/costs", headers=AH,
           json={"amounts": [{"traveller_id": ids[0], "amount": "10.005"}]})
check("fractions of a paisa are rejected", r.status_code == 422, r.status_code)

r = c.post(f"/requests/{shared['id']}/costs", headers=AH,
           json={"amounts": [{"traveller_id": 999999, "amount": "10.00"}]})
check("a traveller from another request is rejected", r.status_code == 404, r.status_code)

r = c.post(f"/requests/{shared['id']}/costs/split", headers=AH,
           json={"total_amount": "0", "traveller_ids": ids})
check("splitting nothing is rejected", r.status_code == 422, r.status_code)

rejected_req = raise_group([arjun_id], days_out=20)
rejected_tid = rejected_req["travellers"][1]["id"]
c.post(f"/requests/{rejected_req['id']}/travellers/{rejected_tid}/decide", headers=AH,
       json={"to_status": "REJECTED", "reason": "Not needed"})
r = c.post(f"/requests/{rejected_req['id']}/costs", headers=AH,
           json={"amounts": [{"traveller_id": rejected_tid, "amount": "900.00"}]})
check("a rejected traveller cannot be charged", r.status_code == 409, r.status_code)
check(
    "and the refusal says why",
    "no cost to record" in r.json().get("detail", ""),
    r.json().get("detail"),
)

# =========================================================================
section("cost is admin-only to read")
# =========================================================================
r = c.get(f"/requests/{shared['id']}", headers=RAVI)
staff_view = r.json()["travellers"]
check(
    "ground staff never see a cost, even on their own request",
    all(t["cost_amount"] is None for t in staff_view),
    [t["cost_amount"] for t in staff_view],
)
check(
    "nor who entered it",
    all(t["cost_entered_by_name"] is None for t in staff_view),
)

r = c.get(f"/requests/{shared['id']}", headers=AH)
check(
    "admins do see it",
    any(t["cost_amount"] is not None for t in r.json()["travellers"]),
)

r = c.get("/analytics", headers=RAVI)
check("ground staff cannot read the analytics", r.status_code == 403, r.status_code)

# =========================================================================
section("campaign financials (SOW 2)")
# =========================================================================
hotel = book_all(raise_group([], days_out=5, kind="HOTEL"), "HTL-0007")
c.post(f"/requests/{hotel['id']}/costs", headers=AH, json={"amounts": [
    {"traveller_id": hotel["travellers"][0]["id"], "amount": "4500.00"},
]})

r = c.get("/analytics/campaigns", headers=AH)
check("campaign financials load", r.status_code == 200, r.text[:200])
mine = next((row for row in r.json() if row["code"] == code), None)
check("this campaign appears", mine is not None, code)
if mine:
    # 500 + 250 + 250 from the override, plus the 4500 hotel.
    check("its spend is the sum of its travellers", money(mine["spent"]) == money("5500.00"), mine["spent"])
    check("it counts the trips", mine["trips"] >= 2, mine["trips"])
    check("and the people", mine["travellers"] >= 3, mine["travellers"])
    check("with nothing uncosted yet", mine["uncosted"] == 0, mine["uncosted"])

# =========================================================================
section("uncosted bookings - the honesty check")
# =========================================================================
blank = book_all(raise_group([], days_out=9), "6E-BLANK")
r = c.get("/analytics/campaigns", headers=AH)
mine = next(row for row in r.json() if row["code"] == code)
check("a booked trip with no fare is flagged", mine["uncosted"] == 1, mine["uncosted"])
check(
    "and does NOT make the campaign look cheaper",
    money(mine["spent"]) == money("5500.00"),
    mine["spent"],
)

r = c.get("/analytics/uncosted", headers=AH)
worklist = [row for row in r.json() if row["request_id"] == blank["id"]]
check("it appears on the admin worklist", len(worklist) == 1, len(worklist))
if worklist:
    check("naming who and which campaign", worklist[0]["project_code"] == code, worklist[0])
    check("and the booking reference to chase", worklist[0]["booking_reference"] == "6E-BLANK")

c.post(f"/requests/{blank['id']}/costs", headers=AH, json={"amounts": [
    {"traveller_id": blank["travellers"][0]["id"], "amount": "1200.00"},
]})
r = c.get("/analytics/campaigns", headers=AH)
mine = next(row for row in r.json() if row["code"] == code)
check("filling the fare clears the flag", mine["uncosted"] == 0, mine["uncosted"])
check("and adds to the spend", money(mine["spent"]) == money("6700.00"), mine["spent"])

# =========================================================================
section("cost analytics (SOW 6)")
# =========================================================================
r = c.get("/analytics", headers=AH)
check("the analytics bundle loads", r.status_code == 200, r.text[:200])
bundle = r.json()
check(
    "it arrives in one round trip",
    {"overview", "trend", "by_campaign", "by_type", "by_person", "by_state", "by_city",
     "deployment", "deployed_people", "uncosted"} <= set(bundle),
    list(bundle),
)

overview = bundle["overview"]
check("spend is reported", money(overview["spent"]) >= money("6700.00"), overview["spent"])
check("the currency is stated", overview["currency"] == "INR", overview["currency"])
check("booked travellers are counted", overview["booked_travellers"] >= 5, overview["booked_travellers"])

by_type = {row["request_type"]: row for row in bundle["by_type"]}
check("every request type appears, even at zero", len(by_type) == 3, list(by_type))
check("hotel spend is separated out", money(by_type["HOTEL"]["spent"]) >= money("4500.00"), by_type["HOTEL"])




def month_start(offset):
    """The first day of the month `offset` months from this one."""
    total = date.today().year * 12 + date.today().month - 1 + offset
    return date(total // 12, total % 12 + 1, 1)


# Seven whole months, three back and three ahead: long enough to chart by month.
window = {"since": str(month_start(-3)), "until": str(month_start(4) - timedelta(days=1))}
trend = c.get("/analytics", headers=AH, params=window).json()
months = trend["trend"]
check("a long window is charted by month", trend["grain"] == "month", trend["grain"])
check("monthly spend covers the chosen window", len(months) == 7, len(months))
check("oldest month first", months[0]["period"] <= months[-1]["period"], [m["period"] for m in months])
this_month = f"{date.today().year:04d}-{date.today().month:02d}"
check(
    "the window runs past today, so booked future travel is visible",
    this_month in [m["period"] for m in months] and months[-1]["period"] > this_month,
    [m["period"] for m in months],
)
check(
    "quiet months appear as zero rather than as gaps",
    all("spent" in m for m in months),
)

deployed = {row["location"]: row for row in bundle["deployment"]}
here = f"Madhya Pradesh {STAMP[:4].upper()}"
check("deployed staff are grouped by campaign location", here in deployed, list(deployed)[:5])
if here in deployed:
    check("counting distinct people", deployed[here]["people"] >= 3, deployed[here])

# A committed cost is a forecast, not spend.
future = raise_group([], days_out=30)
ftid = future["travellers"][0]["id"]
c.post(f"/requests/{future['id']}/travellers/{ftid}/decide", headers=AH,
       json={"to_status": "APPROVED", "conflict_override_reason": "Smoke run"})
c.post(f"/requests/{future['id']}/costs", headers=AH,
       json={"amounts": [{"traveller_id": ftid, "amount": "3000.00"}]})

r = c.get("/analytics", headers=AH)
after = r.json()["overview"]
check(
    "an approved-but-unticketed cost is committed, not spent",
    money(after["committed"]) >= money("3000.00")
    and money(after["spent"]) == money(overview["spent"]),
    {"spent": after["spent"], "committed": after["committed"]},
)

# =========================================================================
section("the ledger")
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
entries = r.json()["items"]
cost_rows = [
    e for e in entries
    if e["entity_type"] == "travel_request" and "cost" in (e["summary"] or "").lower()
]
check("recording a cost is audited", len(cost_rows) >= 1, len(cost_rows))
split_rows = [e for e in entries if "split" in (e["summary"] or "").lower()]
check("so is a split, with the total", len(split_rows) >= 1, split_rows[:1])
check(
    "and the before/after of each share",
    any("costs" in (e.get("changes") or {}) for e in cost_rows),
    cost_rows[0].get("changes") if cost_rows else None,
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
print("All Phase 7 checks passed.")
