"""
Admin decisions on individual travellers (SOW section 4).

Two things are load-bearing here and neither is stated in the SOW. The first is
that a decision is *per person*: a group of four can leave this queue as two
approved, one rejected and one still pending. The second is addendum B6's other
half - conflicts warn the requester, but an admin who approves over one has to
type a reason, and that reason reaches the ledger before the approval it
justifies.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select

from app.core import clock
from app.core.enums import (
    AuditAction,
    Gender,
    NotificationChannel,
    NotificationStatus,
    RequestStatus,
    RequestType,
    Role,
    TravellerStatus,
)
from app.models.audit import AuditLog
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import decisions
from app.services import requests as svc

#: Whitespace that is not a reason. Built with chr() so the literal carries no
#: escapes of its own.
BLANKS = chr(10) + chr(9)

TENANT = "designboxed"


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")

    def person(name, email, role=Role.GROUND_STAFF):
        user = User(
            tenant_id=TENANT,
            email=email,
            full_name=name,
            role=role,
            gender=Gender.MALE,
            password_hash="x",
        )
        db.add(user)
        return user

    admin = person("Priya Shah", "priya@designboxed.com", Role.ADMIN)
    ravi = person("Ravi Kumar", "ravi@designboxed.com")
    meera = person("Meera Iyer", "meera@designboxed.com")
    db.add(project)
    db.commit()
    return project, admin, ravi, meera


def make_stay(db, project, users, *, check_in=None, check_out=None):
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=users[0].id,
        hotel_city="Mumbai",
        check_in=check_in or clock.local_today() + timedelta(days=30),
        check_out=check_out or clock.local_today() + timedelta(days=33),
    )
    row.travellers = [
        RequestTraveller(user_id=u.id, status=TravellerStatus.PENDING) for u in users
    ]
    db.add(row)
    db.commit()
    return row


def decide(db, *, actor, request, traveller, to_status, **kwargs):
    # Every decision needs a reason now. These tests are about transitions and
    # notifications, so the helper supplies one; the rule itself is tested
    # explicitly in TestEveryDecisionNeedsAReason.
    kwargs.setdefault("reason", "Operationally required")
    result = decisions.apply(
        db,
        tenant_id=TENANT,
        actor=actor,
        request=request,
        traveller=traveller,
        decision=decisions.Decision(
            traveller_id=traveller.id, to_status=to_status, **kwargs
        ),
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# The transition map
# ---------------------------------------------------------------------------


def test_pending_can_be_approved_rejected_or_cancelled(db, world):
    project, admin, ravi, meera = world
    for target, extra in (
        (TravellerStatus.APPROVED, {}),
        (TravellerStatus.REJECTED, {"reason": "Budget"}),
        (TravellerStatus.CANCELLED, {}),
    ):
        row = make_stay(db, project, [ravi])
        decide(db, actor=admin, request=row, traveller=row.travellers[0], to_status=target, **extra)
        assert row.travellers[0].status is target


def test_booking_requires_an_approval_first(db, world):
    """Addendum B2: approved means sanctioned, booked means a ticket exists.
    Skipping straight to booked would erase the distinction the SOW was missing."""
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])

    with pytest.raises(HTTPException) as caught:
        decide(
            db,
            actor=admin,
            request=row,
            traveller=row.travellers[0],
            to_status=TravellerStatus.BOOKED,
            booking_reference="PNR123",
        )
    assert caught.value.status_code == 409


def test_approved_then_booked_is_the_happy_path(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]

    decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.APPROVED)
    decide(
        db,
        actor=admin,
        request=row,
        traveller=traveller,
        to_status=TravellerStatus.BOOKED,
        booking_reference="6E-4412 / PNR QK8T2M",
    )
    assert traveller.status is TravellerStatus.BOOKED
    assert traveller.booking_reference == "6E-4412 / PNR QK8T2M"


def test_a_decision_cannot_be_reversed(db, world):
    """No undo in V1. Correcting a decision goes through cancel-and-reraise, the
    same escape hatch the edit lock leaves open."""
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]

    decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.REJECTED,
           reason="Not approved for this campaign")

    with pytest.raises(HTTPException) as caught:
        decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.APPROVED)
    assert caught.value.status_code == 409
    assert "cannot be approved" in caught.value.detail


def test_deciding_the_same_way_twice_says_so_plainly(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]

    decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.APPROVED)
    with pytest.raises(HTTPException) as caught:
        decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.APPROVED)
    assert "already approved" in caught.value.detail


def test_a_cancelled_traveller_is_the_end_of_the_line(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]

    decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.CANCELLED)
    for target in (TravellerStatus.APPROVED, TravellerStatus.REJECTED, TravellerStatus.BOOKED):
        with pytest.raises(HTTPException):
            decide(db, actor=admin, request=row, traveller=traveller, to_status=target,
                   reason="x", booking_reference="y")


# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------


def _decide_raw(db, *, actor, request, traveller, to_status, **kwargs):
    """Bypasses the helper's default reason, to test the rule itself."""
    return decisions.apply(
        db,
        tenant_id=TENANT,
        actor=actor,
        request=request,
        traveller=traveller,
        decision=decisions.Decision(
            traveller_id=traveller.id, to_status=to_status, **kwargs
        ),
    )


class TestEveryDecisionNeedsAReason:
    """Not just rejections.

    An approval with nothing written against it is the one somebody asks about
    months later, and "it was approved" is not an answer. The reason is shown to
    the traveller and kept in the ledger.
    """

    def test_a_rejection_without_a_reason_is_refused(self, db, world):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        with pytest.raises(HTTPException) as caught:
            _decide_raw(db, actor=admin, request=row, traveller=row.travellers[0],
                        to_status=TravellerStatus.REJECTED)
        assert caught.value.status_code == 400

    def test_an_approval_without_a_reason_is_refused(self, db, world):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        with pytest.raises(HTTPException) as caught:
            _decide_raw(db, actor=admin, request=row, traveller=row.travellers[0],
                        to_status=TravellerStatus.APPROVED)
        assert caught.value.status_code == 400
        assert "reason" in caught.value.detail.lower()

    def test_whitespace_is_not_a_reason(self, db, world):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        for blank in ("", "   ", BLANKS):
            with pytest.raises(HTTPException):
                _decide_raw(db, actor=admin, request=row, traveller=row.travellers[0],
                            to_status=TravellerStatus.APPROVED, reason=blank)

    def test_a_reason_is_recorded_on_the_traveller(self, db, world):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        decide(db, actor=admin, request=row, traveller=row.travellers[0],
               to_status=TravellerStatus.APPROVED, reason="Client walkthrough on the 14th")
        assert row.travellers[0].decision_reason == "Client walkthrough on the 14th"


class TestSuppressingTheEmail:
    """The record is not optional; only the email is."""

    def test_the_email_is_sent_by_default(self, db, world, outbox):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        decide(db, actor=admin, request=row, traveller=row.travellers[0],
               to_status=TravellerStatus.APPROVED)
        assert len(outbox.messages) >= 1

    def test_notify_false_sends_nothing(self, db, world, outbox):
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        decisions.apply(
            db,
            tenant_id=TENANT,
            actor=admin,
            request=row,
            traveller=row.travellers[0],
            decision=decisions.Decision(
                traveller_id=row.travellers[0].id,
                to_status=TravellerStatus.APPROVED,
                reason="Told them in person",
            ),
            notify=False,
        )
        db.commit()
        assert len(outbox.messages) == 0

    def test_the_decision_still_lands_without_an_email(self, db, world, outbox):
        """Suppressing the notice must not suppress the decision."""
        project, admin, ravi, _ = world
        row = make_stay(db, project, [ravi])

        decisions.apply(
            db,
            tenant_id=TENANT,
            actor=admin,
            request=row,
            traveller=row.travellers[0],
            decision=decisions.Decision(
                traveller_id=row.travellers[0].id,
                to_status=TravellerStatus.APPROVED,
                reason="Told them in person",
            ),
            notify=False,
        )
        db.commit()

        assert row.travellers[0].status is TravellerStatus.APPROVED
        assert row.travellers[0].decided_at is not None
        assert row.travellers[0].decision_reason == "Told them in person"


def test_booking_without_a_reference_is_refused(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]
    decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.APPROVED)

    with pytest.raises(HTTPException) as caught:
        decide(db, actor=admin, request=row, traveller=traveller, to_status=TravellerStatus.BOOKED)
    assert caught.value.status_code == 400


def test_the_decision_records_who_and_when(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])
    traveller = row.travellers[0]

    decide(db, actor=admin, request=row, traveller=traveller,
           to_status=TravellerStatus.REJECTED, reason="Campaign is over budget")

    assert traveller.decided_by_id == admin.id
    assert isinstance(traveller.decided_at, datetime)
    assert traveller.decision_reason == "Campaign is over budget"


# ---------------------------------------------------------------------------
# Conflict override - addendum B6, the admin half
# ---------------------------------------------------------------------------


def clashing_pair(db, project, user):
    """Two stays on the same nights, both live. The second one clashes."""
    first = make_stay(db, project, [user])
    second = make_stay(db, project, [user])
    return first, second


def test_approving_over_a_clash_without_a_reason_is_refused(db, world):
    project, admin, ravi, _ = world
    _, second = clashing_pair(db, project, ravi)

    with pytest.raises(HTTPException) as caught:
        decide(db, actor=admin, request=second, traveller=second.travellers[0],
               to_status=TravellerStatus.APPROVED)
    assert caught.value.status_code == 409
    assert "typed reason" in caught.value.detail


def test_approving_over_a_clash_with_a_reason_succeeds_and_is_recorded(db, world):
    project, admin, ravi, _ = world
    _, second = clashing_pair(db, project, ravi)

    decide(db, actor=admin, request=second, traveller=second.travellers[0],
           to_status=TravellerStatus.APPROVED,
           conflict_override_reason="Client moved the first visit; this one stands")

    assert second.travellers[0].status is TravellerStatus.APPROVED

    overrides = db.execute(
        select(AuditLog).where(AuditLog.action == AuditAction.OVERRIDE_CONFLICT)
    ).scalars().all()
    assert len(overrides) == 1
    assert overrides[0].reason.startswith("Client moved")
    assert "Ravi Kumar" in overrides[0].summary


def test_the_override_is_written_before_the_approval_it_justifies(db, world):
    """Order matters in an append-only ledger: reading it back must show the
    justification already on file when the approval landed."""
    project, admin, ravi, _ = world
    _, second = clashing_pair(db, project, ravi)

    decide(db, actor=admin, request=second, traveller=second.travellers[0],
           to_status=TravellerStatus.APPROVED, conflict_override_reason="Agreed with the client")

    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.action.in_([AuditAction.OVERRIDE_CONFLICT, AuditAction.APPROVE]))
        .order_by(AuditLog.id)
    ).scalars().all()
    assert [r.action for r in rows] == [AuditAction.OVERRIDE_CONFLICT, AuditAction.APPROVE]


def test_rejecting_over_a_clash_needs_no_override(db, world):
    """The override exists to justify going ahead anyway. Saying no to a clashing
    request is the opposite of that."""
    project, admin, ravi, _ = world
    _, second = clashing_pair(db, project, ravi)

    decide(db, actor=admin, request=second, traveller=second.travellers[0],
           to_status=TravellerStatus.REJECTED, reason="Clashes with the Mumbai trip")
    assert second.travellers[0].status is TravellerStatus.REJECTED


def test_a_clash_is_recomputed_at_decision_time(db, world):
    """The queue may have been open for an hour. A trip that landed since must
    still stop a silent approval."""
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])

    # Clear at first.
    assert decisions.live_conflicts_for(
        db, tenant_id=TENANT, request=row, traveller=row.travellers[0]
    ) == []

    make_stay(db, project, [ravi])   # someone else files an overlapping stay

    with pytest.raises(HTTPException):
        decide(db, actor=admin, request=row, traveller=row.travellers[0],
               to_status=TravellerStatus.APPROVED)


def test_a_cancelled_clash_stops_blocking(db, world):
    project, admin, ravi, _ = world
    first, second = clashing_pair(db, project, ravi)

    first.is_cancelled = True
    for traveller in first.travellers:
        traveller.status = TravellerStatus.CANCELLED
    db.commit()

    decide(db, actor=admin, request=second, traveller=second.travellers[0],
           to_status=TravellerStatus.APPROVED)
    assert second.travellers[0].status is TravellerStatus.APPROVED


# ---------------------------------------------------------------------------
# Partial approval, and what it does to the request
# ---------------------------------------------------------------------------


def test_one_decision_on_a_group_leaves_the_rest_pending(db, world):
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])

    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.APPROVED)

    assert row.travellers[0].status is TravellerStatus.APPROVED
    assert row.travellers[1].status is TravellerStatus.PENDING
    assert svc.status_of(row) is RequestStatus.PARTIALLY_APPROVED


def test_one_decision_locks_the_request_against_edits(db, world):
    """A1 and B1 meeting: the lock is a consequence of the status change, not a
    separate flag someone has to remember to set."""
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])
    assert svc.request_is_editable(row) is True

    decide(db, actor=admin, request=row, traveller=row.travellers[1],
           to_status=TravellerStatus.REJECTED, reason="Already on another campaign")

    assert svc.request_is_editable(row) is False


def test_approving_everyone_settles_the_request(db, world):
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])

    for traveller in row.travellers:
        decide(db, actor=admin, request=row, traveller=traveller,
               to_status=TravellerStatus.APPROVED)
    assert svc.status_of(row) is RequestStatus.APPROVED


def test_an_approved_and_a_rejected_traveller_still_reads_as_approved(db, world):
    """Someone is going, so the request is live. The rejected person's own row
    carries their answer."""
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])

    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.APPROVED)
    decide(db, actor=admin, request=row, traveller=row.travellers[1],
           to_status=TravellerStatus.REJECTED, reason="Needed on the other site")
    assert svc.status_of(row) is RequestStatus.APPROVED


def test_is_decided_reports_when_the_queue_can_drop_it(db, world):
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])

    assert svc.to_read(db, row, tenant_id=TENANT).is_decided is False
    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.APPROVED)
    assert svc.to_read(db, row, tenant_id=TENANT).is_decided is False
    decide(db, actor=admin, request=row, traveller=row.travellers[1],
           to_status=TravellerStatus.APPROVED)
    assert svc.to_read(db, row, tenant_id=TENANT).is_decided is True


# ---------------------------------------------------------------------------
# The traveller is told
# ---------------------------------------------------------------------------


def test_every_decision_notifies_that_traveller_only(db, world):
    project, admin, ravi, meera = world
    row = make_stay(db, project, [ravi, meera])

    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.APPROVED)

    notes = db.execute(select(Notification)).scalars().all()
    # One event, two channels - and nothing at all for the other traveller.
    assert {u.user_id for u in notes} == {ravi.id}
    assert {str(n.channel) for n in notes} == {"IN_APP", "EMAIL"}
    assert all(n.kind == "REQUEST_APPROVED" for n in notes)


def test_a_rejection_notice_carries_the_reason(db, world):
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])

    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.REJECTED, reason="Campaign is over budget")

    notes = db.execute(select(Notification)).scalars().all()
    assert all("Campaign is over budget" in n.body for n in notes)
    assert all("Mumbai" in n.body for n in notes)


def test_the_decision_email_is_recorded_as_attempted(db, world, outbox):
    """A notification row that claims SENT has to have actually been handed to
    the transport - the ledger is worthless if it records intentions."""
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])

    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.REJECTED, reason="Campaign is over budget")

    mail = db.execute(
        select(Notification).where(Notification.channel == NotificationChannel.EMAIL)
    ).scalars().one()
    assert mail.status is NotificationStatus.SENT
    assert mail.attempts == 1
    assert mail.sent_at is not None
    assert mail.to_address == ravi.email
    assert len(outbox.messages) == 1
    assert outbox.messages[0]["to"] == ravi.email


# ---------------------------------------------------------------------------
# Expiry - the one status nothing could reach before Phase 4
# ---------------------------------------------------------------------------


def test_a_request_nobody_decided_expires_once_the_date_passes(db, world):
    project, admin, ravi, meera = world
    row = make_stay(
        db, project, [ravi, meera],
        check_in=clock.local_today() - timedelta(days=10),
        check_out=clock.local_today() - timedelta(days=7),
    )
    assert svc.status_of(row) is RequestStatus.EXPIRED


def test_a_partly_decided_request_does_not_expire(db, world):
    """"Expired" would lose the more useful fact that two of the three went."""
    project, admin, ravi, meera = world
    row = make_stay(
        db, project, [ravi, meera],
        check_in=clock.local_today() - timedelta(days=10),
        check_out=clock.local_today() - timedelta(days=7),
    )
    decide(db, actor=admin, request=row, traveller=row.travellers[0],
           to_status=TravellerStatus.APPROVED,
           conflict_override_reason="Historic record")
    assert svc.status_of(row) is RequestStatus.PARTIALLY_APPROVED


def test_a_future_request_never_expires(db, world):
    project, _, ravi, _ = world
    row = make_stay(db, project, [ravi])
    assert svc.status_of(row) is RequestStatus.SUBMITTED


def test_a_cancelled_past_request_stays_cancelled(db, world):
    project, _, ravi, _ = world
    row = make_stay(
        db, project, [ravi],
        check_in=clock.local_today() - timedelta(days=5),
        check_out=clock.local_today() - timedelta(days=3),
    )
    row.is_cancelled = True
    db.commit()
    assert svc.status_of(row) is RequestStatus.CANCELLED


def test_a_draft_that_went_stale_is_still_a_draft(db, world):
    project, _, ravi, _ = world
    row = make_stay(
        db, project, [ravi],
        check_in=clock.local_today() - timedelta(days=5),
        check_out=clock.local_today() - timedelta(days=3),
    )
    row.is_draft = True
    db.commit()
    assert svc.status_of(row) is RequestStatus.DRAFT


def test_booking_an_unapproved_traveller_gives_usable_advice(db, world):
    """The generic transition message says "cancel and raise a new request",
    which is the wrong fix here - the ticket-confirmation path hits this whenever
    an admin uploads before approving, and the answer is simply to approve."""
    project, admin, ravi, _ = world
    row = make_stay(db, project, [ravi])

    with pytest.raises(HTTPException) as caught:
        decide(db, actor=admin, request=row, traveller=row.travellers[0],
               to_status=TravellerStatus.BOOKED, booking_reference="PNR123")

    assert "Approve them first" in caught.value.detail
    assert "raise a new request" not in caught.value.detail
