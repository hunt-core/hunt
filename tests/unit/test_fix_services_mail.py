"""Regression tests for MAIL-01..05 and the mail half of QUE-04."""

from __future__ import annotations

import datetime
import json
import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from hunt.database.model import Model
from hunt.mail.mailable import Mailable
from hunt.mail.manager import Mail, _build_mime, _MailManager, _normalise_encryption, _SendMailableJob, _SmtpDriver
from hunt.mail.message import MailMessage


def _mailable() -> Mailable:
    m = Mailable().to("to@example.com").subject("Hi").text("hello")
    m._from_addr = "from@example.com"
    m._from_name = "Sender"
    return m


def _send(config: dict, mailable: Mailable | None = None):
    """Run the SMTP driver against mocked smtplib; return (SMTP mock, SMTP_SSL mock)."""
    with (
        patch("hunt.mail.manager.smtplib.SMTP") as smtp,
        patch("hunt.mail.manager.smtplib.SMTP_SSL") as smtp_ssl,
    ):
        _SmtpDriver().send(mailable or _mailable(), config)
    return smtp, smtp_ssl


# ---------------------------------------------------------------------------
# MAIL-01: Bcc
# ---------------------------------------------------------------------------


class TestBcc:
    def test_bcc_is_in_envelope_but_not_in_headers(self):
        m = _mailable().cc("cc@example.com").bcc(["secret@example.com", "Boss <boss@example.com>"])
        smtp, _ = _send({"encryption": "none"}, m)
        server = smtp.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        kwargs = server.send_message.call_args[1]
        assert kwargs["to_addrs"] == ["to@example.com", "cc@example.com", "secret@example.com", "boss@example.com"]
        assert kwargs["from_addr"] == "from@example.com"
        assert msg["Bcc"] is None
        assert "secret@example.com" not in msg.as_string()
        assert "boss@example.com" not in msg.as_string()
        assert "cc@example.com" in msg["Cc"]

    def test_bcc_only_mail_is_sent(self):
        m = Mailable().bcc("only@example.com").subject("x").text("y")
        m._from_addr = "from@example.com"
        smtp, _ = _send({"encryption": "none"}, m)
        assert smtp.return_value.send_message.call_args[1]["to_addrs"] == ["only@example.com"]

    def test_no_recipients_raises(self):
        m = Mailable().subject("x").text("y")
        with pytest.raises(ValueError, match="recipient"):
            _send({"encryption": "none"}, m)


# ---------------------------------------------------------------------------
# MAIL-02: encryption, timeout, async
# ---------------------------------------------------------------------------


class TestEncryption:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("ssl", "ssl"),
            ("SSL", "ssl"),
            ("tls", "starttls"),
            ("TLS", "starttls"),
            ("starttls", "starttls"),
            (" StartTLS ", "starttls"),
            ("none", "none"),
            ("NONE", "none"),
        ],
    )
    def test_normalised(self, value, expected):
        assert _normalise_encryption(value) == expected

    @pytest.mark.parametrize("value", ["", "tsl", "plain", None, False, 1])
    def test_unknown_raises(self, value):
        with pytest.raises(ValueError, match="encryption"):
            _normalise_encryption(value)

    @pytest.mark.parametrize("value", ["", "tsl", None])
    def test_unknown_value_never_connects(self, value):
        with (
            patch("hunt.mail.manager.smtplib.SMTP") as smtp,
            patch("hunt.mail.manager.smtplib.SMTP_SSL") as smtp_ssl,
            pytest.raises(ValueError),
        ):
            _SmtpDriver().send(_mailable(), {"encryption": value, "username": "u", "password": "p"})
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

    @pytest.mark.parametrize("value", ["tls", "TLS", "starttls", "STARTTLS"])
    def test_starttls_happens_before_login(self, value):
        smtp, smtp_ssl = _send({"encryption": value, "username": "u", "password": "p"})
        smtp_ssl.assert_not_called()
        names = [c[0] for c in smtp.return_value.method_calls]
        assert names.index("starttls") < names.index("login") < names.index("send_message")

    def test_default_encryption_is_starttls(self):
        smtp, _ = _send({})
        smtp.return_value.starttls.assert_called_once()

    @pytest.mark.parametrize("value", ["ssl", "SSL"])
    def test_ssl_uses_smtp_ssl(self, value):
        smtp, smtp_ssl = _send({"encryption": value, "username": "u", "password": "p", "port": 465})
        smtp.assert_not_called()
        assert smtp_ssl.call_args[0] == ("127.0.0.1", 465)
        assert smtp_ssl.call_args[1]["context"] is not None
        smtp_ssl.return_value.starttls.assert_not_called()
        smtp_ssl.return_value.login.assert_called_once_with("u", "p")

    def test_failed_starttls_never_logs_in_or_sends(self):
        with patch("hunt.mail.manager.smtplib.SMTP") as smtp:
            smtp.return_value.starttls.side_effect = RuntimeError("STARTTLS not supported")
            with pytest.raises(RuntimeError):
                _SmtpDriver().send(_mailable(), {"encryption": "tls", "username": "u", "password": "p"})
        smtp.return_value.login.assert_not_called()
        smtp.return_value.send_message.assert_not_called()

    def test_explicit_none_allows_plaintext_login(self):
        smtp, _ = _send({"encryption": "none", "username": "u", "password": "p"})
        smtp.return_value.starttls.assert_not_called()
        smtp.return_value.login.assert_called_once_with("u", "p")

    def test_no_login_without_credentials(self):
        smtp, _ = _send({"encryption": "tls"})
        smtp.return_value.login.assert_not_called()

    def test_timeout_default_and_override(self):
        smtp, _ = _send({"encryption": "none"})
        assert smtp.call_args[1]["timeout"] == 30
        smtp, _ = _send({"encryption": "none", "timeout": 5})
        assert smtp.call_args[1]["timeout"] == 5
        _, smtp_ssl = _send({"encryption": "ssl"})
        assert smtp_ssl.call_args[1]["timeout"] == 30


class _ThreadRecordingDriver:
    def __init__(self):
        self.threads = []
        self.sent = []

    def send(self, mailable, config):
        self.threads.append(threading.current_thread())
        self.sent.append(mailable)


class TestSendAsync:
    async def test_send_async_runs_transport_off_the_event_loop_thread(self):
        mgr = _MailManager()
        mgr.configure({"default": "x", "mailers": {"x": {"transport": "x"}}, "from": {"address": "f@example.com"}})
        driver = _ThreadRecordingDriver()
        with patch.object(mgr, "_resolve_driver_for", return_value=driver):
            await mgr.to("a@example.com").send_async(Mailable().subject("s").text("t"))
        assert len(driver.sent) == 1
        assert driver.sent[0]._to_addresses == ["a@example.com"]
        assert driver.threads[0] is not threading.current_thread()

    async def test_send_async_propagates_errors(self):
        mgr = _MailManager()
        mgr.configure({"default": "x", "mailers": {"x": {"transport": "x"}}})
        driver = MagicMock()
        driver.send.side_effect = OSError("smtp down")
        with patch.object(mgr, "_resolve_driver_for", return_value=driver), pytest.raises(OSError):
            await mgr.send_async(Mailable().to("a@example.com"))


# ---------------------------------------------------------------------------
# MAIL-03: headers and recipients
# ---------------------------------------------------------------------------


class TestHeaderValidation:
    @pytest.mark.parametrize("bad", ["x@example.com\r\nBcc: evil@example.com", "x@example.com\n", "a\rb@example.com"])
    @pytest.mark.parametrize("setter", ["to", "cc", "bcc", "reply_to", "from_address"])
    def test_crlf_rejected_in_address_setters(self, setter, bad):
        with pytest.raises(ValueError):
            getattr(Mailable(), setter)(bad)

    def test_crlf_rejected_in_address_lists(self):
        with pytest.raises(ValueError):
            Mailable().to(["ok@example.com", "x@example.com\r\nX-Injected: 1"])

    @pytest.mark.parametrize("bad", ["Hello\r\nBcc: evil@example.com", "Hello\nWorld", "Hello\rWorld"])
    def test_crlf_rejected_in_subject(self, bad):
        with pytest.raises(ValueError):
            Mailable().subject(bad)

    def test_crlf_rejected_in_from_name_and_attachment_names(self, tmp_path):
        with pytest.raises(ValueError):
            Mailable().from_address("a@example.com", "Name\r\nBcc: x@example.com")
        with pytest.raises(ValueError):
            Mailable().attach_data(b"x", "a.txt\r\nX: 1")
        with pytest.raises(ValueError):
            Mailable().attach(str(tmp_path / "f.txt"), {"as": "a\nb.txt"})

    @pytest.mark.parametrize("bad", ["a@example.com, b@example.com", "a@example.com,b@example.com", "", "   "])
    def test_one_address_per_value(self, bad):
        with pytest.raises(ValueError):
            Mailable().to(bad)

    def test_display_name_with_comma_is_one_address(self):
        m = Mailable().to('"Doe, Jane" <jane@example.com>')
        m._from_addr = "f@example.com"
        msg = _build_mime(m)
        assert "jane@example.com" in msg["To"]
        assert len(msg["To"].addresses) == 1

    def test_pending_mail_validates_recipients(self):
        mgr = _MailManager()
        mgr.configure({"default": "array", "mailers": {"array": {"transport": "array"}}})
        with pytest.raises(ValueError):
            mgr.to("a@example.com\r\nBcc: evil@example.com").send(Mailable())
        with pytest.raises(ValueError):
            mgr.to("a@example.com").bcc("x@example.com, y@example.com").send(Mailable())

    def test_values_assigned_directly_are_still_validated_at_build(self):
        m = _mailable()
        m._subject_line = "Hi\r\nBcc: evil@example.com"
        with pytest.raises(ValueError):
            _build_mime(m)
        m = _mailable()
        m._to_addresses.append("a@example.com\nX-Injected: 1")
        with pytest.raises(ValueError):
            _build_mime(m)
        m = _mailable()
        m._bcc_addresses.append("a@example.com, b@example.com")
        with pytest.raises(ValueError):
            _build_mime(m)

    def test_from_header_uses_display_name(self):
        msg = _build_mime(_mailable())
        assert msg["From"].addresses[0].display_name == "Sender"
        assert msg["From"].addresses[0].addr_spec == "from@example.com"

    def test_attachment_filename_is_quoted_not_interpolated(self):
        name = 'report "final"; name=evil.txt'
        m = _mailable().attach_data(b"data", name, "text/plain")
        msg = _build_mime(m)
        attachments = list(msg.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_filename() == name
        assert attachments[0].get_content_disposition() == "attachment"
        assert attachments[0].get_content_type() == "text/plain"

    def test_non_ascii_attachment_filename_round_trips(self):
        m = _mailable().attach_data(b"data", "résumé.pdf", "application/pdf")
        part = next(iter(_build_mime(m).iter_attachments()))
        assert part.get_filename() == "résumé.pdf"
        assert part.get_content().startswith(b"data")


# ---------------------------------------------------------------------------
# MAIL-04: render errors and attachments
# ---------------------------------------------------------------------------


class TestRenderAndAttachments:
    def test_render_error_propagates(self):
        m = Mailable().view("emails.broken", {})
        with patch("hunt.support.helpers.view", side_effect=RuntimeError("template exploded")):
            with pytest.raises(RuntimeError, match="template exploded"):
                m.render()

    def test_broken_template_is_not_sent(self):
        mgr = _MailManager()
        mgr.configure({"default": "smtp", "mailers": {"smtp": {"transport": "smtp", "encryption": "none"}}})
        m = Mailable().to("a@example.com").subject("s").view("emails.broken", {})
        m._from_addr = "f@example.com"
        with (
            patch("hunt.support.helpers.view", side_effect=RuntimeError("template exploded")),
            patch("hunt.mail.manager.smtplib.SMTP") as smtp,
            pytest.raises(RuntimeError, match="template exploded"),
        ):
            mgr.send(m)
        smtp.return_value.send_message.assert_not_called()

    def test_missing_attachment_raises(self, tmp_path):
        m = _mailable().attach(str(tmp_path / "nope.pdf"))
        with pytest.raises(FileNotFoundError, match=r"nope\.pdf"):
            _build_mime(m)

    def test_missing_attachment_is_not_sent(self, tmp_path):
        m = _mailable().attach(str(tmp_path / "nope.pdf"))
        with patch("hunt.mail.manager.smtplib.SMTP") as smtp, pytest.raises(FileNotFoundError):
            _SmtpDriver().send(m, {"encryption": "none"})
        smtp.assert_not_called()

    def test_attach_guesses_type_and_keeps_name(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4 data")
        part = next(iter(_build_mime(_mailable().attach(str(f))).iter_attachments()))
        assert part.get_filename() == "report.pdf"
        assert part.get_content_type() == "application/pdf"
        assert part.get_content() == b"%PDF-1.4 data"

    def test_attach_options_are_honoured(self, tmp_path):
        f = tmp_path / "tmp123.bin"
        f.write_bytes(b"a,b\n1,2\n")
        m = _mailable().attach(str(f), {"as": "export.csv", "mime": "text/csv"})
        part = next(iter(_build_mime(m).iter_attachments()))
        assert part.get_filename() == "export.csv"
        assert part.get_content_type() == "text/csv"


# ---------------------------------------------------------------------------
# MAIL-05: no HTML part for text-only mail
# ---------------------------------------------------------------------------


class TestBodyParts:
    def test_text_only_mail_has_no_html_part(self):
        m = _mailable()
        m.text("line one\nline <b>two</b>")
        msg = _build_mime(m)
        assert msg.get_content_type() == "text/plain"
        assert [p.get_content_type() for p in msg.walk()] == ["text/plain"]
        assert "line <b>two</b>" in msg.get_content()

    def test_text_and_html_are_alternatives(self):
        m = _mailable().html("<p>hello</p>")
        msg = _build_mime(m)
        assert msg.get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]

    def test_html_only(self):
        m = Mailable().to("a@example.com").html("<p>x</p>")
        assert _build_mime(m).get_content_type() == "text/html"

    def test_view_mail_has_html_part(self):
        m = Mailable().to("a@example.com").view("emails.x", {})
        with patch("hunt.support.helpers.view", return_value="<p>view</p>"):
            msg = _build_mime(m)
        assert msg.get_content_type() == "text/html"
        assert "<p>view</p>" in msg.get_content()

    def test_mail_message_renders_html(self):
        m = MailMessage().to("a@example.com").line("Hello <world>")
        msg = _build_mime(m)
        assert msg.get_content_type() == "text/html"
        assert "&lt;world&gt;" in msg.get_content()

    def test_text_with_attachment_stays_plain(self):
        msg = _build_mime(_mailable().attach_data(b"x", "a.bin"))
        types = [p.get_content_type() for p in msg.walk()]
        assert "text/html" not in types
        assert "text/plain" in types


# ---------------------------------------------------------------------------
# QUE-04 (mail half): JSON payloads, no pickle
# ---------------------------------------------------------------------------


class _Account(Model):
    table = "accounts"


class _InvoiceMail(Mailable):
    def __init__(self, account=None, due=None, amount=None):
        super().__init__()
        self.account = account
        self.due = due
        self.amount = amount

    def build(self):
        return self.subject(f"Invoice {self.amount}").text("see attached")


class _NotAMailable:
    pass


def _round_trip(job: _SendMailableJob) -> _SendMailableJob:
    from hunt.queue.drivers.database import _serialize_job

    body = json.loads(json.dumps(_serialize_job(job)))
    return _SendMailableJob(**body["data"])


class TestQueuedMailPayload:
    def test_modules_do_not_use_pickle(self):
        import hunt.mail._queue_payload as payload_mod
        import hunt.mail.manager as manager_mod
        import hunt.notifications.notifiable as notifiable_mod

        for mod in (payload_mod, manager_mod, notifiable_mod):
            assert not hasattr(mod, "pickle")
            with open(mod.__file__, encoding="utf-8") as fh:
                assert "import pickle" not in fh.read()

    def test_payload_is_json_and_rebuilds_the_mailable(self):
        due = datetime.datetime(2026, 5, 1, 9, 30, tzinfo=datetime.UTC)
        m = _InvoiceMail(due=due, amount=Decimal("19.90")).to("a@example.com").bcc("b@example.com")
        m.attach_data(b"\x00\x01binary", "blob.bin")
        job = _round_trip(_SendMailableJob(m))

        assert json.loads(job.payload)["class"].endswith("._InvoiceMail")
        with Mail.fake() as fake:
            job.handle()
        sent = fake.sent(_InvoiceMail)
        assert len(sent) == 1
        assert sent[0] is not m
        assert sent[0].due == due
        assert sent[0].amount == Decimal("19.90")
        assert sent[0]._to_addresses == ["a@example.com"]
        assert sent[0]._bcc_addresses == ["b@example.com"]
        assert sent[0]._attachments[0]["_raw_data"] == b"\x00\x01binary"
        assert sent[0]._subject_line == "Invoice 19.90"

    def test_model_is_stored_as_class_and_key_and_refetched(self):
        account = _Account().force_fill({"id": 12, "password": "s3cret-hash"})
        job = _SendMailableJob(_InvoiceMail(account=account).to("a@example.com"))
        assert isinstance(job.payload, str)
        assert "s3cret-hash" not in job.payload
        state = json.loads(job.payload)["state"]
        assert state["account"]["v"] == {"class": f"{__name__}._Account", "key": 12}

        fresh = _Account().force_fill({"id": 12})
        with patch.object(_Account, "find", return_value=fresh) as find, Mail.fake() as fake:
            _round_trip(job).handle()
        find.assert_called_once_with(12)
        assert fake.sent()[0].account is fresh

    def test_deleted_model_raises_in_worker(self):
        account = _Account().force_fill({"id": 12})
        job = _SendMailableJob(_InvoiceMail(account=account).to("a@example.com"))
        with patch.object(_Account, "find", return_value=None), Mail.fake() as fake:
            with pytest.raises(LookupError, match="no longer exists"):
                job.handle()
            fake.assert_nothing_sent()

    def test_unsaved_model_raises_at_dispatch(self):
        with pytest.raises(TypeError, match="account"):
            _SendMailableJob(_InvoiceMail(account=_Account()))

    def test_non_serializable_attribute_names_the_attribute(self):
        with pytest.raises(TypeError, match="'due'"):
            _SendMailableJob(_InvoiceMail(due=object()))

    def test_local_class_is_rejected_at_dispatch(self):
        class Local(Mailable):
            pass

        with pytest.raises(TypeError, match="importable"):
            _SendMailableJob(Local())

    @pytest.mark.parametrize(
        "path",
        ["subprocess.Popen", "os.system", f"{__name__}._NotAMailable", "builtins.eval", "nonexistent.module.Thing"],
    )
    def test_worker_only_loads_mailable_subclasses(self, path):
        job = _SendMailableJob(payload=json.dumps({"class": path, "state": {}}))
        with Mail.fake() as fake, pytest.raises((TypeError, ImportError)):
            job.handle()
        assert fake.sent() == []

    def test_legacy_pickle_payload_is_refused(self):
        job = _SendMailableJob(payload="gASVCAAAAAAAAACMBGV2aWyULg==")
        with pytest.raises(TypeError, match="pickle"):
            job.handle()

    def test_custom_payload_hooks_are_used(self):
        job = _round_trip(_SendMailableJob(_HookedMail("tok-1").to("a@example.com")))
        assert json.loads(job.payload)["state"] == {"token": "tok-1", "to": ["a@example.com"]}
        with Mail.fake() as fake:
            job.handle()
        assert fake.sent()[0].token == "tok-1"
        assert fake.sent()[0]._to_addresses == ["a@example.com"]


class _HookedMail(Mailable):
    def __init__(self, token):
        super().__init__()
        self.token = token

    def to_queue_payload(self):
        return {"token": self.token, "to": list(self._to_addresses)}

    @classmethod
    def from_queue_payload(cls, state):
        return cls(state["token"]).to(state["to"])


class _BrokenBackend:
    """A real (non-inline) queue backend that is down."""

    def push(self, job):
        raise ConnectionError("queue down")

    def later(self, delay, job):
        raise ConnectionError("queue down")


class TestQueueDispatch:
    def _manager(self, driver):
        mgr = _MailManager()
        mgr.configure({"default": "x", "mailers": {"x": {"transport": "x"}}, "from": {"address": "f@example.com"}})
        return mgr, patch.object(mgr, "_resolve_driver_for", return_value=driver)

    def test_sync_queue_failure_propagates_without_second_attempt(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.manager import Queue

        driver = MagicMock()
        driver.send.side_effect = OSError("smtp down")
        mgr, resolve = self._manager(driver)
        with (
            resolve,
            patch("hunt.mail.manager.Mail", mgr),
            patch.object(Queue, "_get_driver", return_value=SyncDriver()),
            pytest.raises(OSError, match="smtp down"),
        ):
            mgr.queue(_InvoiceMail(amount=1).to("a@example.com"))
        assert driver.send.call_count == 1

    def test_broken_queue_backend_falls_back_to_sync_send(self):
        from hunt.queue.manager import Queue

        driver = MagicMock()
        mgr, resolve = self._manager(driver)
        with resolve, patch.object(Queue, "_get_driver", return_value=_BrokenBackend()):
            mgr.queue(_InvoiceMail(amount=1).to("a@example.com"))
            mgr.later(60, _InvoiceMail(amount=2).to("b@example.com"))
        assert driver.send.call_count == 2

    def test_serialization_error_is_not_swallowed_by_the_fallback(self):
        driver = MagicMock()
        mgr, resolve = self._manager(driver)
        with resolve, pytest.raises(TypeError):
            mgr.queue(_InvoiceMail(due=object()).to("a@example.com"))
        driver.send.assert_not_called()
