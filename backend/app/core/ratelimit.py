"""
Rate limiting for the endpoints worth attacking.

Phase 1 locks an *account* after repeated failures, which stops someone grinding
one password. It does nothing about the other shape of the same attack: one
address trying one password against a hundred addresses, which never trips a
single account's counter. This closes that.

**Two tiers, because one is always wrong.** Keying on the address alone is the
obvious design and it breaks a real deployment: this product serves a field-ops
company whose office shares one NAT egress, so fifty people signing in on Monday
morning look identical to one attacker. Keying only on the account is no better -
it is exactly the gap account lockout already leaves.

So:

* a **tight** window per (address, account), which is the attack - one address
  grinding one login; and
* a **loose** window per address, high enough that a whole office behind one IP
  never notices it, low enough that walking the directory does not scale.

Other properties worth knowing:

* **The tight tier counts failures, not attempts.** Signing in correctly ten
  times is not suspicious - it is a person with several tabs, or a token that
  expired. Charging successful sign-ins against a credential-stuffing budget
  locks out the only people who know their password. The loose tier still counts
  every request, because that one is a flood guard and a flood of *valid*
  requests is still a flood.
* **In memory, per process.** At ~110 users on one container this is accurate
  and free. Two replicas each allow the quota - stated here rather than
  discovered later. The fix when that matters is Redis behind `check()`, not a
  redesign.
* **Mostly unauthenticated endpoints.** An authenticated user hitting the API
  hard is a bug to find, not an attacker to stop. The exceptions (EMAIL_TEST,
  REAUTH) guard something a stolen session could abuse.
* **429 with `Retry-After`.** A client that cannot tell "slow down" from "wrong
  password" will retry the wrong one forever.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class SlidingWindow:
    """Counts hits per key inside a moving window.

    A deque of timestamps rather than a fixed bucket, so the limit cannot be
    doubled by straddling a bucket boundary - the classic way a naive counter is
    bypassed.
    """

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, seconds until the window frees up)."""
        allowed, retry_after = self.peek(key)
        if allowed:
            self.record(key)
        return allowed, retry_after

    def peek(self, key: str) -> tuple[bool, int]:
        """Is there room, without taking any?

        Split out from `check` so a caller can decide *after the fact* whether
        the attempt was worth charging for - which is how the sign-in path
        charges failures and lets successes through free.
        """
        now = time.monotonic()
        cutoff = now - self.window

        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= self.limit:
                return False, max(int(hits[0] + self.window - now) + 1, 1)
            return True, 0

    def record(self, key: str) -> None:
        """Charge one hit against `key`."""
        now = time.monotonic()
        with self._lock:
            self._hits[key].append(now)

            # Keys go quiet and stay in the dict forever otherwise. Cheap to do
            # here, and only when the map has grown enough to be worth it.
            if len(self._hits) > 2048:
                self._sweep(now - self.window)

    def _sweep(self, cutoff: float) -> None:
        """Drop keys with nothing left in the window. Caller holds the lock."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
            del self._hits[key]

    def reset(self, key: str | None = None) -> None:
        """Clear one key, or everything. For tests and for an admin unblocking."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


#: Sign-in, per (address, account). This is the attack: one address grinding one
#: login. Generous enough that someone mistyping twice and then pasting from a
#: password manager is never inconvenienced.
LOGIN = SlidingWindow(limit=10, window_seconds=300)

#: Sign-in, per address, whatever account is named. A backstop against walking
#: the directory. Deliberately high: a hundred-person office behind one NAT
#: egress must never meet it, and account lockout is what actually stops a
#: focused attack.
LOGIN_BURST = SlidingWindow(limit=120, window_seconds=300)

#: Password reset, per (address, account). Lower, because each one can send an
#: email, and an unauthenticated endpoint that sends email is a way to use this
#: server to harass someone.
RESET = SlidingWindow(limit=5, window_seconds=900)

#: Password reset, per address.
RESET_BURST = SlidingWindow(limit=40, window_seconds=900)

#: Redeeming an invite or reset token. Guessing one is infeasible; this stops
#: someone trying anyway at speed.
TOKEN = SlidingWindow(limit=40, window_seconds=300)

#: The admin "send a test email" button, per admin. The one authenticated
#: endpoint here: each press makes the server sign in to the company mail
#: account, and a few retries while fixing settings is normal, a loop is not.
EMAIL_TEST = SlidingWindow(limit=10, window_seconds=900)

#: A wrong current password on change-password or change-email, per user.
#: Authenticated, like EMAIL_TEST, because a stolen session must not be able to
#: grind the password that guards the sign-in email.
REAUTH = SlidingWindow(limit=5, window_seconds=900)


def client_key(request: Request) -> str:
    """Who to count against.

    Behind our own ingress the first `X-Forwarded-For` hop is the client. That
    header is forgeable by anyone who can reach the app directly, which is the
    reason this is a speed bump layered on top of account lockout rather than
    the only control.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else "unknown"


def enforce(window: SlidingWindow, request: Request, what: str, *, subject: str = "") -> None:
    """Raise 429 if this caller has had its share of `what` for now.

    `subject` narrows the bucket - an email address, usually - so the tight
    window counts attempts against one account rather than against everyone
    arriving from the same office.
    """
    key = f"{what}:{client_key(request)}"
    if subject:
        key = f"{key}:{subject.strip().lower()}"

    allowed, retry_after = window.check(key)
    if allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many {what} attempts. Try again in about {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def bucket(request: Request, what: str, subject: str = "") -> str:
    key = f"{what}:{client_key(request)}"
    return f"{key}:{subject.strip().lower()}" if subject else key


def enforce_pair(
    tight: SlidingWindow,
    loose: SlidingWindow,
    request: Request,
    what: str,
    *,
    subject: str,
) -> str:
    """Check both tiers and return the tight bucket's key, uncharged.

    The loose tier is charged here - it counts every request, because it is a
    flood guard. The tight tier is only *checked*; the caller charges it with
    `penalise()` if the attempt turns out to have failed. Returning the key
    rather than a closure keeps the caller's failure path obvious.
    """
    enforce(loose, request, what)

    key = bucket(request, what, subject)
    allowed, retry_after = tight.peek(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many {what} attempts. Try again in about {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    return key


def penalise(window: SlidingWindow, key: str) -> None:
    """Charge a failed attempt. Successes never reach this."""
    window.record(key)


def reset_all() -> None:
    """Clear every window. Used by the test suite between cases."""
    for window in (LOGIN, LOGIN_BURST, RESET, RESET_BURST, TOKEN, EMAIL_TEST, REAUTH):
        window.reset()
