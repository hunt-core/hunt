"""Regression tests for TEST-01 (MailFake) and TEST-02 (freeze_time)."""

from __future__ import annotations

import copy
import datetime
import time

import pytest

from hunt.mail.mailable import Mailable
from hunt.mail.manager import Mail, _MailFake
from hunt.testing import MailFake, freeze_time


class WelcomeMail(Mailable):
    def build(self):
        return self.subject("Welcome").text("hi")


class OtherMail(Mailable):
    pass


class _ExplodingTransport:
    def __init__(self):
        self.calls = 0

    def send(self, mailable, config):
        self.calls += 1
        raise AssertionError("real mail transport was reached while faking")


@pytest.fixture
def transport(monkeypatch):
    """Make the global Mail manager use a transport that must never be hit."""
    t = _ExplodingTransport()
    monkeypatch.setattr(Mail, "_fake", None)
    monkeypatch.setattr(Mail, "_resolve_driver_for", lambda config: t)
    return t


@pytest.fixture
def no_queue(monkeypatch):
    from hunt.queue.manager import Queue

    pushed = []
    monkeypatch.setattr(Queue, "push", lambda job: pushed.append(job))
    monkeypatch.setattr(Queue, "later", lambda delay, job: pushed.append(job))
    return pushed


# ---------------------------------------------------------------------------
# TEST-01
# ---------------------------------------------------------------------------


class TestMailFake:
    def test_precondition_real_transport_is_reached_without_the_fake(self, transport):
        with pytest.raises(AssertionError, match="real mail transport"):
            Mail.to("user@example.com").send(WelcomeMail())
        assert transport.calls == 1

    def test_send_is_intercepted_and_recorded(self, transport):
        with MailFake() as fake:
            Mail.to("user@example.com").send(WelcomeMail())
            Mail.send(WelcomeMail().to("second@example.com"))
            fake.assert_sent(WelcomeMail)
            fake.assert_sent(WelcomeMail, 2)
            fake.assert_sent(WelcomeMail, 1, lambda m: "second@example.com" in m._to_addresses)
            assert len(fake.sent(WelcomeMail)) == 2
            assert len(fake.sent()) == 2
        assert transport.calls == 0

    def test_build_runs_for_recorded_mailables(self, transport):
        with MailFake() as fake:
            Mail.to("user@example.com").send(WelcomeMail())
        assert fake.sent(WelcomeMail)[0]._subject_line == "Welcome"

    def test_assert_sent_to(self, transport):
        with MailFake() as fake:
            Mail.to("User <user@example.com>").cc("cc@example.com").bcc("bcc@example.com").send(WelcomeMail())
            fake.assert_sent_to("user@example.com")
            fake.assert_sent_to("USER@example.com", WelcomeMail)
            fake.assert_sent_to("cc@example.com", WelcomeMail)
            fake.assert_sent_to("bcc@example.com")
            with pytest.raises(AssertionError):
                fake.assert_sent_to("nobody@example.com")
            with pytest.raises(AssertionError):
                fake.assert_sent_to("user@example.com", OtherMail)

    def test_negative_assertions_can_fail(self, transport):
        with MailFake() as fake:
            fake.assert_nothing_sent()
            fake.assert_not_sent(WelcomeMail)
            with pytest.raises(AssertionError):
                fake.assert_sent(WelcomeMail)

            Mail.to("user@example.com").send(WelcomeMail())

            with pytest.raises(AssertionError):
                fake.assert_nothing_sent()
            with pytest.raises(AssertionError):
                fake.assert_not_sent(WelcomeMail)
            with pytest.raises(AssertionError):
                fake.assert_sent(WelcomeMail, 2)
            fake.assert_not_sent(OtherMail)

    def test_queue_and_later_are_intercepted(self, transport, no_queue):
        with MailFake() as fake:
            Mail.to("q@example.com").queue(WelcomeMail())
            Mail.to("l@example.com").later(60, WelcomeMail())
            Mail.to("s@example.com").send(OtherMail())
            fake.assert_sent(WelcomeMail, 2)
            fake.assert_queued(WelcomeMail, 2)
            fake.assert_sent_to("q@example.com", WelcomeMail)
            fake.assert_sent_to("l@example.com", WelcomeMail)
            assert fake.queued(OtherMail) == []
            with pytest.raises(AssertionError):
                fake.assert_queued(OtherMail)
        assert no_queue == []
        assert transport.calls == 0

    def test_queueing_under_the_fake_needs_no_serializable_state(self, transport, no_queue):
        class Local(Mailable):  # not importable: could never go through a real queue
            def __init__(self):
                super().__init__()
                self.handle = object()

        with MailFake() as fake:
            Mail.to("q@example.com").queue(Local())
            fake.assert_queued(Local)

    async def test_send_async_is_intercepted(self, transport):
        with MailFake() as fake:
            await Mail.to("user@example.com").send_async(WelcomeMail())
            fake.assert_sent_to("user@example.com", WelcomeMail)
        assert transport.calls == 0

    def test_real_transport_is_restored_on_exit(self, transport):
        with MailFake():
            assert Mail._fake is not None
        assert Mail._fake is None
        with pytest.raises(AssertionError, match="real mail transport"):
            Mail.to("user@example.com").send(WelcomeMail())

    def test_restored_even_when_the_block_raises(self, transport):
        with pytest.raises(RuntimeError), MailFake():
            raise RuntimeError("test failed")
        assert Mail._fake is None

    def test_assertions_remain_available_after_the_block(self, transport):
        with MailFake() as fake:
            Mail.to("user@example.com").send(WelcomeMail())
        fake.assert_sent(WelcomeMail, 1)

    def test_nested_fakes_restore_the_outer_one(self, transport):
        with MailFake() as outer:
            with MailFake() as inner:
                Mail.to("in@example.com").send(WelcomeMail())
            Mail.to("out@example.com").send(WelcomeMail())
            inner.assert_sent(WelcomeMail, 1)
            outer.assert_sent(WelcomeMail, 1)
            outer.assert_sent_to("out@example.com")
        assert Mail._fake is None
        assert transport.calls == 0

    def test_unentered_fake_has_nothing_sent(self):
        fake = MailFake()
        fake.assert_nothing_sent()
        with pytest.raises(AssertionError):
            fake.assert_sent(WelcomeMail)


class TestMailFakeContextManager:
    def test_mail_fake_as_context_manager(self, transport):
        with Mail.fake() as fake:
            assert isinstance(fake, _MailFake)
            assert Mail._fake is fake
            Mail.to("user@example.com").send(WelcomeMail())
            fake.assert_sent(WelcomeMail)
        assert Mail._fake is None
        assert transport.calls == 0

    def test_context_manager_restores_on_error(self, transport):
        with pytest.raises(RuntimeError), Mail.fake():
            raise RuntimeError("boom")
        assert Mail._fake is None

    def test_plain_call_form_still_works(self, transport):
        fake = Mail.fake()
        try:
            Mail.to("user@example.com").send(WelcomeMail())
            fake.assert_sent(WelcomeMail)
            assert Mail.sent() == fake.sent()
        finally:
            Mail.stop_faking()
        assert Mail._fake is None

    def test_exiting_a_stale_fake_does_not_remove_a_newer_one(self, transport):
        stale = Mail.fake()
        current = Mail.fake()
        try:
            with stale:
                pass
            assert Mail._fake is current
        finally:
            Mail.stop_faking()


# ---------------------------------------------------------------------------
# TEST-02
# ---------------------------------------------------------------------------

UTC = datetime.UTC
REAL_DATETIME = datetime.datetime
REAL_DATE = datetime.date


class TestFreezeTime:
    def test_frozen_values_are_real_datetime_instances(self):
        frozen = REAL_DATETIME(2026, 1, 1, 12, 0, 0)
        with freeze_time(frozen):
            now = datetime.datetime.now()
            assert isinstance(now, datetime.datetime)
            assert isinstance(now, REAL_DATETIME)
            # Plain datetimes, so DB drivers and serializers that look up the exact type accept them.
            assert type(now) is REAL_DATETIME
            assert type(datetime.datetime(2026, 1, 1)) is REAL_DATETIME
            assert type(datetime.datetime.now(UTC) + datetime.timedelta(1)) is REAL_DATETIME
            assert type(datetime.datetime.fromisoformat("2026-01-01T00:00:00")) is REAL_DATETIME
            assert type(datetime.datetime.strptime("2026-01-01", "%Y-%m-%d")) is REAL_DATETIME
            assert type(datetime.datetime.combine(datetime.date(2026, 1, 1), datetime.time(1))) is REAL_DATETIME
            assert type(datetime.date.today()) is REAL_DATE
            assert type(datetime.date(2026, 1, 1)) is REAL_DATE
            assert issubclass(REAL_DATETIME, datetime.datetime)
            assert issubclass(datetime.datetime, datetime.date)
            assert not issubclass(REAL_DATE, datetime.datetime)
            assert isinstance(datetime.datetime.utcnow(), datetime.datetime)
            assert isinstance(datetime.datetime.now(UTC), datetime.datetime)
            assert isinstance(datetime.date.today(), datetime.date)
            assert now == frozen

    def test_ordinary_datetimes_still_pass_isinstance_while_frozen(self):
        before = REAL_DATETIME(2020, 5, 5)
        with freeze_time(REAL_DATETIME(2026, 1, 1)):
            assert isinstance(before, datetime.datetime)
            assert isinstance(before.date(), datetime.date)
            assert isinstance(datetime.datetime(2021, 1, 1), datetime.datetime)
            assert isinstance(datetime.datetime.fromisoformat("2021-01-01T00:00:00"), datetime.datetime)
            assert not isinstance("2026-01-01", datetime.datetime)
            assert not isinstance(before.date(), datetime.datetime)

    def test_now_with_tz_converts_instead_of_relabelling(self):
        frozen = REAL_DATETIME(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        plus2 = datetime.timezone(datetime.timedelta(hours=2))
        minus5 = datetime.timezone(datetime.timedelta(hours=-5))
        with freeze_time(frozen):
            east = datetime.datetime.now(plus2)
            west = datetime.datetime.now(tz=minus5)
            assert (east.hour, east.utcoffset()) == (14, datetime.timedelta(hours=2))
            assert (west.hour, west.utcoffset()) == (7, datetime.timedelta(hours=-5))
            assert east == frozen == west
            assert datetime.datetime.now(UTC) == frozen
            assert datetime.datetime.now(UTC).hour == 12

    def test_naive_frozen_value_is_local_time(self):
        frozen = REAL_DATETIME(2026, 6, 15, 9, 30, 0)
        with freeze_time(frozen):
            assert datetime.datetime.now() == frozen
            assert datetime.datetime.now().tzinfo is None
            assert datetime.datetime.today() == frozen
            assert datetime.datetime.now(UTC) == frozen.astimezone(UTC)
            assert datetime.datetime.utcnow() == frozen.astimezone(UTC).replace(tzinfo=None)
            assert datetime.date.today() == frozen.date()

    def test_aware_frozen_value_gives_local_naive_now(self):
        frozen = REAL_DATETIME(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with freeze_time(frozen):
            assert datetime.datetime.now() == frozen.astimezone().replace(tzinfo=None)
            assert datetime.datetime.utcnow() == REAL_DATETIME(2026, 1, 1, 12, 0, 0)

    def test_time_functions(self):
        frozen = REAL_DATETIME(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with freeze_time(frozen):
            assert time.time() == frozen.timestamp()
            assert time.time_ns() == int(frozen.timestamp()) * 1_000_000_000
            assert time.monotonic() == frozen.timestamp()
            assert datetime.datetime.fromtimestamp(time.time(), UTC) == frozen

    def test_arithmetic_and_formatting(self):
        frozen = REAL_DATETIME(2026, 1, 31, 23, 0, 0)
        with freeze_time(frozen):
            later = datetime.datetime.now() + datetime.timedelta(hours=2)
            assert later == REAL_DATETIME(2026, 2, 1, 1, 0, 0)
            assert isinstance(later, datetime.datetime)
            assert datetime.datetime.now().isoformat() == "2026-01-31T23:00:00"
            assert datetime.datetime.now().strftime("%Y-%m-%d") == "2026-01-31"
            assert (datetime.datetime.now() - REAL_DATETIME(2026, 1, 31)).total_seconds() == 23 * 3600
            assert datetime.datetime.now().date() == REAL_DATE(2026, 1, 31)

    def test_frozen_values_can_be_copied(self):
        with freeze_time(REAL_DATETIME(2026, 1, 1, 12)):
            now = datetime.datetime.now()
            assert copy.deepcopy(now) == now
            assert copy.copy(now) == now
            assert now.replace(year=2027) == REAL_DATETIME(2027, 1, 1, 12)

    def test_frozen_values_go_through_the_cache_codec(self):
        from hunt.cache.manager import ArrayStore

        store = ArrayStore()
        with freeze_time(REAL_DATETIME(2026, 1, 1, 12, tzinfo=UTC)) as frozen:
            store.put("k", {"at": datetime.datetime.now(UTC), "day": datetime.date.today()}, 60)
            assert store.get("k")["at"] == frozen
            assert store.get("k")["day"] == REAL_DATE(2026, 1, 1)
        # Back on the real clock the 60s TTL set in January 2026 has long passed.
        assert store.get("k", "expired") == "expired"

    def test_framework_modules_that_imported_datetime_by_name_are_frozen(self):
        import hunt.scheduling.cron as cron
        import hunt.scheduling.scheduler as scheduler

        assert scheduler.datetime is REAL_DATETIME
        frozen = REAL_DATETIME(2026, 3, 9, 8, 15, 0)
        with freeze_time(frozen):
            assert scheduler.datetime is not REAL_DATETIME
            assert scheduler.datetime.now() == frozen
            assert cron.datetime.now() == frozen
            assert isinstance(scheduler.datetime.now(), REAL_DATETIME)
        assert scheduler.datetime is REAL_DATETIME
        assert cron.datetime is REAL_DATETIME

    def test_scheduler_sees_the_frozen_clock(self):
        import hunt.scheduling.scheduler as scheduler
        from hunt.scheduling.cron import matches

        frozen = REAL_DATETIME(2026, 3, 9, 8, 15, 0)
        with freeze_time(frozen):
            now = scheduler.datetime.now()
            assert matches("15 8 * * *", now)
            assert not matches("16 8 * * *", now)

    def test_extra_modules_can_be_frozen(self):
        import sys
        import types

        mod = types.ModuleType("fix_services_fake_app.clock")
        mod.datetime = REAL_DATETIME
        mod.dt_alias = REAL_DATETIME
        mod.date = REAL_DATE
        mod.other = "untouched"
        sys.modules[mod.__name__] = mod
        try:
            frozen = REAL_DATETIME(2026, 1, 1)
            with freeze_time(frozen):
                assert mod.datetime.now() != frozen  # not listed: keeps the real clock
            with freeze_time(frozen, modules=["fix_services_fake_app"]):
                assert mod.datetime.now() == frozen
                assert mod.dt_alias.now() == frozen
                assert mod.date.today() == REAL_DATE(2026, 1, 1)
                assert mod.other == "untouched"
            assert mod.datetime is REAL_DATETIME and mod.dt_alias is REAL_DATETIME and mod.date is REAL_DATE
        finally:
            del sys.modules[mod.__name__]

    def test_everything_is_restored_after_the_block(self):
        frozen = REAL_DATETIME(2000, 1, 1)
        with freeze_time(frozen):
            pass
        assert datetime.datetime is REAL_DATETIME
        assert datetime.date is REAL_DATE
        assert time.time() != frozen.timestamp()
        assert datetime.datetime.now().year != 2000

    def test_restored_when_the_block_raises(self):
        with pytest.raises(RuntimeError), freeze_time(REAL_DATETIME(2000, 1, 1)):
            raise RuntimeError("boom")
        assert datetime.datetime is REAL_DATETIME
        assert time.time() > REAL_DATETIME(2020, 1, 1).timestamp()

    def test_nested_freezes(self):
        outer = REAL_DATETIME(2026, 1, 1)
        inner = REAL_DATETIME(2030, 6, 6)
        with freeze_time(outer):
            with freeze_time(inner):
                assert datetime.datetime.now() == inner
                assert time.time() == inner.timestamp()
            assert datetime.datetime.now() == outer
            assert time.time() == outer.timestamp()

    def test_class_captured_inside_a_block_behaves_normally_afterwards(self):
        with freeze_time(REAL_DATETIME(2000, 1, 1)):
            captured = datetime.datetime
            captured_date = datetime.date
        assert captured.now().year >= 2025
        assert captured.utcnow().year >= 2025
        assert captured_date.today().year >= 2025

    def test_yields_the_given_value_and_defaults_to_now(self):
        target = REAL_DATETIME(2026, 3, 10, 8, 0, 0)
        with freeze_time(target) as dt:
            assert dt is target
        before = time.time()
        with freeze_time() as dt:
            assert dt.timestamp() >= before
            assert time.time() == dt.timestamp()
