"""
Scheduled reminders, preferences, and read state (SOW section 4, addendum C3).

The hard part of a reminder system is not sending, it is not sending. Almost
every test here is about something the jobs must *decline* to do: repeat
themselves, chase a trip that has already happened, nudge someone who is still
waiting on a decision, or email a category the person switched off.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select

from app.core.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    RequestType,
    Role,
    TravellerStatus,
    category_of,
)
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications, reminders

TENANT = "designboxed"


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")

    def person(name, email, role=Role.GROUND_STAFF):
        user = User(
            tenant_id=TENANT, email=email, full_name=name, role=role, password_hash="x"
        )
        db.add(user)
        return user

    admin = person("Priya Shah", "priya@designboxed.com", Role.ADMIN)
    ravi = person("Ravi Kumar", "ravi@designboxed.com")
    db.add(project)
    db.commit()
    return project, admin, ravi


def make_trip(
    db,
    project,
    user,
    *,
    days_out=1,
    status=TravellerStatus.BOOKED,
    submitted_days_ago=0,
    reference="QK8T2M",
    cancelled=False,
    draft=False,
):
    start = date.today() + timedelta(days=days_out)
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=user.id,
        hotel_city="Mumbai",
        check_in=start,
        check_out=start + timedelta(days=2),
        is_cancelled=cancelled,
        is_draft=draft,
        submitted_at=naive_utcnow() - timedelta(days=submitted_days_ago),
    )
    row.travellers = [
        RequestTraveller(user_id=user.id, status=status, booking_reference=reference)
    ]
    db.add(row)
    db.commit()
    return row


def notices(db, *, kind=None, channel=None):
    stmt = select(Notification)
    rows = db.execute(stmt).scalars().all()
    if kind:
        rows = [r for r in rows if r.kind == kind]
    if channel:
        rows = [r for r in rows if r.channel is channel]
    return rows


# ---------------------------------------------------------------------------
# Travel reminders
# ---------------------------------------------------------------------------


def test_a_booked_trip_two_days_out_is_reminded(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=2)

    result = reminders.remind_travellers(db, TENANT)
    assert result["notified"] == 1

    in_app = notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.IN_APP)
    assert len(in_app) == 1
    assert "Mumbai" in in_app[0].body
    assert "QK8T2M" in in_app[0].body


def test_a_trip_further_out_than_the_horizon_is_left_alone(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=reminders.TRAVEL_REMINDER_DAYS + 1)
    assert reminders.remind_travellers(db, TENANT)["notified"] == 0


def test_a_trip_in_the_past_is_not_reminded(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=-3)
    assert reminders.remind_travellers(db, TENANT)["notified"] == 0


@pytest.mark.parametrize(
    "status,reminded",
    [
        (TravellerStatus.BOOKED, True),
        # Still waiting on a decision: reminding them of a trip that may not
        # happen is worse than saying nothing.
        (TravellerStatus.PENDING, False),
        # Approved but unticketed means the admin needs chasing, not the
        # traveller.
        (TravellerStatus.APPROVED, False),
        (TravellerStatus.REJECTED, False),
        (TravellerStatus.CANCELLED, False),
    ],
)
def test_only_booked_travellers_are_reminded(db, world, status, reminded):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1, status=status)
    assert bool(reminders.remind_travellers(db, TENANT)["notified"]) is reminded


def test_a_cancelled_trip_is_not_reminded(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1, cancelled=True)
    assert reminders.remind_travellers(db, TENANT)["notified"] == 0


def test_running_the_job_twice_reminds_once(db, world):
    """The whole point of the dedupe key. A loop every ten minutes must not
    produce a reminder every ten minutes."""
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1)

    assert reminders.remind_travellers(db, TENANT)["notified"] == 1
    for _ in range(4):
        assert reminders.remind_travellers(db, TENANT)["notified"] == 0

    assert len(notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.IN_APP)) == 1


def test_two_trips_for_the_same_person_are_reminded_separately(db, world):
    """Deduplication is per event, not per person - otherwise the second trip
    would silently never be mentioned."""
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1)
    make_trip(db, project, ravi, days_out=2)

    assert reminders.remind_travellers(db, TENANT)["notified"] == 2


def test_someone_who_has_left_is_not_reminded(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1)
    ravi.is_active = False
    db.commit()
    assert reminders.remind_travellers(db, TENANT)["notified"] == 0


# ---------------------------------------------------------------------------
# Stale request nudges
# ---------------------------------------------------------------------------


def test_admins_are_told_about_a_request_nobody_decided(db, world):
    project, admin, ravi = world
    make_trip(
        db, project, ravi,
        days_out=10,
        status=TravellerStatus.PENDING,
        submitted_days_ago=reminders.STALE_AFTER_DAYS + 1,
    )

    result = reminders.remind_admins_of_stale_requests(db, TENANT)
    assert result["stale"] == 1
    assert result["notified"] == 1

    sent = notices(db, kind="REQUEST_STALE", channel=NotificationChannel.IN_APP)
    assert sent[0].user_id == admin.id


def test_a_request_submitted_recently_is_not_chased(db, world):
    project, _, ravi = world
    make_trip(
        db, project, ravi,
        days_out=10,
        status=TravellerStatus.PENDING,
        submitted_days_ago=reminders.STALE_AFTER_DAYS - 1,
    )
    assert reminders.remind_admins_of_stale_requests(db, TENANT)["stale"] == 0


def test_an_already_decided_request_is_not_chased(db, world):
    project, _, ravi = world
    make_trip(
        db, project, ravi,
        days_out=10,
        status=TravellerStatus.APPROVED,
        submitted_days_ago=10,
    )
    assert reminders.remind_admins_of_stale_requests(db, TENANT)["stale"] == 0


def test_a_stale_request_whose_travel_has_passed_is_not_chased(db, world):
    """Chasing an admin about last week helps nobody, and the request already
    reads as EXPIRED."""
    project, _, ravi = world
    make_trip(
        db, project, ravi,
        days_out=-2,
        status=TravellerStatus.PENDING,
        submitted_days_ago=10,
    )
    assert reminders.remind_admins_of_stale_requests(db, TENANT)["stale"] == 0


def test_a_stale_request_is_reported_once_not_daily(db, world):
    project, _, ravi = world
    make_trip(
        db, project, ravi,
        days_out=10,
        status=TravellerStatus.PENDING,
        submitted_days_ago=5,
    )

    assert reminders.remind_admins_of_stale_requests(db, TENANT)["notified"] == 1
    assert reminders.remind_admins_of_stale_requests(db, TENANT)["notified"] == 0


def test_every_admin_is_told_but_ground_staff_are_not(db, world):
    project, admin, ravi = world
    second = User(
        tenant_id=TENANT, email="raj@designboxed.com", full_name="Raj Menon",
        role=Role.SYSTEM_ADMIN, password_hash="x",
    )
    db.add(second)
    db.commit()

    make_trip(
        db, project, ravi,
        days_out=10, status=TravellerStatus.PENDING, submitted_days_ago=5,
    )
    reminders.remind_admins_of_stale_requests(db, TENANT)

    told = {n.user_id for n in notices(db, kind="REQUEST_STALE")}
    assert told == {admin.id, second.id}
    assert ravi.id not in told


# ---------------------------------------------------------------------------
# Running everything
# ---------------------------------------------------------------------------


def test_run_all_reports_each_job(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1)

    results = reminders.run_all(db, TENANT)
    assert [r["job"] for r in results] == [
        "remind_travellers",
        "remind_admins_of_stale_requests",
        "retry_undelivered",
    ]


def test_one_failing_job_does_not_stop_the_others(db, world, monkeypatch):
    """A broken reminder must not also stop refused email being retried."""
    def explode(*args, **kwargs):
        raise RuntimeError("job is broken")

    monkeypatch.setattr(reminders, "remind_travellers", explode)
    monkeypatch.setattr(reminders, "JOBS", (reminders.remind_travellers,
                                            reminders.remind_admins_of_stale_requests,
                                            reminders.retry_undelivered))

    results = reminders.run_all(db, TENANT)
    assert "error" in results[0]
    assert results[1]["job"] == "remind_admins_of_stale_requests"
    assert results[2]["job"] == "retry_undelivered"


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_everything_is_on_for_someone_with_no_preferences(db, world):
    _, _, ravi = world
    assert set(notifications.preferences_for(db, ravi).values()) == {True}


def test_switching_a_category_off_stops_the_email_but_keeps_the_row(db, world, outbox):
    """The in-app row is the record. Hiding it would hide the trail from the
    person it is about."""
    project, _, ravi = world
    notifications.set_preference(
        db, user=ravi, category=NotificationCategory.REMINDERS, enabled=False
    )
    db.commit()

    make_trip(db, project, ravi, days_out=1)
    reminders.remind_travellers(db, TENANT)

    assert len(notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.IN_APP)) == 1
    assert notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.EMAIL) == []
    assert outbox.messages == []


def test_switching_it_back_on_restores_the_email(db, world, outbox):
    project, _, ravi = world
    notifications.set_preference(
        db, user=ravi, category=NotificationCategory.REMINDERS, enabled=False
    )
    notifications.set_preference(
        db, user=ravi, category=NotificationCategory.REMINDERS, enabled=True
    )
    db.commit()

    make_trip(db, project, ravi, days_out=1)
    reminders.remind_travellers(db, TENANT)
    assert len(notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.EMAIL)) == 1


def test_toggling_twice_leaves_one_preference_row(db, world):
    _, _, ravi = world
    for enabled in (False, True, False):
        notifications.set_preference(
            db, user=ravi, category=NotificationCategory.BOOKINGS, enabled=enabled
        )
    db.commit()
    assert notifications.preferences_for(db, ravi)["BOOKINGS"] is False


def test_decisions_cannot_be_switched_off(db, world):
    """An opt-out here would produce staff who turn up at airports."""
    _, _, ravi = world
    with pytest.raises(ValueError):
        notifications.set_preference(
            db, user=ravi, category=NotificationCategory.DECISIONS, enabled=False
        )


def test_decisions_are_sent_even_if_a_row_somehow_says_otherwise(db, world):
    """Defence in depth: the guard is in `wants`, not only in the setter, so a
    hand-edited table cannot silence a decision."""
    from app.models.preference import NotificationPreference

    _, _, ravi = world
    db.add(
        NotificationPreference(
            user_id=ravi.id,
            category=NotificationCategory.DECISIONS,
            channel=NotificationChannel.EMAIL,
            enabled=False,
        )
    )
    db.commit()

    assert notifications.wants(
        db, ravi, NotificationCategory.DECISIONS, NotificationChannel.EMAIL
    ) is True


def test_preferences_are_per_person(db, world, outbox):
    project, admin, ravi = world
    notifications.set_preference(
        db, user=ravi, category=NotificationCategory.REMINDERS, enabled=False
    )
    db.commit()

    make_trip(db, project, admin, days_out=1)
    reminders.remind_travellers(db, TENANT)

    # Priya never opted out, so her reminder still goes.
    assert len(notices(db, kind="TRAVEL_REMINDER", channel=NotificationChannel.EMAIL)) == 1


# ---------------------------------------------------------------------------
# Categories and read state
# ---------------------------------------------------------------------------


def test_every_known_kind_has_a_category():
    for kind in (
        "REQUEST_APPROVED", "REQUEST_REJECTED", "BOOKING_CONFIRMED",
        "COSTAY_REQUESTED", "TRAVEL_REMINDER", "REQUEST_STALE",
    ):
        assert isinstance(category_of(kind), NotificationCategory)


def test_an_unknown_kind_falls_back_to_decisions():
    """The safest default: decisions are the notices someone would most regret
    not receiving."""
    assert category_of("SOMETHING_NEW") is NotificationCategory.DECISIONS


def test_the_category_is_stored_on_the_row(db, world):
    project, _, ravi = world
    make_trip(db, project, ravi, days_out=1)
    reminders.remind_travellers(db, TENANT)

    row = notices(db, kind="TRAVEL_REMINDER")[0]
    assert row.category is NotificationCategory.REMINDERS


def test_unread_counts_only_in_app_notices(db, world, outbox):
    _, _, ravi = world
    notifications.notify(
        db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED", title="Hi", body="Body"
    )
    db.commit()

    # Two rows were written, one of them email; only the in-app one is unread.
    assert notifications.unread_count(db, ravi) == 1


def test_marking_one_read_leaves_the_others(db, world, outbox):
    _, _, ravi = world
    for i in range(3):
        notifications.notify(
            db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED",
            title=f"Notice {i}", body="Body",
        )
    db.commit()
    assert notifications.unread_count(db, ravi) == 3

    first = notices(db, channel=NotificationChannel.IN_APP)[0]
    assert notifications.mark_read(db, user=ravi, notification_id=first.id) == 1
    assert notifications.unread_count(db, ravi) == 2


def test_marking_all_read_clears_the_bell(db, world, outbox):
    _, _, ravi = world
    for i in range(3):
        notifications.notify(
            db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED",
            title=f"Notice {i}", body="Body",
        )
    db.commit()

    assert notifications.mark_read(db, user=ravi) == 3
    assert notifications.unread_count(db, ravi) == 0


def test_marking_read_twice_is_a_no_op(db, world, outbox):
    _, _, ravi = world
    notifications.notify(
        db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED", title="Hi", body="Body"
    )
    db.commit()

    assert notifications.mark_read(db, user=ravi) == 1
    assert notifications.mark_read(db, user=ravi) == 0


def test_one_person_cannot_mark_another_persons_notice_read(db, world, outbox):
    project, admin, ravi = world
    notifications.notify(
        db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED", title="Hi", body="Body"
    )
    db.commit()

    theirs = notices(db, channel=NotificationChannel.IN_APP)[0]
    assert notifications.mark_read(db, user=admin, notification_id=theirs.id) == 0
    assert notifications.unread_count(db, ravi) == 1


def test_the_ledger_summary_separates_channels(db, world, outbox):
    _, _, ravi = world
    notifications.notify(
        db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED", title="Hi", body="Body"
    )
    db.commit()

    summary = notifications.ledger_summary(db, TENANT)
    assert summary["total"] == 2
    assert summary["emails"] == 1
    assert summary["by_status"][str(NotificationStatus.SENT)] == 2
