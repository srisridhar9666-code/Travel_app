"""
India time: which date "today" is, and how recorded moments are written out.

The bug these pin down: server timestamps are naive UTC, and a browser reads a
naive ISO string as its own local time - so in India every recorded time showed
five and a half hours early. Trip times are the other kind of time and must not
move at all.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core import clock
from app.core.logging import LocalTimeFormatter
from app.schemas.request import NotificationRead, RequestBody
from app.schemas.user import AuditRead


class TestToday:
    def test_after_half_past_six_utc_it_is_already_tomorrow_in_india(self):
        assert clock.local_today(datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc)) == date(2026, 10, 2)

    def test_before_that_it_is_the_same_day(self):
        assert clock.local_today(datetime(2026, 10, 1, 18, 29, tzinfo=timezone.utc)) == date(2026, 10, 1)

    def test_a_naive_time_is_taken_as_utc(self):
        assert clock.local_today(datetime(2026, 10, 1, 19, 0)) == date(2026, 10, 2)

    def test_an_unknown_zone_falls_back_to_ist(self, monkeypatch):
        """Windows Python has no zone database, so even Asia/Kolkata can fail to
        resolve there. India has no DST, so +05:30 is exact."""
        def missing(_name):
            raise clock.ZoneInfoNotFoundError("no tzdata")

        monkeypatch.setattr(clock, "ZoneInfo", missing)
        clock.app_tz.cache_clear()
        try:
            assert clock.app_tz().utcoffset(None) == timedelta(hours=5, minutes=30)
            assert clock.zone_problem() is None   # IST is what was asked for
        finally:
            clock.app_tz.cache_clear()

    def test_a_mistyped_zone_falls_back_and_logging_still_works(self, monkeypatch, caplog):
        """The console formatter asks for the zone on every line, so the lookup
        must never log - a warning from inside it used to recurse until Python
        gave up, on the first log line of the API."""
        monkeypatch.setattr(clock.get_settings(), "app_timezone", "Asia/Kolkatta")
        clock.app_tz.cache_clear()
        try:
            with caplog.at_level(logging.DEBUG):
                assert clock.app_tz() is clock.IST
            assert caplog.records == []
            formatter = LocalTimeFormatter("%(asctime)s %(message)s")
            record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
            assert formatter.format(record).endswith("hello")
            assert "Asia/Kolkatta" in clock.zone_problem()
        finally:
            clock.app_tz.cache_clear()


class TestInstantsAreMarkedUtc:
    def test_a_naive_utc_instant_gets_a_z(self):
        assert clock.utc_iso(datetime(2026, 10, 1, 12, 0)) == "2026-10-01T12:00:00Z"

    def test_an_aware_india_time_is_converted(self):
        ist = datetime(2026, 10, 1, 17, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert clock.utc_iso(ist) == "2026-10-01T12:00:00Z"

    def test_the_local_view_is_india(self):
        assert clock.to_local(datetime(2026, 10, 1, 19, 0)).strftime("%d %H:%M") == "02 00:30"

    def test_a_filter_bound_is_converted_not_stripped(self):
        ist = datetime(2026, 10, 2, 0, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert clock.to_utc_naive(ist) == datetime(2026, 10, 1, 19, 0)

    def test_response_schemas_send_recorded_times_as_utc(self):
        notice = NotificationRead(
            id=1, kind="TEST", title="t", body="b", request_id=None,
            created_at=datetime(2026, 10, 1, 12, 0, 0, 123456), read_at=None,
        )
        assert '"created_at":"2026-10-01T12:00:00.123456Z"' in notice.model_dump_json()

    def test_the_audit_feed_too(self):
        row = AuditRead.model_validate({
            "id": 1, "created_at": datetime(2026, 10, 1, 12, 0), "actor_user_id": None,
            "actor_name": None, "actor_email": None, "actor_role": None,
            "action": "LOGIN", "entity_type": "user", "entity_id": None,
            "summary": "s", "reason": None, "changes": None, "ip_address": None,
            "request_id": None,
        })
        assert row.model_dump(mode="json")["created_at"] == "2026-10-01T12:00:00Z"

    def test_python_side_values_stay_datetimes(self):
        """Only the JSON is a string; code that reads the model still gets a
        datetime to compare with."""
        notice = NotificationRead(
            id=1, kind="TEST", title="t", body="b", request_id=None,
            created_at=datetime(2026, 10, 1, 12, 0), read_at=None,
        )
        assert isinstance(notice.model_dump()["created_at"], datetime)


class TestTripTimesDoNotMove:
    def test_a_typed_departure_is_kept_as_typed(self):
        body = RequestBody(
            request_type="LONG_DISTANCE", project_id=1, mode="FLIGHT",
            origin="Hyderabad", destination="Pune", travel_reason="Audit",
            start_at="2026-10-05T06:00",
        )
        assert body.model_dump(mode="json")["start_at"] == "2026-10-05T06:00:00"
