"""
Phase 8 end-to-end smoke test.

Covers the hardening and the audit viewer against a running server: security
headers on real responses, per-address rate limiting on the unauthenticated
endpoints, the append-only ledger probe (addendum B9), and the audit viewer's
entity history, summary and CSV export.

The checks that matter most are the ones about *refusal*. A header that is
absent, a rate limit that never trips and a ledger that can be rewritten all
look identical to a working system until the day they matter.

    ./.venv/Scripts/python.exe scripts/smoke_phase8.py

The rate-limit checks present their own `X-Forwarded-For` addresses rather
than resetting anything, so the script is safe to re-run immediately and never
locks the real admin out of the dev server.
"""
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core import ratelimit  # noqa: E402

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=30)
failures = []

STAMP = uuid.uuid4().hex[:8]


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


# =========================================================================
section("security headers on every response")
# =========================================================================
r = c.get("/health")
check("the API answers", r.status_code == 200, r.status_code)

for header, expected in (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    ("cross-origin-opener-policy", "same-origin"),
):
    check(f"{header} is set", r.headers.get(header) == expected, r.headers.get(header))

policy = r.headers.get("content-security-policy", "")
check("a content security policy is sent", "default-src 'none'" in policy, policy)
check("framing is refused in the policy too", "frame-ancestors 'none'" in policy, policy)
check(
    "permissions policy switches off hardware we never use",
    "camera=()" in r.headers.get("permissions-policy", ""),
    r.headers.get("permissions-policy"),
)

# The docs render HTML on purpose, so the blanket policy would white-screen them.
r = c.get("/docs")
check("the API docs still render", r.status_code == 200, r.status_code)
check(
    "the strict policy is not applied to them",
    "default-src 'none'" not in r.headers.get("content-security-policy", ""),
    r.headers.get("content-security-policy"),
)
check(
    "but nosniff still is",
    r.headers.get("x-content-type-options") == "nosniff",
)

# HSTS teaches a browser to refuse plain HTTP for two years. Never in dev.
check(
    "HSTS is not sent over plain HTTP",
    "strict-transport-security" not in {k.lower() for k in r.headers},
    r.headers.get("strict-transport-security"),
)

# =========================================================================
section("configuration that must not reach production")
# =========================================================================
from app.core.hardening import production_problems  # noqa: E402

settings = get_settings()
problems = production_problems(settings)
print(f"      this machine would report {len(problems)} problem(s) if ENVIRONMENT=production")
for problem in problems:
    print(f"        - {problem}")

from app.config import Settings  # noqa: E402

unsafe = Settings(
    environment="production",
    secret_key="insecure-development-key",
    admin_password="ChangeMe@123",
    pii_encryption_key="",
    backend_cors_origins="*",
    frontend_base_url="http://example.com",
)
found = production_problems(unsafe)
check("every shipped default is caught", len(found) >= 5, len(found))
check("including the published admin password", any("ADMIN_PASSWORD" in p for p in found))
check("and the example signing key", any("SECRET_KEY" in p for p in found))
check("and an unset PII key", any("PII_ENCRYPTION_KEY" in p for p in found))

# =========================================================================
section("the append-only ledger (addendum B9)")
# =========================================================================
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin signs in", r.status_code == 200, r.status_code)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = c.get("/audit/verify", headers=AH)
check("the hash chain verifies", r.json()["ok"] is True, r.json())
chain_len = r.json()["checked"]

r = c.get("/audit/grants", headers=AH)
check("the grant probe runs", r.status_code == 200, r.text[:200])
probe = r.json()
check("it actually probed rather than guessing", probe.get("checked") is True, probe)

if probe.get("append_only"):
    check("the database refuses to modify the ledger", True, probe["detail"])
else:
    # Expected on a dev box running as root. Worth reporting loudly rather than
    # passing quietly, because this is the control B9 promised.
    print(
        "NOTE  the ledger is NOT append-only on this database - the app user can "
        "still UPDATE audit_logs.\n"
        "      Expected in development (running as root). Apply it with:\n"
        "        ./.venv/Scripts/python.exe scripts/grant_append_only.py --show\n"
        f"      probe said: {probe.get('detail')}"
    )
    check(
        "and the probe says exactly how to fix it",
        "grant_append_only" in (probe.get("detail") or ""),
        probe.get("detail"),
    )

r = c.get("/audit/verify", headers=AH)
check(
    "the probe left the ledger untouched",
    r.json()["ok"] is True and r.json()["checked"] == chain_len,
    {"before": chain_len, "after": r.json()["checked"]},
)

# =========================================================================
section("the audit viewer")
# =========================================================================
r = c.get("/audit/summary", headers=AH)
check("the summary loads", r.status_code == 200, r.text[:200])
summary = r.json()
check("it counts by action", len(summary["by_action"]) > 3, list(summary["by_action"])[:5])
check("and by entity", len(summary["by_entity"]) > 3, list(summary["by_entity"])[:5])
check("and reports the range it covers", summary["oldest"] and summary["newest"], summary["oldest"])

r = c.get("/audit", headers=AH, params={"page_size": 5})
check("the ledger lists", r.status_code == 200 and len(r.json()["items"]) == 5, r.status_code)
sample = next((e for e in r.json()["items"] if e["entity_id"]), None)

if sample:
    r = c.get(
        f"/audit/entity/{sample['entity_type']}/{sample['entity_id']}", headers=AH
    )
    check("one thing's whole history loads", r.status_code == 200, r.text[:200])
    history = r.json()
    check("it is scoped to that thing", all(
        e["entity_type"] == sample["entity_type"] and e["entity_id"] == sample["entity_id"]
        for e in history
    ), len(history))
    check(
        "and reads forwards, oldest first",
        [e["id"] for e in history] == sorted(e["id"] for e in history),
        [e["id"] for e in history][:5],
    )

before_export = c.get("/audit/summary", headers=AH).json()["total"]
r = c.get("/audit/export", headers=AH, params={"limit": 50})
check("the ledger exports as CSV", r.status_code == 200, r.status_code)
check("as a download, not a page", "attachment" in r.headers.get("content-disposition", ""))
check("with nosniff", r.headers.get("x-content-type-options") == "nosniff")
lines = r.text.strip().splitlines()
check("with a header row", lines[0].startswith("id,when_utc,actor"), lines[0][:40])
check("and rows under it", len(lines) > 1, len(lines))

after_export = c.get("/audit/summary", headers=AH).json()["total"]
check(
    "exporting the log is itself recorded",
    after_export == before_export + 1,
    {"before": before_export, "after": after_export},
)
r = c.get("/audit", headers=AH, params={"action": "VIEW_SENSITIVE", "page_size": 5})
check(
    "as a VIEW_SENSITIVE row naming who took it",
    any("exported" in e["summary"] for e in r.json()["items"]),
    [e["summary"][:50] for e in r.json()["items"][:2]],
)

r = c.get("/audit/grants")
check("the grant probe needs a system admin", r.status_code == 401, r.status_code)

# =========================================================================
section("rate limiting the unauthenticated endpoints")
# =========================================================================
# The limiter lives in the server process, so this script cannot reset it.
# Each check presents its own X-Forwarded-For instead, which is what the
# limiter keys on - that gives every check a clean budget and exercises the
# property that matters: one noisy caller must not lock anyone else out.


def from_address(ip: str) -> dict:
    return {"X-Forwarded-For": ip}


noisy = "198.51.100.17"
victim = f"nobody.{STAMP}@designboxed.com"

codes = []
for _ in range(ratelimit.LOGIN.limit + 3):
    codes.append(
        c.post(
            "/auth/login",
            json={"email": victim, "password": "wrong-password"},
            headers=from_address(noisy),
        ).status_code
    )

check("wrong passwords answer 401 until the quota runs out", codes[0] == 401, codes[:3])
check("then that address and account pair is refused with 429", 429 in codes, codes)
check(
    "the limit bites at roughly where it is set",
    codes.index(429) <= ratelimit.LOGIN.limit + 1,
    {"limit": ratelimit.LOGIN.limit, "first_429_at": codes.index(429)},
)

r = c.post(
    "/auth/login",
    json={"email": victim, "password": "wrong-password"},
    headers=from_address(noisy),
)
check("the refusal carries Retry-After", r.headers.get("retry-after") is not None, r.headers.get("retry-after"))
check(
    "and says to slow down rather than that the password was wrong",
    "Too many" in r.json().get("detail", ""),
    r.json().get("detail"),
)

# The bug this two-tier design exists to avoid. A field-ops company shares one
# NAT egress; a per-address-only limit would lock out the whole office.
r = c.post(
    "/auth/login",
    json={"email": "admin@designboxed.com", "password": "ChangeMe@123"},
    headers=from_address(noisy),
)
check(
    "a colleague at the same address is completely unaffected",
    r.status_code == 200,
    r.status_code,
)

r = c.post(
    "/auth/login",
    json={"email": "admin@designboxed.com", "password": "ChangeMe@123"},
    headers=from_address("203.0.113.42"),
)
check("and so is a different address entirely", r.status_code == 200, r.status_code)

check(
    "the address ceiling is far above a shared office",
    ratelimit.LOGIN_BURST.limit >= 100,
    ratelimit.LOGIN_BURST.limit,
)
check(
    "password reset is limited harder than sign-in",
    ratelimit.RESET.limit < ratelimit.LOGIN.limit,
    {"reset": ratelimit.RESET.limit, "login": ratelimit.LOGIN.limit},
)

resetter = "198.51.100.211"
reset_codes = [
    c.post(
        "/auth/forgot-password", json={"email": victim}, headers=from_address(resetter)
    ).status_code
    for _ in range(ratelimit.RESET.limit + 2)
]
check("forgot-password is rate limited too", 429 in reset_codes, reset_codes)
check(
    "and answers identically until it does",
    len({code for code in reset_codes if code != 429}) == 1,
    reset_codes,
)
check(
    "exhausting reset for one address does not block sign-in from it",
    c.post(
        "/auth/login",
        json={"email": "admin@designboxed.com", "password": "ChangeMe@123"},
        headers=from_address(resetter),
    ).status_code
    == 200,
)

# =========================================================================
section("the ledger, one more time")
# =========================================================================
r = c.post(
    "/auth/login",
    json={"email": "admin@designboxed.com", "password": "ChangeMe@123"},
    headers={"X-Forwarded-For": "203.0.113.99"},
)
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}
r = c.get("/audit/verify", headers=AH)
check("the audit chain is still intact after all of that", r.json()["ok"] is True, r.json())

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All Phase 8 checks passed.")
