"""Regression tests for NOTIF-01 and the notification half of QUE-04."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from hunt.database.model import Model
from hunt.notifications.channels import slack as slack_mod
from hunt.notifications.channels.database import DatabaseChannel
from hunt.notifications.channels.slack import SlackChannel, SlackMessage
from hunt.notifications.notifiable import Notifiable, _NotificationSender, _SendNotificationJob
from hunt.notifications.notification import Notification

HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


class _SlackNote(Notification):
    def __init__(self, webhook=HOOK):
        super().__init__()
        self.webhook = webhook

    def via(self, notifiable):
        return ["slack"]

    def to_slack(self, notifiable):
        msg = SlackMessage().content("deploy finished")
        return msg.to(self.webhook) if self.webhook else msg


class _Plain(Notifiable):
    def __init__(self, id_=1, slack=None):
        self.id = id_
        self._slack = slack

    def route_notification_for_slack(self):
        return self._slack


@pytest.fixture
def opener(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(slack_mod, "_opener", fake)
    monkeypatch.setattr(SlackChannel, "allowed_hosts", None)
    monkeypatch.delenv("SLACK_WEBHOOK_ALLOWED_HOSTS", raising=False)
    return fake


# ---------------------------------------------------------------------------
# Slack channel
# ---------------------------------------------------------------------------


class TestSlackChannel:
    def test_posts_json_to_webhook(self, opener):
        SlackChannel().send(_Plain(), _SlackNote())
        req = opener.open.call_args[0][0]
        assert req.full_url == HOOK
        assert req.get_method() == "POST"
        assert json.loads(req.data) == {"text": "deploy finished"}
        assert opener.open.call_args[1]["timeout"] == 10

    def test_webhook_from_notifiable_route(self, opener):
        SlackChannel().send(_Plain(slack=HOOK), _SlackNote(webhook=None))
        assert opener.open.call_args[0][0].full_url == HOOK

    def test_no_webhook_is_a_noop(self, opener):
        SlackChannel().send(_Plain(), _SlackNote(webhook=None))
        opener.open.assert_not_called()

    @pytest.mark.parametrize(
        "exc",
        [
            urllib.error.URLError("connection refused"),
            urllib.error.HTTPError(HOOK, 500, "Server Error", None, None),
            TimeoutError("timed out"),
        ],
    )
    def test_delivery_errors_are_logged_and_reraised(self, opener, exc):
        opener.open.side_effect = exc
        with patch("hunt.log.manager.Log.error") as log_error, pytest.raises(type(exc)):
            SlackChannel().send(_Plain(), _SlackNote())
        log_error.assert_called_once()
        # The webhook path is a credential and must not be logged.
        assert "XXXX" not in repr(log_error.call_args)

    @pytest.mark.parametrize(
        "url",
        [
            "http://hooks.slack.com/services/T/B/X",
            "file:///etc/passwd",
            "ftp://hooks.slack.com/x",
            "gopher://127.0.0.1:6379/_x",
            "hooks.slack.com/services/T/B/X",
            "https:///nohost",
            "https://hooks.slack.com/a b",
            "https://hooks.slack.com/a\r\nHost: evil",
        ],
    )
    def test_non_https_webhook_is_rejected_without_a_request(self, opener, url):
        with pytest.raises(ValueError):
            SlackChannel().send(_Plain(), _SlackNote(webhook=url))
        opener.open.assert_not_called()

    def test_any_https_host_allowed_by_default(self, opener):
        SlackChannel().send(_Plain(), _SlackNote(webhook="https://chat.example.com/hooks/abc"))
        opener.open.assert_called_once()

    def test_allowed_hosts_class_option(self, opener, monkeypatch):
        monkeypatch.setattr(SlackChannel, "allowed_hosts", ("hooks.slack.com",))
        SlackChannel().send(_Plain(), _SlackNote())
        assert opener.open.call_count == 1
        for url in ("https://169.254.169.254/latest/meta-data", "https://hooks.slack.com.evil.test/x"):
            with pytest.raises(ValueError, match="allowed"):
                SlackChannel().send(_Plain(), _SlackNote(webhook=url))
        assert opener.open.call_count == 1

    def test_allowed_hosts_env_option(self, opener, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_ALLOWED_HOSTS", "hooks.slack.com, Chat.Example.com")
        SlackChannel().send(_Plain(), _SlackNote(webhook="https://chat.example.com/hooks/abc"))
        with pytest.raises(ValueError):
            SlackChannel().send(_Plain(), _SlackNote(webhook="https://internal.example.com/x"))
        assert opener.open.call_count == 1

    def test_redirects_are_refused(self):
        handler = slack_mod._NoRedirect()
        req = MagicMock(full_url=HOOK)
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")
        assert any(isinstance(h, slack_mod._NoRedirect) for h in slack_mod._opener.handlers)

    def test_sync_notify_surfaces_slack_failure(self, opener):
        opener.open.side_effect = urllib.error.URLError("down")
        with patch("hunt.log.manager.Log.error"), pytest.raises(urllib.error.URLError):
            _Plain().notify(_SlackNote())


# ---------------------------------------------------------------------------
# notify_now and queueing
# ---------------------------------------------------------------------------


class _QueuedNote(Notification):
    should_queue = True

    def __init__(self, label="x"):
        super().__init__()
        self.label = label

    def via(self, notifiable):
        return ["mail", "database"]


class _Member(Model, Notifiable):
    table = "members"


class _NotANotification:
    def via(self, notifiable):
        return ["mail"]


class TestNotifyNow:
    def test_notify_now_bypasses_the_queue(self):
        from hunt.queue.manager import Queue

        driver = MagicMock()
        user = _Plain()
        note = _QueuedNote()
        with (
            patch.object(_NotificationSender, "_resolve", return_value=driver),
            patch.object(Queue, "push") as push,
        ):
            user.notify_now(note)
        push.assert_not_called()
        assert driver.send.call_count == 2
        driver.send.assert_called_with(user, note)

    def test_notify_queues_each_channel(self):
        from hunt.queue.manager import Queue

        driver = MagicMock()
        user = _Member().force_fill({"id": 3})
        with (
            patch.object(_NotificationSender, "_resolve", return_value=driver),
            patch.object(Queue, "push") as push,
        ):
            user.notify(_QueuedNote())
        driver.send.assert_not_called()
        jobs = [c[0][0] for c in push.call_args_list]
        assert [j.channel for j in jobs] == ["mail", "database"]
        assert all(isinstance(j, _SendNotificationJob) for j in jobs)

    def test_both_fakes_intercept_notify_now(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.testing.fakes import NotificationFake as TestingNotificationFake

        for fake_cls in (NotificationFake, TestingNotificationFake):
            user = _Plain()
            with patch.object(_NotificationSender, "_resolve") as resolve, fake_cls() as fake:
                user.notify_now(_QueuedNote())
                user.notify(_QueuedNote())
                fake.assert_sent_to(user, _QueuedNote)
            resolve.assert_not_called()
            assert len(fake._sent) == 2


class TestQueuedNotificationPayload:
    def test_payload_holds_model_reference_not_row_data(self):
        user = _Member().force_fill({"id": 9, "email": "m@example.com", "password": "bcrypt-hash"})
        job = _SendNotificationJob(_QueuedNote("hello"), user, "mail")
        assert isinstance(job.payload, str)
        assert "bcrypt-hash" not in job.payload
        assert "m@example.com" not in job.payload
        data = json.loads(job.payload)
        assert data["notifiable"] == {"class": f"{__name__}._Member", "key": 9}
        assert data["notification"]["class"] == f"{__name__}._QueuedNote"

    def test_worker_refetches_notifiable_and_rebuilds_notification(self):
        from hunt.queue.drivers.database import _serialize_job

        note = _QueuedNote("hello")
        job = _SendNotificationJob(note, _Member().force_fill({"id": 9}), "database")
        body = json.loads(json.dumps(_serialize_job(job)))
        rebuilt = _SendNotificationJob(**body["data"])

        fresh = _Member().force_fill({"id": 9, "email": "now@example.com"})
        driver = MagicMock()
        with (
            patch.object(_Member, "find", return_value=fresh) as find,
            patch.object(_NotificationSender, "_resolve", return_value=driver) as resolve,
        ):
            rebuilt.handle()
        find.assert_called_once_with(9)
        resolve.assert_called_once_with("database")
        notifiable, notification = driver.send.call_args[0]
        assert notifiable is fresh
        assert isinstance(notification, _QueuedNote)
        assert notification.label == "hello"
        assert notification.id == note.id

    @pytest.mark.parametrize("notifiable", [{"id": 7}, _Plain(), "user@example.com", None])
    def test_non_model_notifiable_raises_clear_error(self, notifiable):
        with pytest.raises(TypeError, match="must be a saved Model"):
            _SendNotificationJob(_QueuedNote(), notifiable, "mail")

    def test_queued_notify_to_non_model_raises_instead_of_sending(self):
        driver = MagicMock()
        with patch.object(_NotificationSender, "_resolve", return_value=driver), pytest.raises(TypeError):
            _Plain().notify(_QueuedNote())
        driver.send.assert_not_called()

    def test_unsaved_model_notifiable_raises(self):
        with pytest.raises(TypeError, match="primary key"):
            _SendNotificationJob(_QueuedNote(), _Member(), "mail")

    def test_deleted_notifiable_raises_lookup_error(self):
        job = _SendNotificationJob(_QueuedNote(), _Member().force_fill({"id": 9}), "mail")
        with patch.object(_Member, "find", return_value=None), pytest.raises(LookupError):
            job.handle()

    @pytest.mark.parametrize("path", ["subprocess.Popen", f"{__name__}._NotANotification", "os.system"])
    def test_worker_only_loads_notification_subclasses(self, path):
        payload = json.dumps(
            {"notification": {"class": path, "state": {}}, "notifiable": {"class": f"{__name__}._Member", "key": 1}}
        )
        with patch.object(_Member, "find") as find, pytest.raises(TypeError):
            _SendNotificationJob(payload=payload, channel="mail").handle()
        find.assert_not_called()

    def test_worker_only_loads_model_notifiables(self):
        payload = json.dumps(
            {
                "notification": {"class": f"{__name__}._QueuedNote", "state": {}},
                "notifiable": {"class": "subprocess.Popen", "key": "id"},
            }
        )
        with pytest.raises(TypeError):
            _SendNotificationJob(payload=payload, channel="mail").handle()

    def test_legacy_pickle_payload_is_refused(self):
        with pytest.raises(TypeError, match="pickle"):
            _SendNotificationJob(payload="gASVCAAAAAAAAACMBGV2aWyULg==", channel="mail").handle()


# ---------------------------------------------------------------------------
# Database notifications: scoping and type names
# ---------------------------------------------------------------------------


class _DbNote(Notification):
    def via(self, notifiable):
        return ["database"]

    def to_database(self, notifiable):
        return {"k": "v"}


class _Customer(Notifiable):
    def __init__(self, id_):
        self.id = id_


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with eng.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE notifications (id TEXT PRIMARY KEY, type TEXT, notifiable_id INTEGER, "
                "notifiable_type TEXT, data TEXT, read_at INTEGER, created_at INTEGER, updated_at INTEGER)"
            )
        )
        conn.commit()
    with patch("hunt.database.connection.connection", return_value=eng):
        yield eng
    eng.dispose()


def _rows(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, type, notifiable_id, notifiable_type, read_at FROM notifications"))
        return {r.id: r for r in result.fetchall()}


def _insert(engine, id_, notifiable_id, notifiable_type, type_="X"):
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO notifications VALUES (:id, :type, :nid, :ntype, '{\"a\": 1}', NULL, 1, 1)",
            ),
            {"id": id_, "type": type_, "nid": notifiable_id, "ntype": notifiable_type},
        )
        conn.commit()


class TestDatabaseNotifications:
    def test_rows_use_fully_qualified_type_names(self, engine):
        DatabaseChannel().send(_Customer(1), _DbNote())
        (row,) = _rows(engine).values()
        assert row.type == f"{__name__}._DbNote"
        assert row.notifiable_type == f"{__name__}._Customer"

    def test_new_rows_are_readable(self, engine):
        alice = _Customer(1)
        alice.notify(_DbNote())
        notes = alice.notifications()
        assert len(notes) == 1
        assert notes[0]["data"] == {"k": "v"}
        assert alice.unread_notifications() == notes
        assert alice.read_notifications() == []

    def test_legacy_bare_type_rows_are_still_read(self, engine):
        _insert(engine, "old", 1, "_Customer", "_DbNote")
        _insert(engine, "new", 1, f"{__name__}._Customer", f"{__name__}._DbNote")
        _insert(engine, "other-class", 1, "app.models.Other")
        _insert(engine, "other-id", 2, "_Customer")
        assert {n["id"] for n in _Customer(1).notifications()} == {"old", "new"}

    def test_mark_notification_read_is_scoped_to_the_notifiable(self, engine):
        alice, bob = _Customer(1), _Customer(2)
        _insert(engine, "a1", 1, f"{__name__}._Customer")
        _insert(engine, "b1", 2, f"{__name__}._Customer")

        alice.mark_notification_read("b1")  # somebody else's ID
        assert _rows(engine)["b1"].read_at is None

        alice.mark_notification_read("a1")
        rows = _rows(engine)
        assert rows["a1"].read_at is not None
        assert rows["b1"].read_at is None

        bob.mark_notification_read("b1")
        assert _rows(engine)["b1"].read_at is not None

    def test_mark_notification_read_ignores_same_id_other_type(self, engine):
        _insert(engine, "x1", 1, "app.models.Admin")
        _Customer(1).mark_notification_read("x1")
        assert _rows(engine)["x1"].read_at is None

    def test_mark_read_covers_legacy_rows(self, engine):
        _insert(engine, "old", 1, "_Customer")
        _insert(engine, "new", 1, f"{__name__}._Customer")
        _insert(engine, "bob", 2, "_Customer")
        alice = _Customer(1)
        alice.mark_notification_read("old")
        assert _rows(engine)["old"].read_at is not None
        alice.mark_notifications_read()
        rows = _rows(engine)
        assert rows["new"].read_at is not None
        assert rows["bob"].read_at is None
