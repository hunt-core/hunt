"""Behavioural tests for generated applications (GEN-01, 03, 04, 05, 06, 08, 09, 13, 14).

Each test scaffolds an application into a temp directory with ``hunt new`` and
drives it through its real HTTP kernel. The app runs in a child Python process
so its ``app``/``bootstrap``/``routes`` packages and the framework's global
state (Auth model, facade application, admin registry) never leak into the
rest of the test suite. Nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hunt.console.commands.new import new_command

# ---------------------------------------------------------------------------
# Child-process harness
# ---------------------------------------------------------------------------

_HARNESS = r'''
import asyncio, hashlib, hmac, json, os, re, sys, time, types
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")

from hunt.database.schema.migration import Migrator
from bootstrap.app import kernel
from hunt.http.request import Request

Migrator(Path.cwd() / "database" / "migrations").run()

R = {}


class Client:
    """Minimal cookie-aware client that calls the application's HTTP kernel directly."""

    def __init__(self, ip="10.0.0.1"):
        self.cookies = {}
        self.ip = ip

    async def request(self, method, path, data=None, json_body=None, headers=None, raw=None):
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        body = b""
        if raw is not None:
            body = raw
        elif json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["content-type"] = "application/json"
            hdrs.setdefault("accept", "application/json")
        elif data is not None:
            body = urlencode(data).encode()
            hdrs["content-type"] = "application/x-www-form-urlencoded"
        if self.cookies:
            hdrs["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        path_only, _, query = path.partition("?")
        scope = {
            "type": "http",
            "method": method,
            "path": path_only,
            "query_string": query.encode(),
            "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": (self.ip, 1234),
        }
        resp = await kernel._handle(Request(scope, body))
        for cookie in getattr(resp, "_cookie_headers", []):
            name, _, rest = cookie.partition("=")
            self.cookies[name.strip()] = rest.split(";", 1)[0]
        resp.text = resp._body.decode("utf-8", "replace")
        resp.location = resp.get_header("Location")
        return resp

    async def get(self, path, **kw):
        return await self.request("GET", path, **kw)

    async def csrf(self, path="/login"):
        page = await self.get(path)
        match = re.search(r'name="_token" value="([^"]+)"', page.text)
        return match.group(1) if match else ""

    async def post(self, path, data=None, token_from="/login", **kw):
        if data is not None and "_token" not in data and token_from:
            data = {**data, "_token": await self.csrf(token_from)}
        return await self.request("POST", path, data=data, **kw)

    async def login(self, email, password="password1"):
        return await self.post("/login", {"email": email, "password": password})


def make_user(name, admin=False):
    from app.models.user import User
    from hunt.auth.manager import hash_password

    user = User.create({"name": name, "email": f"{name}@example.com", "password": hash_password("password1")})
    if admin:
        user.is_admin = True
        user.save()
    return user


def freeze_throttle_clock(moment):
    """Pin the generated limiter's clock so a run can never straddle two windows."""
    import app.middleware.throttle as throttle

    throttle.time = types.SimpleNamespace(time=lambda: float(moment))


def finish():
    print("RESULT:" + json.dumps(R))
'''


def _child_env() -> dict[str, str]:
    """Environment for the child: drop settings that would override the scaffolded .env."""
    prefixes = ("REDIS_", "CACHE_", "DB_", "DATABASE_", "SESSION_", "QUEUE_", "MAIL_", "APP_", "THROTTLE_", "SECURE_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _drive(app: Path, driver: str, *args: str, extra_env: dict[str, str] | None = None) -> dict:
    script = app.parent / f"driver_{abs(hash(driver))}.py"
    script.write_text(_HARNESS + "\n" + driver + "\nfinish()\n", encoding="utf-8")
    env = _child_env()
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=app,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"driver failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, proc.stdout[-2000:]
    return json.loads(lines[-1][len("RESULT:") :])


@contextlib.contextmanager
def _cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _scaffold(base: Path, starter: str | None = None) -> Path:
    args = ["myapp"] + ([f"--starter={starter}"] if starter else [])
    with _cwd(base):
        result = CliRunner().invoke(new_command, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return base / "myapp"


def _invoke_in(app: Path, command, args: list[str]):
    with _cwd(app):
        result = CliRunner().invoke(command, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# GEN-01: API starter
# ---------------------------------------------------------------------------

_API_DRIVER = r"""
async def main():
    from app.models.user import User

    make_user("a"); make_user("b"); make_user("root", admin=True)
    anon = Client()
    routes = [("GET", "/api/v1/users"), ("POST", "/api/v1/users"), ("GET", "/api/v1/users/1"),
              ("PUT", "/api/v1/users/1"), ("DELETE", "/api/v1/users/1")]
    R["anonymous"] = [
        (await anon.request(m, p, json_body={} if m in ("POST", "PUT") else None, headers={"Accept": "application/json"})).status
        for m, p in routes
    ]
    R["invalid_token"] = (await anon.request("GET", "/api/v1/users/1", headers={"Authorization": "Bearer nope"})).status

    async def token(email, ip):
        resp = await Client(ip=ip).request("POST", "/api/v1/auth/login", json_body={"email": email, "password": "password1"})
        R.setdefault("login_status", []).append(resp.status)
        return json.loads(resp.text)["token"]

    user_token = await token("a@example.com", "10.2.0.1")
    admin_token = await token("root@example.com", "10.2.0.2")
    auth = lambda t: {"Authorization": "Bearer " + t, "Accept": "application/json"}

    from app.models.personal_access_token import PersonalAccessToken
    stored = [t._attributes["token"] for t in PersonalAccessToken.all().get()]
    R["token_stored_hashed"] = user_token not in stored and hashlib.sha256(user_token.encode()).hexdigest() in stored

    own = await anon.request("GET", "/api/v1/users/1", headers=auth(user_token))
    R["own_show"] = own.status
    R["sets_cookie_with_auth"] = any("_auth" in c for c in own._cookie_headers)
    R["foreign"] = [
        (await anon.request("GET", "/api/v1/users/2", headers=auth(user_token))).status,
        (await anon.request("PUT", "/api/v1/users/2", json_body={"email": "x@example.com"}, headers=auth(user_token))).status,
        (await anon.request("DELETE", "/api/v1/users/2", headers=auth(user_token))).status,
    ]
    R["foreign_untouched"] = User.find(2)._attributes["email"]
    R["index_as_user"] = (await anon.request("GET", "/api/v1/users", headers=auth(user_token))).status
    escalate = await anon.request("PUT", "/api/v1/users/1", json_body={"name": "A2", "is_admin": True}, headers=auth(user_token))
    R["own_update"] = escalate.status
    R["own_is_admin_after"] = bool(User.find(1)._attributes.get("is_admin"))

    R["admin"] = [
        (await anon.request("GET", "/api/v1/users", headers=auth(admin_token))).status,
        (await anon.request("POST", "/api/v1/users", json_body={"name": "N", "email": "n@example.com", "password": "password1"}, headers=auth(admin_token))).status,
        (await anon.request("GET", "/api/v1/users/2", headers=auth(admin_token))).status,
        (await anon.request("PUT", "/api/v1/users/2", json_body={"name": "B2"}, headers=auth(admin_token))).status,
        (await anon.request("DELETE", "/api/v1/users/2", headers=auth(admin_token))).status,
    ]
    R["logout"] = (await anon.request("POST", "/api/v1/auth/logout", headers=auth(user_token))).status
    R["after_logout"] = (await anon.request("GET", "/api/v1/users/1", headers=auth(user_token))).status

    freeze_throttle_clock(1_900_000_000)
    brute = Client(ip="10.2.0.9")
    R["login_throttle"] = [
        (await brute.request("POST", "/api/v1/auth/login", json_body={"email": "A@example.com", "password": "bad"})).status
        for _ in range(6)
    ]
    # Session-cookie requests to other paths still need a CSRF token.
    R["csrf_outside_api"] = (await anon.request("POST", "/logout", data={})).status

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def api_result(tmp_path_factory):
    app = _scaffold(tmp_path_factory.mktemp("api"), "api")
    return _drive(app, _API_DRIVER)


class TestApiStarter:
    def test_every_user_route_requires_a_token(self, api_result):
        assert api_result["anonymous"] == [401, 401, 401, 401, 401]
        assert api_result["invalid_token"] == 401

    def test_login_issues_a_hashed_token(self, api_result):
        assert api_result["login_status"] == [201, 201]
        assert api_result["token_stored_hashed"] is True

    def test_valid_token_reaches_own_record_without_a_session(self, api_result):
        assert api_result["own_show"] == 200
        assert api_result["sets_cookie_with_auth"] is False

    def test_non_admin_token_cannot_touch_another_user(self, api_result):
        assert api_result["foreign"] == [403, 403, 403]
        assert api_result["foreign_untouched"] == "b@example.com"
        assert api_result["index_as_user"] == 403

    def test_update_cannot_escalate_privileges(self, api_result):
        assert api_result["own_update"] == 200
        assert api_result["own_is_admin_after"] is False

    def test_admin_token_can_use_all_five_routes(self, api_result):
        assert api_result["admin"] == [200, 201, 200, 200, 204]

    def test_logout_revokes_the_token(self, api_result):
        assert api_result["logout"] == 204
        assert api_result["after_logout"] == 401

    def test_token_login_is_throttled(self, api_result):
        assert api_result["login_throttle"] == [401, 401, 401, 401, 401, 429]

    def test_csrf_still_protects_non_api_routes(self, api_result):
        assert api_result["csrf_outside_api"] == 419


# ---------------------------------------------------------------------------
# GEN-03: blog starter
# ---------------------------------------------------------------------------

_BLOG_DRIVER = r"""
async def main():
    from app.models.post import Post

    make_user("a"); make_user("b"); make_user("root", admin=True)
    anon = Client(ip="10.3.0.1")
    token = await anon.csrf("/login")
    R["anon_writes"] = [
        [(r := await anon.request("POST", path, data={"title": "x", "body": "y", "_token": token})).status, r.location]
        for path in ("/posts", "/posts/1/update", "/posts/1/delete")
    ]
    R["anon_forms"] = [(await anon.get(path)).location for path in ("/posts/create", "/posts/1/edit")]

    a = Client(ip="10.3.0.2"); await a.login("a@example.com")
    b = Client(ip="10.3.0.3"); await b.login("b@example.com")
    root = Client(ip="10.3.0.4"); await root.login("root@example.com")

    R["create_form"] = (await a.get("/posts/create")).status
    R["store_draft"] = (await a.post("/posts", {"title": "Hello World", "body": "draft"}, token_from="/posts/create")).status
    R["store_duplicate_title"] = (
        await a.post("/posts", {"title": "Hello World", "body": "second", "published": "1"}, token_from="/posts/create")
    ).status
    posts = Post.all().get()
    R["slugs"] = [p._attributes["slug"] for p in posts]
    R["authors"] = [p._attributes["author_id"] for p in posts]

    R["draft_as_guest"] = (await anon.get("/posts/1")).status
    R["draft_as_other_user"] = (await b.get("/posts/1")).status
    R["draft_as_owner"] = (await a.get("/posts/1")).status
    R["draft_as_admin"] = (await root.get("/posts/1")).status
    R["published_as_guest"] = (await anon.get("/posts/2")).status
    R["missing"] = (await anon.get("/posts/999")).status
    R["non_numeric"] = (await anon.get("/posts/abc")).status

    R["other_user"] = [
        (await b.get("/posts/2/edit")).status,
        (await b.post("/posts/2/update", {"title": "pwn", "body": "x"}, token_from="/posts")).status,
        (await b.post("/posts/2/delete", {}, token_from="/posts")).status,
    ]
    R["title_after_attack"] = Post.find(2)._attributes["title"]
    R["owner_update"] = (
        await a.post("/posts/2/update", {"title": "Hello World", "body": "changed", "published": "1"}, token_from="/posts/2/edit")
    ).status
    R["body_after_update"] = Post.find(2)._attributes["body"]
    R["admin_delete"] = (await root.post("/posts/2/delete", {}, token_from="/posts")).status
    R["deleted"] = Post.find(2) is None

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def blog_result(tmp_path_factory):
    app = _scaffold(tmp_path_factory.mktemp("blog"), "blog")
    return _drive(app, _BLOG_DRIVER)


class TestBlogStarter:
    def test_anonymous_writes_redirect_to_login(self, blog_result):
        assert blog_result["anon_writes"] == [[302, "/login"], [302, "/login"], [302, "/login"]]
        assert blog_result["anon_forms"] == ["/login", "/login"]

    def test_author_is_recorded_and_duplicate_titles_get_distinct_slugs(self, blog_result):
        assert blog_result["create_form"] == 200
        assert blog_result["store_draft"] == 302
        assert blog_result["store_duplicate_title"] == 302
        assert blog_result["slugs"] == ["hello-world", "hello-world-2"]
        assert blog_result["authors"] == [1, 1]

    def test_drafts_are_hidden_from_everyone_but_owner_and_admin(self, blog_result):
        assert blog_result["draft_as_guest"] == 404
        assert blog_result["draft_as_other_user"] == 404
        assert blog_result["draft_as_owner"] == 200
        assert blog_result["draft_as_admin"] == 200
        assert blog_result["published_as_guest"] == 200

    def test_missing_and_malformed_ids_are_404(self, blog_result):
        assert blog_result["missing"] == 404
        assert blog_result["non_numeric"] == 404

    def test_only_owner_or_admin_can_change_a_post(self, blog_result):
        assert blog_result["other_user"] == [403, 403, 403]
        assert blog_result["title_after_attack"] == "Hello World"
        assert blog_result["owner_update"] == 302
        assert blog_result["body_after_update"] == "changed"
        assert blog_result["admin_delete"] == 302
        assert blog_result["deleted"] is True


# ---------------------------------------------------------------------------
# GEN-04: SaaS starter
# ---------------------------------------------------------------------------

_SAAS_DRIVER = r"""
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"


async def main():
    from app.models.team import Team

    make_user("a"); make_user("b")
    a = Client(ip="10.4.0.1"); await a.login("a@example.com")
    b = Client(ip="10.4.0.2"); await b.login("b@example.com")

    R["store"] = (await a.post("/teams", {"name": "Acme", "plan": "enterprise"}, token_from="/teams/create")).status
    R["stored_plan"] = Team.find(1)._attributes["plan"]
    R["stored_owner"] = Team.find(1)._attributes["owner_id"]
    R["duplicate_name"] = (await a.post("/teams", {"name": "Acme"}, token_from="/teams/create")).status
    R["slugs"] = [t._attributes["slug"] for t in Team.all().get()]

    R["owner_billing"] = (await a.get("/teams/1/billing")).status
    R["foreign_billing_get"] = (await b.get("/teams/1/billing")).status
    R["foreign_billing_post"] = (await b.post("/teams/1/billing/plan", {"plan": "enterprise"}, token_from="/teams")).status
    R["owner_paid_plan"] = (await a.post("/teams/1/billing/plan", {"plan": "pro"}, token_from="/teams/1/billing")).status
    R["plan_after"] = Team.find(1)._attributes["plan"]
    R["missing_team"] = (await a.get("/teams/999/billing")).status

    hook = Client(ip="10.4.0.9")
    body = b'{"type": "ping"}'
    R["webhook_without_stripe"] = (
        await hook.request("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": "x"})
    ).status

    # A stand-in for the stripe package that verifies an HMAC over the raw body.
    stripe = types.ModuleType("stripe")

    class Webhook:
        @staticmethod
        def construct_event(payload, signature, secret):
            if not isinstance(payload, (bytes, bytearray)):
                raise TypeError("payload must be the raw request bytes")
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError("bad signature")
            return json.loads(payload)

    stripe.Webhook = Webhook
    sys.modules["stripe"] = stripe
    good = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    R["webhook_signed"] = (
        await hook.request("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": good})
    ).status
    R["webhook_bad_signature"] = (
        await hook.request("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": "bad"})
    ).status
    R["csrf_elsewhere"] = (await hook.request("POST", "/teams", data={"name": "x"})).status

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def saas_result(tmp_path_factory):
    app = _scaffold(tmp_path_factory.mktemp("saas"), "saas")
    return _drive(app, _SAAS_DRIVER)


class TestSaasStarter:
    def test_new_team_is_always_free(self, saas_result):
        assert saas_result["store"] == 302
        assert saas_result["stored_plan"] == "free"
        assert saas_result["stored_owner"] == 1

    def test_duplicate_team_names_do_not_collide(self, saas_result):
        assert saas_result["duplicate_name"] == 302
        assert saas_result["slugs"] == ["acme", "acme-2"]

    def test_billing_is_limited_to_team_owners(self, saas_result):
        assert saas_result["owner_billing"] == 200
        assert saas_result["foreign_billing_get"] == 403
        assert saas_result["foreign_billing_post"] == 403
        assert saas_result["missing_team"] == 404

    def test_paid_plan_is_refused_without_a_payment_provider(self, saas_result):
        assert saas_result["owner_paid_plan"] == 402
        assert saas_result["plan_after"] == "free"

    def test_webhook(self, saas_result):
        assert saas_result["webhook_without_stripe"] == 500
        assert saas_result["webhook_signed"] == 200
        assert saas_result["webhook_bad_signature"] == 400
        # Only the webhook is exempt from CSRF.
        assert saas_result["csrf_elsewhere"] == 419


# ---------------------------------------------------------------------------
# Base skeleton: GEN-06, GEN-08, GEN-09, GEN-L3, GEN-L4
# ---------------------------------------------------------------------------

_BASE_DRIVER = r"""
async def main():
    from app.models.user import User
    from hunt.mail.manager import Mail

    freeze_throttle_clock(1_900_000_000)
    home = await Client().get("/")
    R["home"] = home.status
    R["csp"] = home.get_header("Content-Security-Policy")
    R["frame_options"] = home.get_header("X-Frame-Options")

    visitor = Client(ip="10.5.0.1")
    registered = await visitor.post(
        "/register",
        {"name": "A", "email": "a@example.com", "password": "password1", "password_confirmation": "password1"},
        token_from="/register",
    )
    R["register"] = [registered.status, registered.location]

    attacker = Client(ip="10.5.0.2")
    R["login_attempts"] = [(await attacker.post("/login", {"email": "A@Example.com", "password": "wrong"})).status for _ in range(6)]
    # Another address is not locked out by the attacker.
    other = await Client(ip="10.5.0.3").login("a@example.com")
    R["login_from_other_address"] = [other.status, other.location]

    spammer = Client(ip="10.5.0.4")
    statuses = []
    for i in range(6):
        spammer.cookies.clear()
        resp = await spammer.post(
            "/register",
            {"name": "S", "email": f"s{i}@example.com", "password": "password1", "password_confirmation": "password1"},
            token_from="/register",
        )
        statuses.append(resp.status)
    R["register_attempts"] = statuses

    resetter = Client(ip="10.5.0.5")
    Mail.fake()
    forgot = [(await resetter.post("/forgot-password", {"email": "a@example.com"}, token_from="/forgot-password")).status for _ in range(6)]
    R["forgot_attempts"] = forgot
    sent = Mail.sent()
    R["reset_mails"] = len(sent)
    R["reset_mail_has_link"] = bool(sent) and "/reset-password/" in sent[0].render()
    if sent:
        token = re.search(r"/reset-password/([0-9A-Za-z_%-]+)", sent[0].render()).group(1)
        fields = {"email": "a@example.com", "token": token, "password": "new-password1", "password_confirmation": "new-password1"}
        page = f"/reset-password/{token}?email=a@example.com"
        done = await Client(ip="10.5.0.6").post("/reset-password", fields, token_from=page)
        R["reset"] = [done.status, done.location]
        again = await Client(ip="10.5.0.7").post("/reset-password", fields, token_from=page)
        R["reset_reuse"] = again.status
        relogin = await Client(ip="10.5.0.8").login("a@example.com", "new-password1")
        R["login_with_new_password"] = [relogin.status, relogin.location]

asyncio.run(main())
"""

_SHARED_THROTTLE_DRIVER = r"""
async def main():
    make_user("a") if sys.argv[1] == "first" else None
    freeze_throttle_clock(1_900_000_000)
    client = Client(ip="10.6.0.1")
    R["statuses"] = [(await client.post("/login", {"email": "a@example.com", "password": "wrong"})).status for _ in range(3)]

asyncio.run(main())
"""

_MAINTENANCE_DRIVER = r"""
async def main():
    resp = await Client().get("/")
    R["status"] = resp.status
    R["retry_after"] = resp.get_header("Retry-After")

asyncio.run(main())
"""

_CLI_DRIVER = r"""
from click.testing import CliRunner
from hunt.console.kernel import cli

outcome = CliRunner().invoke(cli, sys.argv[1:])
R["exit_code"] = outcome.exit_code
R["output"] = outcome.output
"""

_ADMIN_DRIVER = r"""
async def main():
    from app.models.user import User

    make_user("root", admin=True)
    make_user("plain")
    admin = Client(ip="10.7.0.1"); await admin.login("root@example.com")
    plain = Client(ip="10.7.0.2"); await plain.login("plain@example.com")

    R["non_admin_panel"] = (await plain.get("/hunt-admin/resources/users")).status
    R["admin_panel"] = (await admin.get("/hunt-admin/resources/users")).status

    created = await admin.post(
        "/hunt-admin/resources/users",
        {"name": "New", "email": "new@example.com", "password": "s3cret-pass", "is_admin": "1"},
        token_from="/hunt-admin/resources/users/create",
    )
    R["create"] = created.status
    new = User.where("email", "new@example.com").first()
    R["created"] = new is not None
    if new is not None:
        stored = str(new._attributes["password"])
        R["password_is_plaintext"] = stored == "s3cret-pass"
        R["password_is_hashed"] = stored.startswith("$")
        R["new_user_is_admin"] = bool(new._attributes.get("is_admin"))
        login = await Client(ip="10.7.0.3").login("new@example.com", "s3cret-pass")
        R["new_user_login"] = [login.status, login.location]

        # Untick "Admin" on the new user: the change must persist.
        demote = await admin.post(
            f"/hunt-admin/resources/users/{new._attributes['id']}",
            {"_method": "PUT", "name": "New", "email": "new@example.com"},
            token_from=f"/hunt-admin/resources/users/{new._attributes['id']}/edit",
        )
        R["demote_other"] = demote.status
        R["other_is_admin_after"] = bool(User.find(new._attributes["id"])._attributes.get("is_admin"))

    # An administrator cannot remove their own admin flag.
    self_demote = await admin.post(
        "/hunt-admin/resources/users/1",
        {"_method": "PUT", "name": "root", "email": "root@example.com"},
        token_from="/hunt-admin/resources/users/1/edit",
    )
    R["self_demote"] = self_demote.status
    R["still_admin"] = bool(User.find(1)._attributes.get("is_admin"))

asyncio.run(main())
"""


class TestBaseSkeleton:
    @pytest.fixture(scope="class")
    def base_result(self, tmp_path_factory):
        app = _scaffold(tmp_path_factory.mktemp("base"))
        return _drive(app, _BASE_DRIVER)

    def test_security_headers_are_sent(self, base_result):
        assert base_result["home"] == 200
        assert base_result["frame_options"] == "SAMEORIGIN"
        assert "default-src 'self'" in base_result["csp"]
        assert "frame-ancestors 'none'" in base_result["csp"]

    def test_sixth_login_in_a_window_is_rejected(self, base_result):
        assert base_result["register"] == [302, "/"]
        # The key is IP + lower-cased email, so "A@Example.com" counts against a@example.com.
        assert base_result["login_attempts"] == [200, 200, 200, 200, 200, 429]
        assert base_result["login_from_other_address"] == [302, "/"]

    def test_registration_is_throttled(self, base_result):
        assert base_result["register_attempts"] == [302, 302, 302, 302, 302, 429]

    def test_forgot_password_is_throttled_and_mails_the_link(self, base_result):
        assert base_result["forgot_attempts"] == [200, 200, 200, 200, 200, 429]
        # The reset broker issues at most one link per address per minute.
        assert base_result["reset_mails"] == 1
        assert base_result["reset_mail_has_link"] is True
        assert base_result["reset"] == [302, "/login?reset=1"]
        assert base_result["reset_reuse"] == 200  # single use: the form is shown again with an error
        assert base_result["login_with_new_password"] == [302, "/"]

    def test_login_throttle_is_shared_between_processes(self, tmp_path):
        """Two server workers are two processes: the limit must count across both."""
        app = _scaffold(tmp_path)
        first = _drive(app, _SHARED_THROTTLE_DRIVER, "first")
        second = _drive(app, _SHARED_THROTTLE_DRIVER, "second")
        assert first["statuses"] == [200, 200, 200]
        assert second["statuses"] == [200, 200, 429]

    def test_hunt_down_takes_the_generated_app_offline(self, tmp_path):
        # The CLI runs in a child process too: in this process another test may
        # have bootstrapped an Application, which would change where the
        # maintenance sentinel is looked up.
        app = _scaffold(tmp_path)
        assert _drive(app, _MAINTENANCE_DRIVER)["status"] == 200

        down = _drive(app, _CLI_DRIVER, "down")
        assert down["exit_code"] == 0, down
        assert "not registered" not in down["output"].lower()
        assert (app / ".maintenance").is_file()
        offline = _drive(app, _MAINTENANCE_DRIVER)
        assert offline["status"] == 503
        assert offline["retry_after"] is not None

        assert _drive(app, _CLI_DRIVER, "up")["exit_code"] == 0
        assert not (app / ".maintenance").exists()
        assert _drive(app, _MAINTENANCE_DRIVER)["status"] == 200

    def test_admin_user_resource(self, tmp_path):
        app = _scaffold(tmp_path)
        result = _drive(app, _ADMIN_DRIVER)
        assert result["non_admin_panel"] in (302, 403, 404)
        assert result["admin_panel"] == 200
        assert result["created"] is True, result
        assert result["password_is_plaintext"] is False
        assert result["password_is_hashed"] is True
        assert result["new_user_login"] == [302, "/"]
        # The Admin checkbox is honoured in both directions ...
        assert result["new_user_is_admin"] is True
        assert result["other_is_admin_after"] is False
        # ... except that administrators cannot demote themselves.
        assert result["still_admin"] is True


# ---------------------------------------------------------------------------
# GEN-05 / GEN-07 / GEN-13: make:crud and make:api output
# ---------------------------------------------------------------------------

_CRUD_DRIVER = r"""
async def main():
    from app.models.article import Article
    from app.models.product import Product

    make_user("a")
    anon = Client(ip="10.8.0.1")
    token = await anon.csrf("/login")
    stored = await anon.request("POST", "/articles", data={"title": "x", "body": "y", "_token": token})
    R["anon_store"] = [stored.status, stored.location]
    R["anon_api_index"] = (await anon.request("GET", "/api/products", headers={"Accept": "application/json"})).status
    R["anon_api_store"] = (
        await anon.request("POST", "/api/products", json_body={"name": "x", "price": "1"}, headers={"X-CSRF-TOKEN": token})
    ).status

    a = Client(ip="10.8.0.2"); await a.login("a@example.com")
    R["views"] = [(await a.get(path)).status for path in ("/articles", "/articles/create")]
    invalid = await a.post("/articles", {"title": "", "body": "b"}, token_from="/articles/create")
    R["invalid_store"] = invalid.status
    R["invalid_store_created"] = Article.all().count()
    R["store"] = (
        await a.post(
            "/articles",
            {"title": "T", "body": "B", "published": "1", "views": "5", "id": "77", "created_at": "1"},
            token_from="/articles/create",
        )
    ).status
    article = Article.find(1)
    R["stored"] = None if article is None else {k: article._attributes.get(k) for k in ("id", "title", "body", "views")}
    R["published"] = None if article is None else bool(article._attributes.get("published"))

    R["pages"] = [(await a.get(path)).status for path in ("/articles", "/articles/1", "/articles/1/edit")]
    R["bad_ids"] = [(await a.get(path)).status for path in ("/articles/abc", "/articles/999")]
    edit = await a.get("/articles/1/edit")
    R["edit_shows_current_values"] = 'value="T"' in edit.text and ">B</textarea>" in edit.text

    # Submit the edit form with a single field: the others keep their values.
    R["update"] = (await a.post("/articles/1/update", {"title": "T2"}, token_from="/articles/1/edit")).status
    after = Article.find(1)._attributes
    R["after_update"] = {k: after.get(k) for k in ("title", "body", "views")}
    R["bad_update"] = (await a.post("/articles/1/update", {"views": "many"}, token_from="/articles/1/edit")).status
    R["views_after_bad_update"] = Article.find(1)._attributes["views"]
    R["delete"] = (await a.post("/articles/1/delete", {}, token_from="/articles")).status
    R["deleted"] = Article.find(1) is None

    api_token = await a.csrf("/articles/create")
    headers = {"X-CSRF-TOKEN": api_token}
    created = await a.request("POST", "/api/products", json_body={"name": "Lamp", "price": "9.5", "id": 50}, headers=headers)
    R["api_store"] = created.status
    R["api_invalid"] = (await a.request("POST", "/api/products", json_body={"price": "x"}, headers=headers)).status
    R["api_show"] = (await a.request("GET", "/api/products/1", headers={"Accept": "application/json"})).status
    R["api_bad_id"] = (await a.request("GET", "/api/products/abc", headers={"Accept": "application/json"})).status
    R["api_update"] = (await a.request("PUT", "/api/products/1", json_body={"name": "Desk"}, headers=headers)).status
    product = Product.find(1)
    R["api_product"] = None if product is None else [product._attributes.get("name"), str(product._attributes.get("price"))]

    # A generated model only accepts its declared fields.
    probe = Article()
    try:
        probe.fill({"title": "ok", "is_admin": True, "id": 9})
        R["mass_assignment"] = sorted(probe._attributes)
    except Exception as exc:
        R["mass_assignment"] = type(exc).__name__

asyncio.run(main())
"""


class TestGeneratedCrudAndApi:
    @pytest.fixture(scope="class")
    def crud_result(self, tmp_path_factory):
        from hunt.console.commands.make.api_scaffold import make_api_command
        from hunt.console.commands.make.crud import make_crud_command

        app = _scaffold(tmp_path_factory.mktemp("crud"))
        _invoke_in(
            app, make_crud_command, ["Article", "--fields", "title:string body:text published:boolean views:integer"]
        )
        _invoke_in(app, make_api_command, ["Product", "--fields", "name:string price:decimal"])
        return _drive(app, _CRUD_DRIVER)

    def test_anonymous_requests_are_turned_away(self, crud_result):
        assert crud_result["anon_store"] == [302, "/login"]
        assert crud_result["anon_api_index"] == 401
        assert crud_result["anon_api_store"] == 401

    def test_all_four_views_render_against_the_skeleton(self, crud_result):
        assert crud_result["views"] == [200, 200]
        assert crud_result["pages"] == [200, 200, 200]
        assert crud_result["edit_shows_current_values"] is True

    def test_store_validates_and_ignores_undeclared_keys(self, crud_result):
        assert crud_result["invalid_store"] == 200
        assert crud_result["invalid_store_created"] == 0
        assert crud_result["store"] == 302
        assert crud_result["stored"] == {"id": 1, "title": "T", "body": "B", "views": 5}
        assert crud_result["published"] is True

    def test_non_numeric_and_missing_ids_are_404(self, crud_result):
        assert crud_result["bad_ids"] == [404, 404]
        assert crud_result["api_bad_id"] == 404

    def test_edit_form_preserves_untouched_fields(self, crud_result):
        assert crud_result["update"] == 302
        assert crud_result["after_update"] == {"title": "T2", "body": "B", "views": 5}
        assert crud_result["bad_update"] == 200
        assert crud_result["views_after_bad_update"] == 5

    def test_delete_form_works_without_method_spoofing(self, crud_result):
        assert crud_result["delete"] == 302
        assert crud_result["deleted"] is True

    def test_generated_api_validates_input(self, crud_result):
        assert crud_result["api_store"] == 201
        assert crud_result["api_invalid"] == 422
        assert crud_result["api_show"] == 200
        assert crud_result["api_update"] == 200
        assert crud_result["api_product"][0] == "Desk"
        assert float(crud_result["api_product"][1]) == 9.5

    def test_generated_model_rejects_unknown_keys(self, crud_result):
        # Either the keys are dropped or the fill is refused; they are never assigned.
        assert crud_result["mass_assignment"] in (["title"], "MassAssignmentError")


# ---------------------------------------------------------------------------
# GEN-14: two-factor scaffold
# ---------------------------------------------------------------------------

_TWO_FACTOR_DRIVER = r"""
async def main():
    import pyotp
    from app.models.user import User

    make_user("t")
    first = Client(ip="10.9.0.1")
    login = await first.login("t@example.com")
    R["login"] = [login.status, login.location]
    R["setup_page"] = (await first.get("/two-factor/setup")).status
    setup = await first.post("/two-factor/setup", {"password": "password1"}, token_from="/two-factor/setup")
    R["setup"] = setup.status
    secret = re.search(r"[A-Z2-7]{16,}", setup.text).group(0)
    token = re.search(r'name="_token" value="([^"]+)"', setup.text).group(1)
    # Enrol with the previous time-step so the challenge below can use the current one.
    code = pyotp.TOTP(secret).at(time.time() - 30)
    R["confirm"] = (await first.request("POST", "/two-factor/confirm", data={"code": code, "_token": token})).status
    R["enabled"] = bool(User.find(1)._attributes.get("two_factor_enabled"))

    second = Client(ip="10.9.0.2")
    relogin = await second.login("t@example.com")
    R["login_with_2fa"] = [relogin.status, relogin.location]
    R["not_logged_in_yet"] = (await second.get("/two-factor/manage")).location
    challenge = await second.get("/two-factor/challenge")
    R["challenge_page"] = challenge.status
    token = re.search(r'name="_token" value="([^"]+)"', challenge.text).group(1)
    passed = await second.request("POST", "/two-factor/challenge", data={"code": pyotp.TOTP(secret).now(), "_token": token})
    R["challenge"] = [passed.status, passed.location]
    R["manage_after_challenge"] = (await second.get("/two-factor/manage")).status

    wrong = await Client(ip="10.9.0.3").login("t@example.com", "wrong-password")
    R["wrong_password"] = [wrong.status, wrong.location]

asyncio.run(main())
"""


class TestTwoFactorScaffold:
    def test_full_flow_enrol_logout_login_challenge(self, tmp_path):
        pytest.importorskip("pyotp")
        from hunt.console.commands.make.two_factor import make_two_factor_command

        app = _scaffold(tmp_path)
        _invoke_in(app, make_two_factor_command, [])
        bootstrap = app / "bootstrap" / "app.py"
        source = bootstrap.read_text()
        assert "admin_routes(router)\n" in source
        bootstrap.write_text(
            source.replace(
                "admin_routes(router)\n",
                "admin_routes(router)\nfrom routes.two_factor import register as two_factor_routes\n"
                "two_factor_routes(router)\n",
                1,
            )
        )

        result = _drive(app, _TWO_FACTOR_DRIVER)
        assert result["login"] == [302, "/"]
        assert result["setup_page"] == 200
        assert result["setup"] == 200
        assert result["confirm"] == 200
        assert result["enabled"] is True
        # Correct password + 2FA enabled: the login controller hands over to the challenge.
        assert result["login_with_2fa"] == [302, "/two-factor/challenge"]
        assert result["not_logged_in_yet"] == "/login"
        assert result["challenge_page"] == 200
        # The post-challenge redirect follows config/auth.py ("home"), not a hard-coded /dashboard.
        assert result["challenge"] == [302, "/"]
        assert result["manage_after_challenge"] == 200
        assert result["wrong_password"] == [200, None]
