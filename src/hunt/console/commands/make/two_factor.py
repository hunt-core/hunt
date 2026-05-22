from __future__ import annotations

from pathlib import Path

import click


@click.command("make:2fa-controllers")
def make_two_factor_command() -> None:
    """Scaffold two-factor authentication controllers, templates, and routes."""
    cwd = Path.cwd()
    _write_controllers(cwd)
    _write_templates(cwd)
    _write_routes_snippet(cwd)
    _write_migration(cwd)
    click.echo("\n  Two-factor authentication scaffolded.")
    click.echo("  Next steps:")
    click.echo("    1. Run the generated migration: hunt migrate:run")
    click.echo("    2. Add the routes from routes/two_factor.py into routes/web.py")
    click.echo("    3. Add EnsureTwoFactorAuthenticated to your global middleware if desired")


def _write_controllers(cwd: Path) -> None:
    dest = cwd / "app" / "controllers" / "two_factor_controller.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_CONTROLLER_STUB)
    click.echo(f"  Created Controller: {dest.relative_to(cwd)}")


def _write_templates(cwd: Path) -> None:
    templates_dir = cwd / "resources" / "views" / "auth" / "two_factor"
    templates_dir.mkdir(parents=True, exist_ok=True)
    for name, content in _TEMPLATES.items():
        dest = templates_dir / name
        dest.write_text(content)
        click.echo(f"  Created Template:   {dest.relative_to(cwd)}")


def _write_routes_snippet(cwd: Path) -> None:
    dest = cwd / "routes" / "two_factor.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_ROUTES_STUB)
    click.echo(f"  Created Routes:     {dest.relative_to(cwd)}")


def _write_migration(cwd: Path) -> None:
    import time

    stamp = str(int(time.time()))
    dest = cwd / "database" / "migrations" / f"{stamp}_add_two_factor_to_users_table.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_MIGRATION_STUB)
    click.echo(f"  Created Migration:  {dest.relative_to(cwd)}")


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_CONTROLLER_STUB = """\
from hunt.auth.controllers.two_factor import (
    TwoFactorChallengeController,
    TwoFactorManageController,
    TwoFactorSetupController,
)

__all__ = [
    "TwoFactorSetupController",
    "TwoFactorChallengeController",
    "TwoFactorManageController",
]
"""

_ROUTES_STUB = """\
from hunt.http.router import Router
from hunt.auth.controllers.two_factor import (
    TwoFactorChallengeController,
    TwoFactorManageController,
    TwoFactorSetupController,
)


def register(router: Router) -> None:
    # 2FA setup flow (requires authenticated user)
    router.get("/two-factor/setup", TwoFactorSetupController().show).named("two-factor.setup")
    router.post("/two-factor/setup", TwoFactorSetupController().store).named("two-factor.setup.store")
    router.get("/two-factor/confirm", TwoFactorSetupController().show)
    router.post("/two-factor/confirm", TwoFactorSetupController().confirm).named("two-factor.confirm")
    router.delete("/two-factor", TwoFactorSetupController().destroy).named("two-factor.destroy")

    # 2FA login challenge
    router.get("/two-factor/challenge", TwoFactorChallengeController().show).named("two-factor.challenge")
    router.post("/two-factor/challenge", TwoFactorChallengeController().store)

    # 2FA management (recovery codes)
    router.get("/two-factor/manage", TwoFactorManageController().show).named("two-factor.manage")
    router.post("/two-factor/regenerate", TwoFactorManageController().regenerate).named("two-factor.regenerate")
"""

_MIGRATION_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class AddTwoFactorToUsersTable(Migration):
    def up(self) -> None:
        Schema.table("users", lambda bp: [
            bp.text("two_factor_secret").nullable(),
            bp.boolean("two_factor_enabled").default(False),
            bp.text("two_factor_recovery_codes").nullable(),
        ])

    def down(self) -> None:
        Schema.table("users", lambda bp: [
            bp.drop_column("two_factor_secret"),
            bp.drop_column("two_factor_enabled"),
            bp.drop_column("two_factor_recovery_codes"),
        ])
"""

_TEMPLATES: dict[str, str] = {
    "setup.html": """\
{% extends "layouts/app.html" %}

{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
  <div class="max-w-md w-full space-y-8">
    <div>
      <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900">
        Enable Two-Factor Authentication
      </h2>
      <p class="mt-2 text-center text-sm text-gray-600">
        Add an extra layer of security to your account.
      </p>
    </div>

    {% if session_has("error") %}
    <div class="rounded-md bg-red-50 p-4">
      <p class="text-sm text-red-700">{{ session_get("error") }}</p>
    </div>
    {% endif %}

    <form method="POST" action="/two-factor/setup" class="mt-8 space-y-6">
      {{ csrf_field() | raw }}
      <div>
        <label for="password" class="block text-sm font-medium text-gray-700">
          Confirm your password to continue
        </label>
        <input id="password" name="password" type="password" required
          class="mt-1 appearance-none block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
          placeholder="Your current password">
      </div>
      <div>
        <button type="submit"
          class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
          Continue
        </button>
      </div>
    </form>
  </div>
</div>
{% endblock %}
""",
    "confirm.html": """\
{% extends "layouts/app.html" %}

{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
  <div class="max-w-md w-full space-y-8">
    <div>
      <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900">
        Scan QR Code
      </h2>
      <p class="mt-2 text-center text-sm text-gray-600">
        Scan this QR code with your authenticator app, then enter the six-digit code below.
      </p>
    </div>

    {% if session_has("error") %}
    <div class="rounded-md bg-red-50 p-4">
      <p class="text-sm text-red-700">{{ session_get("error") }}</p>
    </div>
    {% endif %}

    <div class="flex flex-col items-center space-y-4">
      <div id="qrcode" class="p-4 bg-white border border-gray-200 rounded-lg shadow"></div>
      <p class="text-xs text-gray-500 break-all">Manual key: <code>{{ secret }}</code></p>
    </div>

    <form method="POST" action="/two-factor/confirm" class="mt-8 space-y-6">
      {{ csrf_field() | raw }}
      <div>
        <label for="code" class="block text-sm font-medium text-gray-700">Verification code</label>
        <input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code"
          maxlength="6" pattern="[0-9]{6}" required
          class="mt-1 appearance-none block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm text-center text-2xl tracking-widest"
          placeholder="000000">
      </div>
      <div>
        <button type="submit"
          class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
          Enable Two-Factor Authentication
        </button>
      </div>
    </form>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>
<script>
  new QRCode(document.getElementById("qrcode"), {
    text: {{ qr_url | tojson }},
    width: 200,
    height: 200,
  });
</script>
{% endblock %}
""",
    "recovery.html": """\
{% extends "layouts/app.html" %}

{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
  <div class="max-w-md w-full space-y-8">
    <div>
      <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900">
        Save Your Recovery Codes
      </h2>
      <p class="mt-2 text-center text-sm text-gray-600">
        Store these codes somewhere safe. Each code can only be used once.
      </p>
    </div>

    <div class="bg-gray-100 rounded-lg p-6">
      <ul class="grid grid-cols-2 gap-2">
        {% for code in recovery_codes %}
        <li class="font-mono text-sm text-gray-800 bg-white rounded px-3 py-2 border border-gray-200">
          {{ code }}
        </li>
        {% endfor %}
      </ul>
    </div>

    <div class="flex justify-center">
      <a href="/two-factor/manage"
        class="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700">
        I have saved my recovery codes
      </a>
    </div>
  </div>
</div>
{% endblock %}
""",
    "challenge.html": """\
{% extends "layouts/app.html" %}

{% block content %}
<div class="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
  <div class="max-w-md w-full space-y-8">
    <div>
      <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900">
        Two-Factor Authentication
      </h2>
      <p class="mt-2 text-center text-sm text-gray-600">
        Enter the six-digit code from your authenticator app, or use a recovery code.
      </p>
    </div>

    {% if session_has("error") %}
    <div class="rounded-md bg-red-50 p-4">
      <p class="text-sm text-red-700">{{ session_get("error") }}</p>
    </div>
    {% endif %}

    <form method="POST" action="/two-factor/challenge" class="mt-8 space-y-6">
      {{ csrf_field() | raw }}
      <div>
        <label for="code" class="block text-sm font-medium text-gray-700">Authentication code</label>
        <input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code"
          required autofocus
          class="mt-1 appearance-none block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm text-center text-2xl tracking-widest"
          placeholder="000000 or recovery-code">
      </div>
      <div>
        <button type="submit"
          class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
          Verify
        </button>
      </div>
    </form>
  </div>
</div>
{% endblock %}
""",
    "manage.html": """\
{% extends "layouts/app.html" %}

{% block content %}
<div class="max-w-2xl mx-auto py-10 px-4 sm:px-6 lg:px-8 space-y-8">
  <div>
    <h2 class="text-2xl font-bold text-gray-900">Two-Factor Authentication</h2>
    <p class="mt-1 text-sm text-gray-500">
      {% if enabled %}
        Two-factor authentication is <span class="font-semibold text-green-600">enabled</span> on your account.
      {% else %}
        Two-factor authentication is <span class="font-semibold text-red-600">disabled</span>.
      {% endif %}
    </p>
  </div>

  {% if session_has("success") %}
  <div class="rounded-md bg-green-50 p-4">
    <p class="text-sm text-green-700">{{ session_get("success") }}</p>
  </div>
  {% endif %}

  {% if session_has("error") %}
  <div class="rounded-md bg-red-50 p-4">
    <p class="text-sm text-red-700">{{ session_get("error") }}</p>
  </div>
  {% endif %}

  {% if enabled %}
  <!-- Recovery codes section -->
  {% if recovery_codes %}
  <div class="bg-white shadow sm:rounded-lg">
    <div class="px-4 py-5 sm:p-6">
      <h3 class="text-lg font-medium text-gray-900">Recovery Codes</h3>
      <p class="mt-1 text-sm text-gray-500">
        Store these codes safely. Each can only be used once if you lose your device.
      </p>
      <div class="mt-4 bg-gray-50 rounded-md p-4">
        <ul class="grid grid-cols-2 gap-2">
          {% for code in recovery_codes %}
          <li class="font-mono text-sm text-gray-700">{{ code }}</li>
          {% endfor %}
        </ul>
      </div>
      <form method="POST" action="/two-factor/regenerate" class="mt-4">
        {{ csrf_field() | raw }}
        <input name="password" type="password" required
          placeholder="Confirm password to regenerate"
          class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
        <button type="submit"
          class="mt-3 inline-flex items-center px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50">
          Regenerate Recovery Codes
        </button>
      </form>
    </div>
  </div>
  {% endif %}

  <!-- Disable 2FA -->
  <div class="bg-white shadow sm:rounded-lg">
    <div class="px-4 py-5 sm:p-6">
      <h3 class="text-lg font-medium text-gray-900">Disable Two-Factor Authentication</h3>
      <p class="mt-1 text-sm text-gray-500">
        This will remove the extra layer of security from your account.
      </p>
      <form method="POST" action="/two-factor" class="mt-4 space-y-3">
        {{ csrf_field() | raw }}
        <input type="hidden" name="_method" value="DELETE">
        <input name="password" type="password" required
          placeholder="Confirm password"
          class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
        <button type="submit"
          class="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-red-600 hover:bg-red-700">
          Disable Two-Factor Authentication
        </button>
      </form>
    </div>
  </div>

  {% else %}
  <!-- Enable 2FA -->
  <div class="bg-white shadow sm:rounded-lg">
    <div class="px-4 py-5 sm:p-6">
      <h3 class="text-lg font-medium text-gray-900">Enable Two-Factor Authentication</h3>
      <p class="mt-1 text-sm text-gray-500">
        Use an authenticator app like Google Authenticator or Authy to generate time-based codes.
      </p>
      <div class="mt-4">
        <a href="/two-factor/setup"
          class="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700">
          Enable Two-Factor Authentication
        </a>
      </div>
    </div>
  </div>
  {% endif %}
</div>
{% endblock %}
""",
}
