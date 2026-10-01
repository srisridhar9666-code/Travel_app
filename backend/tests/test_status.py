"""
Status derivation and the edit lock.

These two functions carry the two structural decisions the SOW got wrong or left
out (addendum A1 and B1), so they are worth pinning down before anything is built
on top of them.
"""
import pytest

from app.core.enums import (
    RequestStatus as R,
    TravellerStatus as T,
    derive_request_status,
    is_editable,
)

# name, traveller statuses, is_draft, is_cancelled, expected status, expected editable
CASES = [
    ("fresh group request", [T.PENDING, T.PENDING], False, False, R.SUBMITTED, True),
    ("admin approved one of four", [T.APPROVED, T.PENDING, T.PENDING], False, False, R.PARTIALLY_APPROVED, False),
    ("all approved, no tickets yet", [T.APPROVED, T.APPROVED], False, False, R.APPROVED, False),
    ("approved and rejected mix", [T.APPROVED, T.REJECTED], False, False, R.APPROVED, False),
    ("everyone rejected", [T.REJECTED, T.REJECTED], False, False, R.REJECTED, False),
    ("tickets uploaded for all", [T.BOOKED, T.BOOKED], False, False, R.BOOKED, False),
    ("some booked, some only approved", [T.BOOKED, T.APPROVED], False, False, R.PARTIALLY_APPROVED, False),
    ("draft beats traveller state", [T.PENDING], True, False, R.DRAFT, True),
    ("cancellation beats traveller state", [T.APPROVED], False, True, R.CANCELLED, False),
    ("every traveller dropped out", [T.CANCELLED, T.CANCELLED], False, False, R.CANCELLED, False),
    ("one dropped, the rest pending", [T.CANCELLED, T.PENDING], False, False, R.SUBMITTED, True),
]


@pytest.mark.parametrize(
    "statuses,is_draft,is_cancelled,expected",
    [(c[1], c[2], c[3], c[4]) for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_derive_request_status(statuses, is_draft, is_cancelled, expected):
    assert derive_request_status(statuses, is_draft=is_draft, is_cancelled=is_cancelled) is expected


@pytest.mark.parametrize(
    "statuses,is_draft,is_cancelled,expected",
    [(c[1], c[2], c[3], c[5]) for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_is_editable(statuses, is_draft, is_cancelled, expected):
    assert is_editable(statuses, is_draft=is_draft, is_cancelled=is_cancelled) is expected


def test_empty_request_is_submitted_and_editable():
    """A request with no travellers yet is still the requester's to shape."""
    assert derive_request_status([]) is R.SUBMITTED
    assert is_editable([]) is True


def test_any_admin_decision_locks_the_request():
    """The core of the edit window: one decision anywhere on the request ends it."""
    for decided in (T.APPROVED, T.REJECTED, T.BOOKED):
        assert is_editable([T.PENDING, decided, T.PENDING]) is False
