"""
Phase 1 end-to-end smoke test.

Exercises the real API over HTTP against a running server and a real MySQL,
which the unit suite deliberately does not. Run it after any change to auth,
users or the ledger:

    ./.venv/Scripts/python.exe scripts/smoke_phase1.py

It creates a handful of throwaway accounts, so point it at a dev database.
"""
import sys
import uuid

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=20)
failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


# 1. admin signs in
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("admin can sign in", r.status_code == 200, r.status_code)
admin = r.json()["access_token"]
AH = {"Authorization": f"Bearer {admin}"}

# 2. wrong password is rejected, and does not reveal the account exists
r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "wrong-password-x"})
wrong_msg = r.json().get("detail")
r2 = c.post("/auth/login", json={"email": "nobody.at.all@designboxed.com", "password": "wrong-password-x"})
check("wrong password rejected", r.status_code == 401, r.status_code)
check("unknown account gives identical message", wrong_msg == r2.json().get("detail"), wrong_msg)

# 3. unauthenticated access is refused
check("users list needs auth", c.get("/users").status_code == 401)

# 4. admin creates a ground-staff user and gets an invite link
email = f"field.{uuid.uuid4().hex[:8]}@designboxed.com"
r = c.post(
    "/users",
    headers=AH,
    json={
        "email": email,
        "full_name": "Ravi Kumar",
        "role": "GROUND_STAFF",
        "designation": "EXECUTIVE",
        "gender": "MALE",
        "base_location": "Hyderabad",
    },
)
check("admin can invite a user", r.status_code == 201, r.status_code)
invite_url = r.json()["invite_url"]
raw_token = invite_url.split("token=")[1]

# 5. the invited user cannot sign in yet
r = c.post("/auth/login", json={"email": email, "password": "anything-at-all"})
check("invited user cannot sign in before accepting", r.status_code == 401, r.status_code)

# 6. token preview works
r = c.get(f"/auth/token/{raw_token}")
check("invite token previews", r.status_code == 200 and r.json()["email"] == email, r.status_code)

# 7. password policy is enforced
r = c.post("/auth/set-password", json={"token": raw_token, "password": "short"})
check("short password rejected", r.status_code == 422, r.status_code)
r = c.post("/auth/set-password", json={"token": raw_token, "password": "password123"})
check("common password rejected", r.status_code == 422, r.json().get("detail"))
r = c.post("/auth/set-password", json={"token": raw_token, "password": f"{email.split('@')[0]}-9999"})
check("password containing the email rejected", r.status_code == 422, r.json().get("detail"))

# 8. a good password is accepted
r = c.post("/auth/set-password", json={"token": raw_token, "password": "Monsoon-Ledger-42"})
check("valid password accepted", r.status_code == 200, r.json().get("detail"))

# 9. the token is single use
r = c.post("/auth/set-password", json={"token": raw_token, "password": "Another-Valid-Pass-7"})
check("invite token cannot be replayed", r.status_code == 404, r.status_code)

# 10. the new user can now sign in
r = c.post("/auth/login", json={"email": email, "password": "Monsoon-Ledger-42"})
check("invited user can sign in", r.status_code == 200, r.status_code)
staff = r.json()["access_token"]
SH = {"Authorization": f"Bearer {staff}"}
staff_id = r.json()["user"]["id"]

# 11. ground staff cannot reach admin endpoints
check("ground staff blocked from user list", c.get("/users", headers=SH).status_code == 403)
check("ground staff blocked from audit log", c.get("/audit", headers=SH).status_code == 403)

# 12. ground staff can read themselves
r = c.get("/auth/me", headers=SH)
check("ground staff can read own profile", r.status_code == 200 and r.json()["email"] == email)

# 13. privilege escalation is blocked
r = c.post(
    "/users",
    headers=SH,
    json={"email": "sneak@designboxed.com", "full_name": "Sneaky Person", "role": "SYSTEM_ADMIN"},
)
check("ground staff cannot create users", r.status_code == 403, r.status_code)

# 14. admin cannot deactivate themselves
r = c.post("/users/1/status", headers=AH, json={"status": "DEACTIVATED"})
check("admin cannot deactivate self", r.status_code == 400, r.json().get("detail"))
r = c.patch("/users/1", headers=AH, json={"role": "GROUND_STAFF"})
check("admin cannot change own role", r.status_code == 400, r.json().get("detail"))

# 15. theme preference persists server-side
r = c.patch("/auth/me/theme", headers=SH, json={"theme_preference": "dark"})
check("theme preference saved", r.status_code == 200 and r.json()["theme_preference"] == "dark")
r = c.patch("/auth/me/theme", headers=SH, json={"theme_preference": "neon"})
check("invalid theme rejected", r.status_code == 422, r.status_code)

# 16. lockout after repeated failures
lock_email = f"lock.{uuid.uuid4().hex[:8]}@designboxed.com"
r = c.post("/users", headers=AH, json={"email": lock_email, "full_name": "Lock Test", "role": "GROUND_STAFF", "gender": "MALE"})
lock_token = r.json()["invite_url"].split("token=")[1]
c.post("/auth/set-password", json={"token": lock_token, "password": "Corridor-Anchor-58"})
codes = [
    c.post("/auth/login", json={"email": lock_email, "password": "definitely-wrong"}).status_code
    for _ in range(6)
]
check("account locks after repeated failures", 423 in codes, codes)
r = c.post("/auth/login", json={"email": lock_email, "password": "Corridor-Anchor-58"})
check("correct password still blocked while locked", r.status_code == 423, r.status_code)

# 17. an admin can unlock
locked_id = c.get("/users", headers=AH, params={"search": lock_email}).json()["items"][0]["id"]
r = c.post(f"/users/{locked_id}/unlock", headers=AH)
check("admin can unlock an account", r.status_code == 200 and r.json()["is_locked"] is False)
r = c.post("/auth/login", json={"email": lock_email, "password": "Corridor-Anchor-58"})
check("sign-in works after unlock", r.status_code == 200, r.status_code)

# 18. forgot-password never reveals whether an account exists
a = c.post("/auth/forgot-password", json={"email": email}).json()
b = c.post("/auth/forgot-password", json={"email": "ghost@designboxed.com"}).json()
check("forgot-password answers identically", a == b and a.get("invite_url") is None, a)

# 19. the audit ledger recorded all of it, and the chain is intact
r = c.get("/audit", headers=AH, params={"page_size": 200})
check("audit log readable by system admin", r.status_code == 200, r.status_code)
entries = r.json()["items"]
actions = {e["action"] for e in entries}
check("logins recorded", "LOGIN" in actions, sorted(actions))
check("failed logins recorded", "LOGIN_FAILED" in actions)
check("user creation recorded", "CREATE" in actions)
check(
    "password values never stored in the ledger",
    all("Monsoon-Ledger-42" not in str(e.get("changes")) for e in entries),
)

r = c.get("/audit/verify", headers=AH)
check("hash chain verifies", r.status_code == 200 and r.json()["ok"], r.json())
print(f"\nledger rows checked: {r.json().get('checked')}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
