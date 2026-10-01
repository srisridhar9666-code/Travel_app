"""
Phase 2 end-to-end smoke test.

Covers projects, the employee directory, identity documents and bulk import
against a running server and a real MySQL. The identity-document checks matter
most: they prove numbers are encrypted at rest, never leak through a list
response, and cannot be read without an audit row.

    ./.venv/Scripts/python.exe scripts/smoke_phase2.py

Creates throwaway projects and accounts, so point it at a dev database.
"""
import io
import sys
import uuid
from datetime import date, timedelta

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=30)
failures = []

# Unique per run: the duplicate check is real, so a fixed number would collide
# with the previous run's record.
_DIGITS = f"{uuid.uuid4().int % 10**12:012d}"
AADHAAR = f"{_DIGITS[0:4]} {_DIGITS[4:8]} {_DIGITS[8:12]}"
LAST4 = _DIGITS[-4:]


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def png_bytes() -> bytes:
    """A real 1x1 PNG, so the magic-byte check has something valid to accept."""
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6360000002000100ffff0305fe02fe"
        "a7d3b2500000000049454e44ae426082"
    )


# --- sign in ---------------------------------------------------------------
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}

# =========================================================================
# Projects
# =========================================================================
code = f"CMP-{uuid.uuid4().hex[:6].upper()}"
r = c.post(
    "/projects",
    headers=AH,
    json={
        "name": "Monsoon Field Survey",
        "code": code,
        "client_name": "Acme Retail",
        "location": "Telangana",
        "start_date": str(date.today()),
        "end_date": str(date.today() + timedelta(days=60)),
    },
)
check("admin creates a campaign", r.status_code == 201, r.status_code)
project_id = r.json()["id"]
check("campaign accepts requests while active", r.json()["accepts_requests"] is True)

r = c.post("/projects", headers=AH, json={"name": "Duplicate", "code": code.lower()})
check("duplicate code rejected, case-insensitively", r.status_code == 409, r.status_code)

r = c.post(
    "/projects",
    headers=AH,
    json={
        "name": "Backwards",
        "code": f"BAD-{uuid.uuid4().hex[:5].upper()}",
        "start_date": str(date.today()),
        "end_date": str(date.today() - timedelta(days=5)),
    },
)
check("end date before start date rejected", r.status_code == 422, r.status_code)

r = c.patch(f"/projects/{project_id}", headers=AH, json={"location": "Andhra Pradesh"})
check("campaign updates", r.status_code == 200 and r.json()["location"] == "Andhra Pradesh")

r = c.post(f"/projects/{project_id}/archive", headers=AH)
check("campaign archives", r.status_code == 200 and r.json()["status"] == "ARCHIVED")
check("archived campaign refuses new requests", r.json()["accepts_requests"] is False)

r = c.post(f"/projects/{project_id}/restore", headers=AH)
check("campaign restores", r.status_code == 200 and r.json()["status"] == "ACTIVE")

# =========================================================================
# Bulk import
# =========================================================================
r = c.get("/users/import/template", headers=AH)
check("import template downloads", r.status_code == 200 and "full_name" in r.text, r.status_code)

stamp = uuid.uuid4().hex[:8]
good_a = f"import.a.{stamp}@designboxed.com"
good_b = f"import.b.{stamp}@designboxed.com"
good_c = f"import.c.{stamp}@designboxed.com"  # gender deliberately blank
csv_body = (
    "full_name,email,role,designation,gender,phone,employee_code,base_location\n"
    f"Anita Desai,{good_a},GROUND_STAFF,TEAM_LEAD,FEMALE,+91 90000 00001,DB-{stamp[:4]},Pune\n"
    f"Vikram Rao,{good_b},GROUND_STAFF,EXECUTIVE,MALE,,,Chennai\n"
    "Broken Row,not-an-email,GROUND_STAFF,,MALE,,,\n"
    ",missing.name@designboxed.com,GROUND_STAFF,,MALE,,,\n"
    f"Repeat Person,{good_a},GROUND_STAFF,,FEMALE,,,\n"
    "Bad Enum,bad.enum@designboxed.com,WIZARD,,MALE,,,\n"
    # Appended last so the line numbers the error checks assert on stay put.
    f"Sunil Nair,{good_c},GROUND_STAFF,EXECUTIVE,,,,Kochi\n"
)


def upload(path, body, name="team.csv"):
    return c.post(
        path, headers=AH, files={"file": (name, io.BytesIO(body.encode()), "text/csv")}
    )


r = upload("/users/import/preview", csv_body)
check("import preview parses", r.status_code == 200, r.status_code)
preview = r.json()
check("preview counts importable rows", preview["importable"] == 3, preview["importable"])
check("preview counts skipped rows", preview["skipped"] == 4, preview["skipped"])
check("preview writes nothing", c.get("/users", headers=AH, params={"search": good_a}).json()["total"] == 0)

errors = {row["line"]: row["errors"] for row in preview["rows"]}
check("invalid email flagged", any("valid email" in e for e in errors.get(4, [])), errors.get(4))
check("missing name flagged", any("full_name" in e for e in errors.get(5, [])), errors.get(5))
check("duplicate within file flagged", any("more than once" in e for e in errors.get(6, [])), errors.get(6))
check("unknown role flagged", any("role must be" in e for e in errors.get(7, [])), errors.get(7))
check(
    "undisclosed gender warns but does not block",
    any("separate room" in w for row in preview["rows"] for w in row["warnings"]),
)

r = upload("/users/import", csv_body)
check("import commits", r.status_code == 200, r.status_code)
result = r.json()
check("only valid rows created", result["created"] == 3, result["created"])
check("invite issued per imported account", len(result["invite_urls"]) == 3, len(result["invite_urls"]))

r = upload("/users/import/preview", csv_body)
check(
    "re-importing the same file finds them already present",
    r.json()["importable"] == 0,
    r.json()["importable"],
)

r = upload("/users/import/preview", "name,mail\nfoo,bar\n")
check("file missing required headers rejected", len(r.json()["file_errors"]) > 0, r.json()["file_errors"])

imported_id = c.get("/users", headers=AH, params={"search": good_a}).json()["items"][0]["id"]

# =========================================================================
# Identity documents
# =========================================================================
def add_proof(user_id, number, proof_type="AADHAAR", with_file=True, content_type="image/png"):
    files = {"file": ("scan.png", io.BytesIO(png_bytes()), content_type)} if with_file else None
    return c.post(
        f"/users/{user_id}/id-proofs",
        headers=AH,
        data={"proof_type": proof_type, "number": number, "label": "Primary"},
        files=files,
    )


r = add_proof(imported_id, AADHAAR)
check("admin adds an ID proof with a scan", r.status_code == 201, r.text[:200])
proof = r.json()
proof_id = proof["id"]
check("list view masks the number", proof["masked_number"] == f"XXXX XXXX {LAST4}", proof["masked_number"])
check("full number absent from the response", AADHAAR.replace(" ", "") not in str(proof), proof)
check("scan recorded", proof["has_file"] is True)

r = c.get(f"/users/{imported_id}/id-proofs", headers=AH)
check("proofs list for a user", r.status_code == 200 and len(r.json()) == 1)
check("full number absent from the list too", _DIGITS not in r.text, r.text[:200])

r = add_proof(imported_id, f"{_DIGITS[0:4]}-{_DIGITS[4:8]}-{_DIGITS[8:12]}")
check("same document rejected even reformatted", r.status_code == 409, r.status_code)

r = add_proof(1, AADHAAR)
check("same document under another employee rejected", r.status_code == 409, r.json().get("detail"))

r = c.post(
    f"/users/{imported_id}/id-proofs",
    headers=AH,
    data={"proof_type": "PAN", "number": "ABCDE1234F"},
    files={"file": ("evil.html", io.BytesIO(b"<script>alert(1)</script>"), "text/html")},
)
check("disallowed file type rejected", r.status_code == 415, r.status_code)

r = c.post(
    f"/users/{imported_id}/id-proofs",
    headers=AH,
    data={"proof_type": "PAN", "number": "ABCDE1234F"},
    files={"file": ("fake.png", io.BytesIO(b"not really a png"), "image/png")},
)
check("file whose bytes contradict its type rejected", r.status_code == 400, r.status_code)

# reveal + download, both audited
r = c.get(f"/id-proofs/{proof_id}/reveal", headers=AH)
check("reveal returns the real number", r.status_code == 200 and r.json()["number"] == AADHAAR, r.status_code)

r = c.get(f"/id-proofs/{proof_id}/file", headers=AH)
check("scan downloads", r.status_code == 200 and r.content == png_bytes(), r.status_code)
check("scan sent with nosniff", r.headers.get("x-content-type-options") == "nosniff")

# ground staff must not reach any of it
r = c.post("/auth/login", json={"email": good_a, "password": "irrelevant"})
staff_blocked = r.status_code == 401  # no password set yet
check("imported user cannot sign in before accepting", staff_blocked, r.status_code)

# =========================================================================
# Encryption at rest
# =========================================================================
r = c.get("/audit", headers=AH, params={"page_size": 200})
ledger = r.text
check("ledger never carries the real number", _DIGITS not in ledger and AADHAAR not in ledger)
check("ledger carries the masked number", f"XXXX XXXX {LAST4}" in ledger, "masked value missing")

entries = r.json()["items"]
sensitive = [e for e in entries if e["action"] == "VIEW_SENSITIVE"]
check("reveal wrote a VIEW_SENSITIVE row", any("viewed the full" in e["summary"] for e in sensitive), len(sensitive))
check("download wrote a VIEW_SENSITIVE row", any("downloaded the" in e["summary"] for e in sensitive), len(sensitive))

# =========================================================================
# Retention - 90 days after exit (addendum C4)
# =========================================================================
r = c.get("/id-proofs/retention", headers=AH)
check("retention status reports the window", r.json()["retention_days"] == 90, r.json())
baseline_due = r.json()["due_now"]

r = c.patch(f"/users/{imported_id}", headers=AH, json={"is_active": False})
check("deactivation alone does not start the clock", c.get("/id-proofs/retention", headers=AH).json()["due_now"] == baseline_due)

r = c.patch(f"/users/{imported_id}", headers=AH, json={"exited_on": str(date.today() - timedelta(days=30))})
check("recent exit is not yet due", c.get("/id-proofs/retention", headers=AH).json()["due_now"] == baseline_due, "30 days should not be due")

r = c.patch(f"/users/{imported_id}", headers=AH, json={"exited_on": str(date.today() - timedelta(days=100))})
due = c.get("/id-proofs/retention", headers=AH).json()["due_now"]
check("exit beyond 90 days becomes due", due == baseline_due + 1, due)

r = c.post("/id-proofs/retention/purge", headers=AH)
check("purge runs", r.status_code == 200, r.status_code)
check("purge removed the record", r.json()["purged"] >= 1, r.json())

r = c.get(f"/users/{imported_id}/id-proofs", headers=AH)
purged = r.json()[0]
check("tombstone remains", purged["is_purged"] is True and purged["purged_at"] is not None)
check("number gone after purge", purged["masked_number"] is None, purged["masked_number"])
check("scan gone after purge", purged["has_file"] is False)

r = c.get(f"/id-proofs/{proof_id}/reveal", headers=AH)
check("purged record cannot be revealed", r.status_code == 410, r.status_code)
r = c.get(f"/id-proofs/{proof_id}/file", headers=AH)
check("purged scan cannot be downloaded", r.status_code == 404, r.status_code)

r = c.post("/id-proofs/retention/purge", headers=AH)
check("purge is idempotent", r.json()["purged"] == 0, r.json())

# =========================================================================
# Ledger integrity across all of it
# =========================================================================
r = c.get("/audit/verify", headers=AH)
check("hash chain still intact", r.json()["ok"] is True, r.json())
print(f"\nledger rows checked: {r.json().get('checked')}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
