"""
Hardening: the checks that stop an unsafe configuration shipping (SOW §7).

Every default these tests assert against is a real value sitting in this
repository - an example signing key, a bootstrap password printed in the README,
a CORS list pointing at localhost. Each is correct for development and each is a
breach in production, and the failure mode is silence: nothing misbehaves, the
app just quietly has a known password.
"""
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.core import ratelimit
from app.core.hardening import SECURITY_HEADERS, check_startup, production_problems

SAFE = dict(
    environment="production",
    secret_key="x" * 48,
    admin_password="a-real-and-sufficiently-long-password",
    pii_encryption_key="Zm9vYmFyYmF6cXV1eGZvb2JhcmJhenF1dXhmb28xMjM0NTY3OD0=",
    backend_cors_origins="https://travel.designboxed.com",
    frontend_base_url="https://travel.designboxed.com",
)


def settings(**overrides) -> Settings:
    return Settings(**{**SAFE, **overrides})


# ---------------------------------------------------------------------------
# What must not reach production
# ---------------------------------------------------------------------------


def test_a_properly_configured_production_deploy_has_no_problems():
    assert production_problems(settings()) == []


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"secret_key": "insecure-development-key"}, "SECRET_KEY"),
        ({"secret_key": "short"}, "SECRET_KEY"),
        ({"secret_key": ""}, "SECRET_KEY"),
        ({"admin_password": "ChangeMe@123"}, "ADMIN_PASSWORD"),
        ({"pii_encryption_key": ""}, "PII_ENCRYPTION_KEY"),
        ({"backend_cors_origins": "*"}, "any origin"),
        ({"backend_cors_origins": "http://localhost:5173"}, "localhost"),
        ({"frontend_base_url": "http://travel.designboxed.com"}, "plain HTTP"),
    ],
)
def test_each_development_default_is_caught(overrides, expected):
    problems = production_problems(settings(**overrides))
    assert any(expected in p for p in problems), problems


def test_production_refuses_to_start_with_a_known_admin_password():
    """The one that matters most: it is published in the README."""
    with pytest.raises(RuntimeError) as caught:
        check_startup(settings(admin_password="ChangeMe@123"))
    assert "ADMIN_PASSWORD" in str(caught.value)


def test_the_refusal_lists_every_problem_not_just_the_first():
    """Fixing them one deploy at a time is how a Friday evening disappears."""
    with pytest.raises(RuntimeError) as caught:
        check_startup(
            settings(
                secret_key="insecure-development-key",
                admin_password="ChangeMe@123",
                pii_encryption_key="",
            )
        )
    message = str(caught.value)
    assert "SECRET_KEY" in message
    assert "ADMIN_PASSWORD" in message
    assert "PII_ENCRYPTION_KEY" in message


def test_development_warns_but_still_starts():
    """A developer must not have to configure a production secret to run tests."""
    check_startup(Settings(environment="development", secret_key="insecure-development-key"))


def test_a_signing_key_long_enough_to_be_real_passes():
    assert production_problems(settings(secret_key="a" * 32)) == []


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


def test_the_headers_that_actually_matter_are_present():
    # nosniff is load-bearing rather than boilerplate: an ID scan or a ticket
    # must never be sniffed into text/html and rendered.
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert "no-referrer" in SECURITY_HEADERS["Referrer-Policy"]


def test_the_content_policy_allows_nothing_by_default():
    """This service returns JSON and files and renders no HTML of its own, so a
    payload that reached a response body has nowhere to execute."""
    policy = SECURITY_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class FakeRequest:
    """Just enough of a Request for the limiter."""

    def __init__(self, ip="203.0.113.7", forwarded=None):
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = type("C", (), {"host": ip})()


@pytest.fixture(autouse=True)
def _clean_windows():
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


def test_the_quota_is_allowed_then_the_next_one_is_refused():
    window = ratelimit.SlidingWindow(limit=3, window_seconds=60)
    assert [window.check("k")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = window.check("k")
    assert allowed is False
    assert retry_after > 0


def test_addresses_are_counted_separately():
    """One noisy address must not lock everyone else out of signing in."""
    window = ratelimit.SlidingWindow(limit=2, window_seconds=60)
    window.check("a")
    window.check("a")
    assert window.check("a")[0] is False
    assert window.check("b")[0] is True


def test_the_window_slides_rather_than_resetting_on_a_boundary(monkeypatch):
    """A fixed bucket lets an attacker send the limit twice across a boundary."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["now"])

    window = ratelimit.SlidingWindow(limit=2, window_seconds=10)
    assert window.check("k")[0] is True
    assert window.check("k")[0] is True
    assert window.check("k")[0] is False

    clock["now"] += 11   # the first two have aged out
    assert window.check("k")[0] is True


def test_enforce_raises_429_with_a_retry_after_header():
    """A client that cannot tell "slow down" from "wrong password" retries the
    wrong one forever."""
    window = ratelimit.SlidingWindow(limit=1, window_seconds=60)
    request = FakeRequest()

    ratelimit.enforce(window, request, "sign-in")
    with pytest.raises(HTTPException) as caught:
        ratelimit.enforce(window, request, "sign-in")

    assert caught.value.status_code == 429
    assert "Retry-After" in caught.value.headers
    assert "sign-in" in caught.value.detail


def test_different_actions_have_separate_budgets():
    """Failing to sign in must not consume someone's password-reset allowance."""
    request = FakeRequest()
    for _ in range(ratelimit.LOGIN.limit):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="a@b.com")

    with pytest.raises(HTTPException):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="a@b.com")
    ratelimit.enforce(ratelimit.RESET, request, "password reset", subject="a@b.com")


def test_the_tight_window_is_per_account_not_per_address():
    """The bug this design exists to avoid: an office behind one NAT egress
    looks identical to one attacker if the key is the address alone. Fifty
    people signing in on Monday morning must not lock each other out."""
    request = FakeRequest()

    for _ in range(ratelimit.LOGIN.limit):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="ravi@designboxed.com")

    with pytest.raises(HTTPException):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="ravi@designboxed.com")

    # A colleague at the same desk is unaffected.
    ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="meera@designboxed.com")


def test_the_loose_window_still_stops_walking_the_directory():
    """The other half: one address trying one password against every account
    never trips the per-account counter, so the address ceiling catches it."""
    request = FakeRequest()

    with pytest.raises(HTTPException):
        for i in range(ratelimit.LOGIN_BURST.limit + 5):
            ratelimit.enforce_pair(
                ratelimit.LOGIN,
                ratelimit.LOGIN_BURST,
                request,
                "sign-in",
                subject=f"person{i}@designboxed.com",
            )


def test_the_address_ceiling_is_far_above_a_shared_office():
    """A hundred-person company behind one egress signs in every morning."""
    assert ratelimit.LOGIN_BURST.limit >= 100
    assert ratelimit.LOGIN_BURST.limit > ratelimit.LOGIN.limit * 5


def test_password_reset_is_limited_harder_than_sign_in():
    """Each reset can send mail, so an unauthenticated endpoint that sends mail
    is a way to use this server to harass someone."""
    assert ratelimit.RESET.limit < ratelimit.LOGIN.limit
    assert ratelimit.RESET_BURST.limit < ratelimit.LOGIN_BURST.limit


def test_the_forwarded_client_is_counted_when_behind_a_proxy():
    assert ratelimit.client_key(FakeRequest(forwarded="198.51.100.9, 10.0.0.1")) == "198.51.100.9"
    assert ratelimit.client_key(FakeRequest(ip="203.0.113.7")) == "203.0.113.7"


def test_a_request_with_no_client_still_gets_a_key():
    """Rather than crashing on a key of None, which would take the endpoint down."""
    request = FakeRequest()
    request.client = None
    assert ratelimit.client_key(request) == "unknown"


def test_the_subject_is_normalised_so_case_cannot_buy_a_fresh_budget():
    request = FakeRequest()
    for _ in range(ratelimit.LOGIN.limit):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="Ravi@Designboxed.com")

    with pytest.raises(HTTPException):
        ratelimit.enforce(ratelimit.LOGIN, request, "sign-in", subject="  ravi@designboxed.COM ")


def test_quiet_keys_are_swept_so_the_map_does_not_grow_forever(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["now"])

    window = ratelimit.SlidingWindow(limit=5, window_seconds=10)
    for i in range(2100):
        window.check(f"key-{i}")
    clock["now"] += 100
    window.check("one-more")

    assert len(window._hits) < 2100


def test_the_tight_tier_charges_failures_not_attempts():
    """Signing in correctly ten times is not suspicious - it is a person with
    several tabs, or a token that expired. Charging successes against a
    credential-stuffing budget locks out the only people who know the password.
    """
    window = ratelimit.SlidingWindow(limit=2, window_seconds=60)

    # peek never consumes, so a hundred successful sign-ins cost nothing.
    for _ in range(100):
        assert window.peek("k")[0] is True

    # A failure does.
    window.record("k")
    window.record("k")
    assert window.peek("k")[0] is False


def test_enforce_pair_returns_an_uncharged_key(monkeypatch):
    """The caller charges it only if the attempt turned out to be wrong."""
    tight = ratelimit.SlidingWindow(limit=1, window_seconds=60)
    loose = ratelimit.SlidingWindow(limit=50, window_seconds=60)
    request = FakeRequest()

    key = ratelimit.enforce_pair(tight, loose, request, "sign-in", subject="a@b.com")
    # Nothing charged yet, so a second call is still fine.
    ratelimit.enforce_pair(tight, loose, request, "sign-in", subject="a@b.com")

    ratelimit.penalise(tight, key)
    with pytest.raises(HTTPException):
        ratelimit.enforce_pair(tight, loose, request, "sign-in", subject="a@b.com")


def test_the_loose_tier_still_counts_every_request():
    """It is a flood guard, and a flood of valid requests is still a flood."""
    tight = ratelimit.SlidingWindow(limit=100, window_seconds=60)
    loose = ratelimit.SlidingWindow(limit=3, window_seconds=60)
    request = FakeRequest()

    for i in range(3):
        ratelimit.enforce_pair(tight, loose, request, "sign-in", subject=f"p{i}@b.com")

    with pytest.raises(HTTPException):
        ratelimit.enforce_pair(tight, loose, request, "sign-in", subject="p9@b.com")


def test_password_reset_charges_every_request_not_just_failures():
    """Unlike sign-in, this endpoint answers identically whether or not the
    address exists and can send mail either way - so there is no failure to
    charge against, and every request is the thing being limited."""
    request = FakeRequest()
    for _ in range(ratelimit.RESET.limit):
        ratelimit.enforce(ratelimit.RESET, request, "password reset", subject="a@b.com")

    with pytest.raises(HTTPException):
        ratelimit.enforce(ratelimit.RESET, request, "password reset", subject="a@b.com")
