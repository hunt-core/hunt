"""SaaS starter kit — teams, memberships, billing stub, subdomain routing."""

from __future__ import annotations

from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def apply(target: Path) -> None:
    """Overlay SaaS starter kit files on the base skeleton."""
    from hunt.console.commands.new import csrf_middleware_source, secure_headers_source

    # The Stripe webhook is authenticated by its signature, not by the session
    # cookie, so it is the one route exempt from CSRF.
    _write(
        target / "app" / "middleware" / "verify_csrf_token.py",
        csrf_middleware_source(["/api/billing/webhook"]),
    )
    # The SaaS layout loads Tailwind from its CDN.
    _write(
        target / "app" / "middleware" / "secure_headers.py",
        secure_headers_source("https://cdn.tailwindcss.com"),
    )
    _add_billing_extra(target / "pyproject.toml")
    _write(target / "app" / "models" / "team.py", _TEAM_MODEL)
    _write(target / "app" / "models" / "membership.py", _MEMBERSHIP_MODEL)
    _write(target / "app" / "controllers" / "team_controller.py", _TEAM_CONTROLLER)
    _write(target / "app" / "controllers" / "billing_controller.py", _BILLING_CONTROLLER)
    _write(target / "app" / "middleware" / "tenant.py", _TENANT_MIDDLEWARE)
    _write(target / "app" / "admin" / "team_resource.py", _TEAM_ADMIN)
    _write(target / "database" / "migrations" / "0100_create_teams_table.py", _MIG_TEAMS)
    _write(target / "database" / "migrations" / "0101_create_memberships_table.py", _MIG_MEMBERSHIPS)
    _write(target / "resources" / "views" / "teams" / "index.html", _TEAMS_INDEX)
    _write(target / "resources" / "views" / "teams" / "create.html", _TEAMS_CREATE)
    _write(target / "resources" / "views" / "billing" / "show.html", _BILLING_SHOW)
    _write(target / "resources" / "views" / "layout.html", _SAAS_LAYOUT)
    _write(target / "routes" / "web.py", _SAAS_ROUTES_WEB)
    _write(target / "routes" / "admin.py", _SAAS_ROUTES_ADMIN)
    _write(target / "README.md", _README)


def _add_billing_extra(pyproject: Path) -> None:
    """Declare the optional `billing` extra (stripe) used by the webhook."""
    if not pyproject.exists():
        return
    text = pyproject.read_text(encoding="utf-8")
    anchor = "\n[tool.hatch.build.targets.wheel]"
    if "billing = [" in text or anchor not in text:
        return
    extra = 'billing = [\n    "stripe>=8.0",\n]\n'
    pyproject.write_text(text.replace(anchor, extra + anchor, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_TEAM_MODEL = """\
from hunt.database.model import Model


class Team(Model):
    table = "teams"
    # plan and owner_id are deliberately not fillable: the plan changes only
    # through billing, and the owner is the logged-in user. Set them explicitly
    # (team.plan = "pro") and never from request input.
    fillable = ["name", "slug"]

    def owner(self):
        from app.models.user import User
        return User.find(self._attributes.get("owner_id"))

    def members(self):
        from app.models.membership import Membership
        return Membership.where("team_id", self._attributes.get("id")).get()
"""

_MEMBERSHIP_MODEL = """\
from hunt.database.model import Model


class Membership(Model):
    table = "memberships"
    # Memberships grant access. Build them from values your code decided on
    # (as TeamController.store does); never pass request input to create().
    fillable = ["team_id", "user_id", "role"]

    ROLES = ("owner", "admin", "member")

    def team(self):
        from app.models.team import Team
        return Team.find(self._attributes.get("team_id"))

    def user(self):
        from app.models.user import User
        return User.find(self._attributes.get("user_id"))
"""

# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------

_TEAM_CONTROLLER = """\
import re

from hunt.auth.manager import Auth
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.validation.validator import Validator
from app.models.team import Team
from app.models.membership import Membership


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _unique_slug(name: str) -> str:
    \"\"\"Slug for *name*; a counter is appended when another team already uses it.\"\"\"
    base = _slugify(name) or "team"
    slug, counter = base, 2
    while Team.where("slug", slug).first() is not None:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


class TeamController(Controller):
    def index(self, request: Request) -> Response:
        user = Auth.user()
        memberships = Membership.where("user_id", user._attributes["id"]).get() if user else []
        team_ids = [m._attributes["team_id"] for m in memberships]
        teams = [Team.find(tid) for tid in team_ids]
        return self.view("teams.index", {"teams": [t for t in teams if t]})

    def create(self, request: Request) -> Response:
        return self.view("teams.create", {"errors": {}, "old": {}})

    def store(self, request: Request) -> Response:
        user = Auth.user()
        data = {"name": request.input("name", "")}
        v = Validator.make(data, {"name": "required|string|max:255"})
        if v.fails():
            return self.view("teams.create", {
                "errors": v.errors()._errors,
                "old": data,
            })
        team = Team()
        team.fill({"name": data["name"], "slug": _unique_slug(data["name"])})
        # Every team starts on the free plan, whatever the form submitted.
        # Paid plans are set by the billing flow after a successful payment.
        team.plan = "free"
        team.owner_id = user._attributes["id"]
        team.save()
        Membership.create({
            "team_id": team._attributes["id"],
            "user_id": user._attributes["id"],
            "role": "owner",
        })
        return RedirectResponse("/teams")
"""

_BILLING_CONTROLLER = """\
\"\"\"Billing stub — wire up Stripe or your payment provider here.\"\"\"
import os

from hunt.auth.manager import Auth
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import HttpException, Response, RedirectResponse
from app.models.membership import Membership
from app.models.team import Team

PLANS = {
    "free": {"name": "Free", "price": 0, "features": ["1 team member", "5 projects"]},
    "pro": {"name": "Pro", "price": 29, "features": ["10 team members", "Unlimited projects", "Priority support"]},
    "enterprise": {"name": "Enterprise", "price": 99, "features": ["Unlimited members", "Custom domain", "SLA", "Dedicated support"]},
}

# Roles that may see and change a team's billing.
BILLING_ROLES = ("owner", "admin")


def _team_for(user, team_id) -> Team:
    \"\"\"Return the team if *user* manages its billing.

    404 when the team does not exist, 403 when the user is not an owner or
    admin of it.
    \"\"\"
    try:
        team = Team.find(int(team_id))
    except (TypeError, ValueError):
        team = None
    if team is None:
        raise HttpException(404, "Team not found.")
    if user is None:
        raise HttpException(403, "Forbidden.")
    membership = (
        Membership.where("team_id", team._attributes["id"])
        .where("user_id", user._attributes["id"])
        .first()
    )
    if membership is None or membership._attributes.get("role") not in BILLING_ROLES:
        raise HttpException(403, "You are not allowed to manage billing for this team.")
    return team


def _fake_billing_enabled() -> bool:
    \"\"\"BILLING_FAKE=true lets plans change without payment. Local development only.\"\"\"
    return os.environ.get("BILLING_FAKE", "false").lower() == "true" and os.environ.get("APP_ENV") != "production"


class BillingController(Controller):
    def _page(self, team: Team, error: str | None = None, status: int = 200) -> Response:
        current_plan = PLANS.get(team._attributes.get("plan", "free"), PLANS["free"])
        response = self.view("billing.show", {
            "team": team,
            "current_plan": current_plan,
            "plans": PLANS,
            "error": error,
        })
        response.status = status
        return response

    def show(self, request: Request, team_id: int) -> Response:
        return self._page(_team_for(Auth.user(), team_id))

    def update_plan(self, request: Request, team_id: int) -> Response:
        team = _team_for(Auth.user(), team_id)
        plan = request.input("plan", "free")
        if plan not in PLANS:
            plan = "free"

        if PLANS[plan]["price"] > 0 and not _fake_billing_enabled():
            # No payment provider is wired up yet, so a paid plan cannot be
            # granted. Create a checkout session here and set the plan from
            # the webhook once the payment succeeded:
            #   stripe.checkout.Session.create(mode="subscription", ...)
            return self._page(
                team,
                error="Paid plans are not available yet: no payment provider is configured.",
                status=402,
            )

        team.plan = plan
        team.save()
        return RedirectResponse(f"/teams/{team._attributes['id']}/billing")

    def webhook(self, request: Request) -> Response:
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if not secret:
            return Response(
                '{"error": "STRIPE_WEBHOOK_SECRET is not configured. Set it in your .env file."}',
                status=500,
                content_type="application/json",
            )

        try:
            import stripe
        except ImportError:
            return Response(
                '{"error": "The stripe package is not installed. Install the billing extra: pip install stripe"}',
                status=500,
                content_type="application/json",
            )

        sig = request.header("Stripe-Signature", "") or ""
        try:
            # request.body is the raw bytes Stripe signed.
            event = stripe.Webhook.construct_event(request.body, sig, secret)
        except Exception:
            return Response(
                '{"error": "Webhook signature verification failed"}',
                status=400,
                content_type="application/json",
            )

        self._handle_event(event)
        return Response('{"received": true}', status=200, content_type="application/json")

    def _handle_event(self, event) -> None:
        \"\"\"Handle verified Stripe events — see https://stripe.com/docs/webhooks\"\"\"
        # if event["type"] == "customer.subscription.updated":
        #     subscription = event["data"]["object"]
        #     team = Team.where("stripe_subscription_id", subscription["id"]).first()
        #     if team:
        #         team.plan = subscription["metadata"].get("plan", "free")
        #         team.save()
"""

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

_TENANT_MIDDLEWARE = """\
\"\"\"Resolve tenant (Team) from subdomain and inject into request.

Usage in routes:
    with router.group(middleware=[TenantMiddleware]):
        router.get(\"/dashboard\", DashboardController().index)

The middleware reads the subdomain from the Host header and looks up
a matching Team record. A 404 is returned for unknown tenants.
\"\"\"
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response


class TenantMiddleware(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        host = request.header("host", "")
        parts = host.split(".")
        if len(parts) < 3:
            return await next(request)

        subdomain = parts[0]
        from app.models.team import Team
        team = Team.where("slug", subdomain).first()
        if team is None:
            from hunt.http.response import Response as Res
            return Res("Tenant not found", status=404)

        request.team = team
        return await next(request)
"""

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

_TEAM_ADMIN = """\
from hunt.admin import AdminResource
from hunt.admin.fields import Text, DateTime
from app.admin.user_resource import is_admin
from app.models.team import Team


class TeamResource(AdminResource):
    model = Team
    label = "Team"
    search_columns = ["name", "slug"]
    per_page = 20

    def fields(self):
        return [
            Text("Id", attribute="id").readonly().sortable(),
            Text("Name", attribute="name").rules("required", "string", "max:255").sortable(),
            Text("Slug", attribute="slug").readonly().sortable(),
            Text("Plan", attribute="plan").sortable(),
            Text("Owner Id", attribute="owner_id").sortable(),
            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),
        ]

    # Administrators only. Narrow these (or return False) to restrict the panel further.
    def can_view_any(self, request) -> bool:
        return is_admin(request)

    def can_create(self, request) -> bool:
        return is_admin(request)

    def can_update(self, request, instance=None) -> bool:
        return is_admin(request)

    def can_delete(self, request, instance=None) -> bool:
        return is_admin(request)
"""

# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_MIG_TEAMS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateTeamsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.integer("owner_id")
            table.string("name")
            table.string("slug").unique()
            table.string("plan", 50).default("free")
            table.string("stripe_customer_id", 100).nullable()
            table.string("stripe_subscription_id", 100).nullable()
            table.timestamps()

        Schema.create("teams", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("teams")
"""

_MIG_MEMBERSHIPS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateMembershipsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.integer("team_id")
            table.integer("user_id")
            table.string("role", 50).default("member")
            table.timestamps()
            table.index(["team_id", "user_id"])

        Schema.create("memberships", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("memberships")
"""

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

_SAAS_LAYOUT = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ page_title if page_title is defined else (config('app.name', 'SaaS') if config is defined else 'SaaS') }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 text-gray-900 min-h-screen flex flex-col">
    <nav class="bg-white shadow-sm border-b">
        <div class="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
            <a href="/" class="text-xl font-bold text-indigo-600">
                {{ config('app.name', 'SaaS') if config is defined else 'SaaS' }}
            </a>
            <div class="flex gap-4 items-center text-sm">
                {% if auth_check is defined and auth_check() %}
                    <a href="/teams" class="text-gray-600 hover:text-indigo-600">Teams</a>
                    <a href="/teams/create" class="text-gray-600 hover:text-indigo-600">New Team</a>
                    <form method="POST" action="/logout" class="inline">
                        @csrf
                        <button type="submit" class="text-gray-600 hover:text-indigo-600">Logout</button>
                    </form>
                {% else %}
                    <a href="/login" class="text-gray-600 hover:text-indigo-600">Login</a>
                    <a href="/register" class="bg-indigo-600 text-white px-3 py-1.5 rounded-md hover:bg-indigo-700">Get started</a>
                {% endif %}
            </div>
        </div>
    </nav>
    <main class="flex-1 max-w-6xl mx-auto px-4 py-8 w-full">
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-t py-4 text-center text-sm text-gray-400">
        Built with <a href="https://hunt-framework.com" class="text-indigo-500">hunt</a>
    </footer>
</body>
</html>
"""

_TEAMS_INDEX = """\
@extends('layout')

@section('content')
<div class="flex items-center justify-between mb-8">
    <h1 class="text-3xl font-bold">Your Teams</h1>
    <a href="/teams/create" class="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 text-sm">New Team</a>
</div>

{% if teams %}
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    {% for team in teams %}
    <div class="bg-white rounded-lg shadow-sm border p-6">
        <div class="flex items-start justify-between">
            <div>
                <h2 class="text-lg font-semibold">{{ team._attributes.name }}</h2>
                <p class="text-sm text-gray-500 mt-1">Plan: <span class="capitalize font-medium">{{ team._attributes.plan }}</span></p>
            </div>
            <a href="/teams/{{ team._attributes.id }}/billing"
               class="text-xs border border-gray-200 px-2 py-1 rounded hover:bg-gray-50">Billing</a>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="text-center py-16 text-gray-400">
    <p class="text-lg">You don't belong to any teams yet.</p>
    <a href="/teams/create" class="mt-4 inline-block text-indigo-600 hover:underline">Create your first team</a>
</div>
{% endif %}
@endsection
"""

_TEAMS_CREATE = """\
@extends('layout')

@section('content')
<div class="bg-white rounded-lg shadow-sm border p-8 max-w-lg">
    <h1 class="text-2xl font-bold mb-6">Create Team</h1>

    @errors

    <form method="POST" action="/teams" class="space-y-5">
        @csrf
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input type="text" name="name" value="{{ old.get('name', '') }}" placeholder="Acme Corp"
                   class="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500">
            {% if errors.name %}<p class="text-red-500 text-xs mt-1">{{ errors.name[0] }}</p>{% endif %}
        </div>
        <p class="text-sm text-gray-500">
            New teams start on the <strong>Free</strong> plan. You can change the plan
            later from the team's billing page.
        </p>
        <div class="flex gap-3 pt-2">
            <button type="submit" class="bg-indigo-600 text-white px-5 py-2 rounded-md hover:bg-indigo-700">Create Team</button>
            <a href="/teams" class="text-gray-500 px-5 py-2 hover:underline">Cancel</a>
        </div>
    </form>
</div>
@endsection
"""

_BILLING_SHOW = """\
@extends('layout')

@section('content')
<div class="max-w-2xl">
    <h1 class="text-2xl font-bold mb-2">Billing — {{ team._attributes.name }}</h1>
    <p class="text-gray-500 text-sm mb-8">
        Current plan: <span class="font-semibold capitalize">{{ current_plan.name }}</span>
        {% if current_plan.price > 0 %} · ${{ current_plan.price }}/month{% else %} · Free{% endif %}
    </p>

    {% if error %}
    <div class="mb-6 p-4 bg-red-50 border border-red-200 rounded-md text-sm text-red-800">{{ error }}</div>
    {% endif %}

    <form method="POST" action="/teams/{{ team._attributes.id }}/billing/plan" class="space-y-4">
        @csrf
        <h2 class="text-lg font-semibold mb-4">Change Plan</h2>
        {% for key, plan in plans.items() %}
        <label class="flex items-start gap-4 p-4 border rounded-lg cursor-pointer hover:bg-gray-50 {% if team._attributes.plan == key %}border-indigo-500 bg-indigo-50{% endif %}">
            <input type="radio" name="plan" value="{{ key }}" class="mt-1 text-indigo-600"
                   {% if team._attributes.plan == key %}checked{% endif %}>
            <div class="flex-1">
                <div class="font-medium">{{ plan.name }}{% if plan.price > 0 %} — ${{ plan.price }}/mo{% endif %}</div>
                <ul class="text-sm text-gray-500 mt-1 space-y-0.5">
                    {% for feature in plan.features %}
                    <li>• {{ feature }}</li>
                    {% endfor %}
                </ul>
            </div>
        </label>
        {% endfor %}
        <button type="submit" class="mt-4 bg-indigo-600 text-white px-5 py-2 rounded-md hover:bg-indigo-700">Update Plan</button>
    </form>

    <div class="mt-10 p-4 bg-yellow-50 border border-yellow-200 rounded-md text-sm text-yellow-800">
        <strong>Payments not configured.</strong> Edit <code>app/controllers/billing_controller.py</code>
        and wire up your payment provider (Stripe, Paddle, etc.) in the <code>update_plan</code> and
        <code>webhook</code> methods.
    </div>
</div>
@endsection
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_SAAS_ROUTES_WEB = """\
from hunt.http.router import Router


def register(router: Router) -> None:
    from hunt.http.middleware.authenticate import Authenticate
    from app.controllers.welcome_controller import WelcomeController
    from app.controllers.team_controller import TeamController
    from app.controllers.billing_controller import BillingController

    router.get("/", WelcomeController().index).named("welcome")

    teams = TeamController()
    billing = BillingController()

    with router.group(middleware=[Authenticate]):
        router.get("/teams", teams.index).named("teams.index")
        router.get("/teams/create", teams.create).named("teams.create")
        router.post("/teams", teams.store).named("teams.store")
        router.get("/teams/{team_id}/billing", billing.show).named("teams.billing.show")
        router.post("/teams/{team_id}/billing/plan", billing.update_plan).named("teams.billing.update")

    # Called by Stripe: authenticated by the webhook signature and exempt from
    # CSRF in app/middleware/verify_csrf_token.py.
    router.post("/api/billing/webhook", billing.webhook).named("billing.webhook")
"""

_SAAS_ROUTES_ADMIN = """\
from hunt.admin import Admin
from hunt.admin.metrics import ValueMetric
from hunt.http.router import Router
from app.admin.user_resource import UserResource
from app.admin.team_resource import TeamResource
from app.models.user import User
from app.models.team import Team

Admin.resource(UserResource)
Admin.resource(TeamResource)

Admin.dashboard(
    ValueMetric("Total Users", lambda: User.query().count()),
    ValueMetric("Total Teams", lambda: Team.query().count()),
    ValueMetric("Pro Teams", lambda: Team.where("plan", "pro").count()),
    ValueMetric("Enterprise Teams", lambda: Team.where("plan", "enterprise").count()),
)


def register(router: Router) -> None:
    from hunt.auth.manager import Auth

    Admin.gate(
        lambda request: Auth.check()
        and bool(getattr(Auth.user(), "_attributes", {}).get("is_admin"))
    )
    # The system tools (sessions, cache, logs, queue) expose operational data
    # and are denied by default. Enable the ones you need, for administrators:
    # Admin.authorize_tool("sessions")
    # Admin.authorize_tool("cache")
    # Admin.authorize_tool("logs")
    # Admin.authorize_tool("queue")
    Admin.register_to(router)
"""

# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

_README = """\
# SaaS Starter

This application was created with the **hunt SaaS starter kit**.

## What was created

- **Models**: `Team`, `Membership` (plus base `User` from skeleton)
- **Controllers**: `TeamController`, `BillingController` stub
- **Middleware**: `TenantMiddleware` — resolves tenant from subdomain
- **Admin**: TeamResource dashboard with plan metrics
- **Migrations**: teams (with `plan` + Stripe columns), memberships
- **Views**: team list, team create, billing/plan selector

## Get started

```bash
cd <your-app>
uv venv && uv pip install -e .
hunt migrate
hunt serve
```

Then visit:
- `http://localhost:8000/register` — create your first account
- `http://localhost:8000/teams` — create a team
- `http://localhost:8000/hunt-admin` — admin panel (requires `is_admin = True`)

## Billing integration

Only a team's owners and admins can open its billing page or change its plan.
New teams always start on the `free` plan, and `update_plan` refuses paid plans
until a payment provider is wired up in `app/controllers/billing_controller.py`.
For local experiments set `BILLING_FAKE=true` in `.env` (ignored when
`APP_ENV=production`) to switch plans without payment.

The Stripe webhook is pre-wired at `POST /api/billing/webhook`. Install the
optional dependency and set the signing secret:

```bash
uv pip install -e ".[billing]"
echo "STRIPE_WEBHOOK_SECRET=whsec_..." >> .env
```

## Subdomain-based tenancy

To activate tenant routing, apply `TenantMiddleware` to your tenant routes:

```python
from app.middleware.tenant import TenantMiddleware

with router.group(middleware=[TenantMiddleware]):
    router.get("/dashboard", DashboardController().index)
```

The middleware reads the subdomain from the `Host` header and injects
`request.team` for the matched `Team` model.

## Plans

The starter ships three plans: `free`, `pro` ($29/mo), `enterprise` ($99/mo).
Edit `PLANS` in `billing_controller.py` to change pricing and features.
"""
