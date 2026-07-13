"""Phase C tests: Mail & Notifications."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hunt.mail.mailable import Mailable
from hunt.mail.manager import _ArrayDriver, _build_mime, _LogDriver, _MailManager
from hunt.mail.message import MailMessage

# ===========================================================================
# Mailable — fluent builder
# ===========================================================================


class TestMailable:
    def test_to_single_address(self):
        m = Mailable().to("a@example.com")
        assert m._to_addresses == ["a@example.com"]

    def test_to_multiple_addresses(self):
        m = Mailable().to(["a@b.com", "c@d.com"])
        assert m._to_addresses == ["a@b.com", "c@d.com"]

    def test_to_chained(self):
        m = Mailable().to("a@b.com").to("c@d.com")
        assert len(m._to_addresses) == 2

    def test_cc_and_bcc(self):
        m = Mailable().cc("cc@b.com").bcc("bcc@b.com")
        assert m._cc_addresses == ["cc@b.com"]
        assert m._bcc_addresses == ["bcc@b.com"]

    def test_reply_to(self):
        m = Mailable().reply_to("reply@b.com")
        assert m._reply_to_addresses == ["reply@b.com"]

    def test_from_address(self):
        m = Mailable().from_address("from@b.com", "Sender")
        assert m._from_addr == "from@b.com"
        assert m._from_name == "Sender"

    def test_subject(self):
        m = Mailable().subject("Hello World")
        assert m._subject_line == "Hello World"

    def test_view_stores_template_and_data(self):
        m = Mailable().view("emails.welcome", {"user": "Alice"})
        assert m._view_name == "emails.welcome"
        assert m._view_data == {"user": "Alice"}

    def test_html_content(self):
        m = Mailable().html("<h1>Hi</h1>")
        assert m._html_content == "<h1>Hi</h1>"

    def test_text_content(self):
        m = Mailable().text("Plain text")
        assert m._text_content == "Plain text"

    def test_attach(self):
        m = Mailable().attach("/tmp/invoice.pdf")
        assert m._attachments[0]["file"] == "/tmp/invoice.pdf"

    def test_fluent_chain_returns_self(self):
        m = Mailable()
        result = m.subject("Hi").to("a@b.com").html("<p>Hi</p>")
        assert result is m

    def test_render_html_content(self):
        m = Mailable().html("<p>test</p>")
        assert m.render() == "<p>test</p>"

    def test_render_text_fallback(self):
        m = Mailable().text("plain")
        assert m.render() == "plain"

    def test_render_empty_without_content(self):
        m = Mailable()
        assert m.render() == ""

    def test_build_hook_called(self):
        class MyMail(Mailable):
            built = False

            def build(self):
                MyMail.built = True
                return self.subject("Built")

        m = MyMail()
        m._build_and_prepare()
        assert MyMail.built is True
        assert m._subject_line == "Built"

    def test_build_and_prepare_fills_from_global(self):
        m = Mailable()
        m._build_and_prepare(global_from=("global@b.com", "Global"))
        assert m._from_addr == "global@b.com"
        assert m._from_name == "Global"

    def test_build_and_prepare_does_not_override_set_from(self):
        m = Mailable().from_address("local@b.com", "Local")
        m._build_and_prepare(global_from=("global@b.com", "Global"))
        assert m._from_addr == "local@b.com"


# ===========================================================================
# MailMessage (notification builder)
# ===========================================================================


class TestMailMessage:
    def test_greeting(self):
        m = MailMessage().greeting("Hey there!")
        assert m._greeting == "Hey there!"

    def test_line_appends(self):
        m = MailMessage().line("First").line("Second")
        assert m._intro_lines == ["First", "Second"]

    def test_outro(self):
        m = MailMessage().outro("Thanks!")
        assert m._outro_lines == ["Thanks!"]

    def test_action(self):
        m = MailMessage().action("Click Me", "https://example.com")
        assert m._action_text == "Click Me"
        assert m._action_url == "https://example.com"

    def test_error_flag(self):
        m = MailMessage().error()
        assert m._error is True

    def test_render_contains_greeting(self):
        out = MailMessage().greeting("Hello!").render()
        assert "Hello!" in out

    def test_render_contains_lines(self):
        out = MailMessage().line("Invoice ready").render()
        assert "Invoice ready" in out

    def test_render_contains_action_link(self):
        out = MailMessage().action("View", "https://x.com").render()
        assert "https://x.com" in out
        assert "View" in out

    def test_render_error_uses_red_color(self):
        normal = MailMessage().action("Click", "https://x.com").render()
        errored = MailMessage().error().action("Click", "https://x.com").render()
        assert "#e3342f" in errored
        assert "#3490dc" in normal

    def test_subject_inherited(self):
        m = MailMessage().subject("My Subject")
        assert m._subject_line == "My Subject"

    def test_to_inherited(self):
        m = MailMessage().to("a@b.com")
        assert m._to_addresses == ["a@b.com"]


# ===========================================================================
# _ArrayDriver
# ===========================================================================


class TestArrayDriver:
    def test_stores_mailable(self):
        driver = _ArrayDriver()
        m = Mailable().to("a@b.com").subject("Hi").html("<p>Hi</p>")
        driver.send(m, {})
        assert len(driver.sent()) == 1
        assert driver.sent()[0] is m

    def test_flush_clears_outbox(self):
        driver = _ArrayDriver()
        driver.send(Mailable(), {})
        driver.flush()
        assert driver.sent() == []

    def test_multiple_sends(self):
        driver = _ArrayDriver()
        driver.send(Mailable(), {})
        driver.send(Mailable(), {})
        assert len(driver.sent()) == 2


# ===========================================================================
# _LogDriver
# ===========================================================================


class TestLogDriver:
    def test_log_driver_calls_log(self):
        driver = _LogDriver()
        m = Mailable().to("a@b.com").subject("Greet").html("<p>Hi</p>")
        m._from_addr = "f@b.com"
        m._from_name = "Sender"

        with patch("hunt.mail.manager.Log") as mock_log:
            driver.send(m, {})
        mock_log.info.assert_called_once()
        logged = mock_log.info.call_args[0][0]
        assert "a@b.com" in logged or "Greet" in logged  # content present


# ===========================================================================
# _build_mime
# ===========================================================================


class TestBuildMime:
    def test_subject_and_to(self):
        m = Mailable().to("x@b.com").subject("Test")
        m._from_addr = "f@b.com"
        m._from_name = "F"
        msg = _build_mime(m)
        assert msg["Subject"] == "Test"
        assert "x@b.com" in msg["To"]

    def test_from_with_name(self):
        m = Mailable()
        m._from_addr = "f@b.com"
        m._from_name = "Sender"
        msg = _build_mime(m)
        assert "Sender" in msg["From"]
        assert "f@b.com" in msg["From"]

    def test_cc_header(self):
        m = Mailable().cc("cc@b.com")
        m._from_addr = "f@b.com"
        msg = _build_mime(m)
        assert "cc@b.com" in msg["Cc"]

    def test_reply_to_header(self):
        m = Mailable().reply_to("r@b.com")
        m._from_addr = "f@b.com"
        msg = _build_mime(m)
        assert "r@b.com" in msg["Reply-To"]


# ===========================================================================
# _MailManager
# ===========================================================================


class TestMailManager:
    def setup_method(self):
        self.mail = _MailManager()
        self.mail.configure(
            {
                "default": "log",
                "mailers": {"log": {"transport": "log"}},
                "from": {"address": "from@app.com", "name": "App"},
            }
        )

    def test_configure_sets_defaults(self):
        assert self.mail._default == "log"
        assert self.mail._from_address == "from@app.com"
        assert self.mail._from_name == "App"

    def test_to_returns_pending_mail(self):
        from hunt.mail.manager import _PendingMail

        pending = self.mail.to("x@b.com")
        assert isinstance(pending, _PendingMail)

    def test_send_calls_driver(self):
        m = Mailable().to("x@b.com").subject("Hi").html("<p>Hi</p>")
        with patch.object(self.mail, "_resolve_driver") as mock_drv:
            mock_drv.return_value = MagicMock()
            self.mail.send(m)
        mock_drv.return_value.send.assert_called_once()

    def test_send_sets_global_from(self):
        m = Mailable().to("x@b.com")
        with patch.object(self.mail, "_resolve_driver") as mock_drv:
            mock_drv.return_value = MagicMock()
            self.mail.send(m)
        assert m._from_addr == "from@app.com"

    def test_pending_mail_sets_to(self):
        captured = []
        with patch.object(self.mail, "_resolve_driver") as mock_drv:
            mock_drv.return_value._type = "mock"
            mock_drv.return_value.send = lambda m, c: captured.append(m)
            self.mail.to("x@b.com").send(Mailable().subject("Hi").html("<p></p>"))
        assert "x@b.com" in captured[0]._to_addresses

    def test_fake_intercepts_sends(self):
        fake = self.mail.fake()
        m = Mailable().to("x@b.com").subject("Hi").html("<p></p>")
        self.mail.send(m)
        assert fake.sent() == [m]

    def test_fake_assert_sent_passes(self):
        class Welcome(Mailable):
            pass

        fake = self.mail.fake()
        self.mail.send(Welcome().to("x@b.com").subject("Hi").html("<p></p>"))
        fake.assert_sent(Welcome)

    def test_fake_assert_not_sent_passes(self):
        class Welcome(Mailable):
            pass

        fake = self.mail.fake()
        fake.assert_not_sent(Welcome)

    def test_fake_assert_sent_with_count(self):
        class Invoice(Mailable):
            pass

        fake = self.mail.fake()
        self.mail.send(Invoice().to("a@b.com").html(""))
        self.mail.send(Invoice().to("b@b.com").html(""))
        fake.assert_sent(Invoice, count=2)

    def test_fake_assert_sent_with_callback(self):
        class Invoice(Mailable):
            def __init__(self, number):
                super().__init__()
                self.number = number

        fake = self.mail.fake()
        self.mail.send(Invoice(42).to("a@b.com").html(""))
        fake.assert_sent(Invoice, callback=lambda m: m.number == 42)

    def test_fake_assert_nothing_sent_passes(self):
        fake = self.mail.fake()
        fake.assert_nothing_sent()

    def test_stop_faking(self):
        fake = self.mail.fake()
        self.mail.stop_faking()
        # After stop_faking, the driver is used — patch it to avoid real sends
        with patch.object(self.mail, "_resolve_driver") as mock_drv:
            mock_drv.return_value = MagicMock()
            self.mail.send(Mailable().to("x@b.com").html(""))
        assert fake.sent() == []  # nothing captured by fake


# ===========================================================================
# Notification
# ===========================================================================


class TestNotification:
    def test_id_is_uuid_string(self):
        from hunt.notifications.notification import Notification

        n = Notification()
        assert len(n.id) == 36
        assert n.id.count("-") == 4

    def test_default_via_is_mail(self):
        from hunt.notifications.notification import Notification

        n = Notification()
        assert n.via(None) == ["mail"]

    def test_to_mail_raises_by_default(self):
        from hunt.notifications.notification import Notification

        n = Notification()
        with pytest.raises(NotImplementedError):
            n.to_mail(None)

    def test_to_database_raises_by_default(self):
        from hunt.notifications.notification import Notification

        n = Notification()
        with pytest.raises(NotImplementedError):
            n.to_database(None)

    def test_subclass_to_mail(self):
        from hunt.notifications.notification import Notification

        class MyNotif(Notification):
            def to_mail(self, notifiable):
                return MailMessage().subject("Hi")

        n = MyNotif()
        msg = n.to_mail(None)
        assert msg._subject_line == "Hi"


# ===========================================================================
# Notifiable mixin
# ===========================================================================


def _make_notifiable(email="user@example.com", id_=1):
    from hunt.notifications.notifiable import Notifiable

    class FakeModel(Notifiable):
        def __init__(self):
            self._attributes = {"id": id_, "email": email}

    return FakeModel()


class TestNotifiable:
    def test_route_notification_for_mail(self):
        user = _make_notifiable(email="u@b.com")
        assert user.route_notification_for_mail() == "u@b.com"

    def test_route_notification_for_database(self):
        user = _make_notifiable(id_=42)
        assert user.route_notification_for_database() == 42

    def test_notify_calls_channels(self):
        from hunt.notifications.notification import Notification

        class Poke(Notification):
            def via(self, notifiable):
                return ["mail"]

            def to_mail(self, notifiable):
                return MailMessage().subject("Poke").to(notifiable._attributes["email"])

        user = _make_notifiable()
        mail = _MailManager()
        mail.configure(
            {"default": "log", "mailers": {"log": {"transport": "log"}}, "from": {"address": "a@b.com", "name": "A"}}
        )
        fake = mail.fake()

        with patch("hunt.notifications.channels.mail.Mail", mail), patch("hunt.mail.manager.Mail", mail):
            user.notify(Poke())

        assert len(fake.sent()) == 1

    def test_notify_database_channel(self):
        from hunt.notifications.notification import Notification

        class DbNotif(Notification):
            def via(self, notifiable):
                return ["database"]

            def to_database(self, notifiable):
                return {"key": "value"}

        user = _make_notifiable()
        with patch("hunt.database.connection.connection") as mock_conn:
            mock_ctx = MagicMock()
            mock_conn.return_value.connect.return_value.__enter__.return_value = mock_ctx
            user.notify(DbNotif())
        mock_ctx.execute.assert_called_once()


# ===========================================================================
# NotificationFake
# ===========================================================================


class TestNotificationFake:
    def test_fake_intercepts_notify(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.notifications.notification import Notification

        class Ping(Notification):
            def via(self, n):
                return ["mail"]

            def to_mail(self, n):
                return MailMessage()

        user = _make_notifiable()

        with NotificationFake() as fake:
            user.notify(Ping())

        assert len(fake.sent_to(user, Ping)) == 1

    def test_assert_sent_to_passes(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.notifications.notification import Notification

        class Ping(Notification):
            def via(self, n):
                return ["mail"]

            def to_mail(self, n):
                return MailMessage()

        user = _make_notifiable()

        with NotificationFake() as fake:
            user.notify(Ping())
            fake.assert_sent_to(user, Ping)

    def test_assert_not_sent_to(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.notifications.notification import Notification

        class Ping(Notification):
            pass

        user = _make_notifiable()

        with NotificationFake() as fake:
            fake.assert_not_sent_to(user, Ping)

    def test_assert_nothing_sent(self):
        from hunt.notifications.fake import NotificationFake

        with NotificationFake() as fake:
            fake.assert_nothing_sent()

    def test_assert_count(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.notifications.notification import Notification

        class Ping(Notification):
            def via(self, n):
                return ["mail"]

            def to_mail(self, n):
                return MailMessage()

        user = _make_notifiable()

        with NotificationFake() as fake:
            user.notify(Ping())
            user.notify(Ping())
            fake.assert_count(Ping, 2)

    def test_assert_sent_to_with_callback(self):
        from hunt.notifications.fake import NotificationFake
        from hunt.notifications.notification import Notification

        class Tagged(Notification):
            def __init__(self, tag):
                super().__init__()
                self.tag = tag

            def via(self, n):
                return ["mail"]

            def to_mail(self, n):
                return MailMessage()

        user = _make_notifiable()

        with NotificationFake() as fake:
            user.notify(Tagged("promo"))
            fake.assert_sent_to(user, Tagged, callback=lambda n: n.tag == "promo")


# ===========================================================================
# MailChannel
# ===========================================================================


class TestMailChannel:
    def test_routes_to_notifiable_email(self):
        from hunt.notifications.channels.mail import MailChannel

        class Ping:
            def to_mail(self, notifiable):
                return MailMessage().subject("Hi")

        user = _make_notifiable(email="u@b.com")
        channel = MailChannel()

        with patch("hunt.notifications.channels.mail.Mail") as mock_mail:
            channel.send(user, Ping())

        mock_mail.send.assert_called_once()
        # The mailable passed to Mail.send should have the notifiable's email
        sent_mailable = mock_mail.send.call_args[0][0]
        assert "u@b.com" in sent_mailable._to_addresses

    def test_does_not_override_explicit_to(self):
        from hunt.notifications.channels.mail import MailChannel

        class Ping:
            def to_mail(self, notifiable):
                return MailMessage().subject("Hi").to("explicit@b.com")

        user = _make_notifiable(email="user@b.com")
        channel = MailChannel()

        with patch("hunt.notifications.channels.mail.Mail") as mock_mail:
            channel.send(user, Ping())

        sent = mock_mail.send.call_args[0][0]
        assert "explicit@b.com" in sent._to_addresses
        assert "user@b.com" not in sent._to_addresses  # explicit wins


# ===========================================================================
# make:mail / make:notification commands
# ===========================================================================


class TestMakeMailCommand:
    def test_make_mail_creates_file(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.make.mail import make_mail_command

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(make_mail_command, ["WelcomeEmail"])
        assert result.exit_code == 0
        out = tmp_path / "app" / "mail" / "welcome_email.py"
        assert out.exists()
        assert "WelcomeEmail" in out.read_text()

    def test_make_notification_creates_file(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.make.notification import make_notification_command

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(make_notification_command, ["InvoiceReady"])
        assert result.exit_code == 0
        out = tmp_path / "app" / "notifications" / "invoice_ready.py"
        assert out.exists()
        assert "InvoiceReady" in out.read_text()


# ===========================================================================
# _MailManager env-var fallback
# ===========================================================================


class TestMailManagerEnvConfig:
    """An unconfigured Mail manager builds its config from MAIL_* env vars."""

    def test_env_config_applied_on_send(self, monkeypatch):
        monkeypatch.setenv("MAIL_MAILER", "array")
        monkeypatch.setenv("MAIL_FROM_ADDRESS", "env@app.com")
        monkeypatch.setenv("MAIL_FROM_NAME", "EnvApp")
        mail = _MailManager()
        m = Mailable().to("x@b.com").subject("Hi").html("<p></p>")
        mail.send(m)
        assert mail._array_driver.sent() == [m]
        assert m._from_addr == "env@app.com"
        assert m._from_name == "EnvApp"

    def test_env_smtp_driver_selected(self, monkeypatch):
        from hunt.mail.manager import _SmtpDriver

        monkeypatch.setenv("MAIL_MAILER", "smtp")
        monkeypatch.setenv("MAIL_HOST", "smtp.example.com")
        monkeypatch.setenv("MAIL_PORT", "2525")
        monkeypatch.setenv("MAIL_USERNAME", "user")
        monkeypatch.setenv("MAIL_PASSWORD", "pass")
        monkeypatch.setenv("MAIL_ENCRYPTION", "ssl")
        mail = _MailManager()
        mail._ensure_configured()
        assert isinstance(mail._resolve_driver(), _SmtpDriver)
        cfg = mail._driver_config()
        assert cfg["host"] == "smtp.example.com"
        assert cfg["port"] == 2525
        assert cfg["username"] == "user"
        assert cfg["password"] == "pass"
        assert cfg["encryption"] == "ssl"

    def test_unconfigured_defaults_to_log_transport(self, monkeypatch):
        monkeypatch.delenv("MAIL_MAILER", raising=False)
        mail = _MailManager()
        mail._ensure_configured()
        assert isinstance(mail._resolve_driver(), _LogDriver)

    def test_explicit_configure_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("MAIL_MAILER", "smtp")
        mail = _MailManager()
        mail.configure({"default": "log", "mailers": {"log": {"transport": "log"}}})
        mail._ensure_configured()
        assert mail._default == "log"
        assert isinstance(mail._resolve_driver(), _LogDriver)


class TestApplicationMailWiring:
    """Application boot forwards config/mail.py to the Mail manager."""

    def test_config_mail_forwarded_to_manager(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "mail.py").write_text(
            'config = {"default": "array", "mailers": {"array": {"transport": "array"}}}\n'
        )
        from hunt.application import Application

        with patch("hunt.mail.manager.Mail") as mock_mail:
            Application(tmp_path)
        mock_mail.configure.assert_called_once_with({"default": "array", "mailers": {"array": {"transport": "array"}}})

    def test_missing_mail_config_leaves_manager_alone(self, tmp_path):
        (tmp_path / "config").mkdir()
        from hunt.application import Application

        with patch("hunt.mail.manager.Mail") as mock_mail:
            Application(tmp_path)
        mock_mail.configure.assert_not_called()


# ===========================================================================
# Queued mailable / notification jobs survive the queue round trip
# ===========================================================================


class _PickleableNotification:
    """Minimal notification stand-in; module-level so pickle can import it."""

    greeting = "hello"

    def via(self, notifiable):
        return ["mail"]


class TestQueuedJobRoundTrip:
    def test_mailable_job_survives_json_round_trip(self):
        import json as _json

        from hunt.console.commands.queue_work import _safe_import
        from hunt.mail.manager import Mail, _SendMailableJob
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job as _Job

        m = Mailable().to("x@b.com").subject("Hi").html("<p>Hi</p>")
        job = _SendMailableJob(m)

        # Same round trip a real backend performs: serialize → JSON → rebuild.
        body = _json.loads(_json.dumps(_serialize_job(job)))
        cls = _safe_import(body["class"], _Job)
        rebuilt = cls(**body["data"])

        fake = Mail.fake()
        try:
            rebuilt.handle()
        finally:
            Mail.stop_faking()
        assert len(fake.sent()) == 1
        assert "x@b.com" in fake.sent()[0]._to_addresses

    def test_notification_job_survives_json_round_trip(self):
        import json as _json

        from hunt.console.commands.queue_work import _safe_import
        from hunt.notifications.notifiable import _NotificationSender, _SendNotificationJob
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job as _Job

        notification = _PickleableNotification()
        notifiable = {"id": 7}
        job = _SendNotificationJob(notification, notifiable, "mail")

        body = _json.loads(_json.dumps(_serialize_job(job)))
        cls = _safe_import(body["class"], _Job)
        rebuilt = cls(**body["data"])

        delivered = []
        driver = MagicMock()
        driver.send.side_effect = lambda n, notif: delivered.append((n, notif))
        with patch.object(_NotificationSender, "_resolve", return_value=driver):
            rebuilt.handle()
        assert len(delivered) == 1
        assert delivered[0][0] == {"id": 7}
        assert delivered[0][1].greeting == "hello"

    def test_jobs_are_job_subclasses(self):
        from hunt.mail.manager import _SendMailableJob
        from hunt.notifications.notifiable import _SendNotificationJob
        from hunt.queue.job import Job as _Job

        assert issubclass(_SendMailableJob, _Job)
        assert issubclass(_SendNotificationJob, _Job)
