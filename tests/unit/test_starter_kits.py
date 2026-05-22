"""Tests for M24 — Starter Kits."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from hunt.console.commands.new import new_command

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new(tmp_path: Path, *args: str) -> object:
    runner = CliRunner()
    return runner.invoke(new_command, ["myapp", *args], catch_exceptions=False)


# ---------------------------------------------------------------------------
# Base --starter flag wiring
# ---------------------------------------------------------------------------

class TestStarterFlag:
    def test_invalid_starter_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(new_command, ["myapp", "--starter=bogus"])
        assert result.exit_code != 0

    def test_no_starter_creates_base_skeleton(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _new(tmp_path)
        assert result.exit_code == 0
        assert (tmp_path / "myapp" / "bootstrap" / "app.py").exists()
        assert "README.md" not in [f.name for f in (tmp_path / "myapp").iterdir()]

    def test_starter_message_shown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _new(tmp_path, "--starter=blog")
        assert result.exit_code == 0, result.output
        assert "blog" in result.output
        assert "README.md" in result.output


# ---------------------------------------------------------------------------
# Blog starter
# ---------------------------------------------------------------------------

class TestBlogStarter:
    def _apply(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.chdir(tmp_path)
        result = _new(tmp_path, "--starter=blog")
        assert result.exit_code == 0, result.output
        return tmp_path / "myapp"

    def test_models_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        assert (app / "app" / "models" / "post.py").exists()
        assert (app / "app" / "models" / "category.py").exists()
        assert (app / "app" / "models" / "tag.py").exists()

    def test_controller_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        ctrl = app / "app" / "controllers" / "post_controller.py"
        assert ctrl.exists()
        content = ctrl.read_text()
        assert "class PostController" in content
        assert "def index" in content
        assert "def store" in content
        assert "def destroy" in content

    def test_migrations_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        mig_dir = app / "database" / "migrations"
        names = [f.name for f in mig_dir.iterdir()]
        assert any("categories" in n for n in names)
        assert any("posts" in n for n in names)
        assert any("tags" in n for n in names)
        assert any("post_tag" in n for n in names)

    def test_admin_resources_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        assert (app / "app" / "admin" / "post_resource.py").exists()
        assert (app / "app" / "admin" / "category_resource.py").exists()

    def test_views_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        views = app / "resources" / "views" / "posts"
        assert (views / "index.html").exists()
        assert (views / "show.html").exists()
        assert (views / "create.html").exists()
        assert (views / "edit.html").exists()

    def test_factories_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        factories = app / "database" / "factories"
        assert (factories / "post_factory.py").exists()
        assert (factories / "category_factory.py").exists()

    def test_routes_include_posts(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        routes = (app / "routes" / "web.py").read_text()
        assert "PostController" in routes
        assert "posts.index" in routes

    def test_admin_routes_include_post_resource(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        admin_routes = (app / "routes" / "admin.py").read_text()
        assert "PostResource" in admin_routes
        assert "CategoryResource" in admin_routes

    def test_readme_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        readme = app / "README.md"
        assert readme.exists()
        content = readme.read_text()
        assert "Blog Starter" in content
        assert "hunt migrate" in content

    def test_layout_has_blog_nav(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        layout = (app / "resources" / "views" / "layout.html").read_text()
        assert "/posts" in layout


# ---------------------------------------------------------------------------
# API starter
# ---------------------------------------------------------------------------

class TestApiStarter:
    def _apply(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.chdir(tmp_path)
        result = _new(tmp_path, "--starter=api")
        assert result.exit_code == 0, result.output
        return tmp_path / "myapp"

    def test_versioned_controller_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        ctrl = app / "app" / "controllers" / "api" / "v1" / "user_controller.py"
        assert ctrl.exists()
        content = ctrl.read_text()
        assert "class UserController" in content
        assert "def index" in content
        assert "def store" in content

    def test_user_resource_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        resource = app / "app" / "resources" / "user_resource.py"
        assert resource.exists()
        content = resource.read_text()
        assert "class UserResource" in content
        assert "def to_array" in content
        assert "def collection" in content

    def test_api_auth_middleware_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        mw = app / "app" / "middleware" / "api_auth.py"
        assert mw.exists()
        assert "Bearer" in mw.read_text()

    def test_rate_limit_middleware_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        mw = app / "app" / "middleware" / "api_rate_limit.py"
        assert mw.exists()
        assert "ThrottleRequests" in mw.read_text()

    def test_api_routes_versioned(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        routes = (app / "routes" / "api.py").read_text()
        assert "/api/v1" in routes
        assert "api.v1.users.index" in routes

    def test_api_routes_has_openapi_hints(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        routes = (app / "routes" / "api.py").read_text()
        assert "OpenAPI" in routes or "GET" in routes

    def test_readme_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        readme = app / "README.md"
        assert readme.exists()
        content = readme.read_text()
        assert "API Starter" in content
        assert "/api/v1/users" in content


# ---------------------------------------------------------------------------
# SaaS starter
# ---------------------------------------------------------------------------

class TestSaasStarter:
    def _apply(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.chdir(tmp_path)
        result = _new(tmp_path, "--starter=saas")
        assert result.exit_code == 0, result.output
        return tmp_path / "myapp"

    def test_team_model_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        model = app / "app" / "models" / "team.py"
        assert model.exists()
        content = model.read_text()
        assert "class Team" in content
        assert "plan" in content

    def test_membership_model_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        model = app / "app" / "models" / "membership.py"
        assert model.exists()
        assert "class Membership" in model.read_text()

    def test_billing_controller_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        ctrl = app / "app" / "controllers" / "billing_controller.py"
        assert ctrl.exists()
        content = ctrl.read_text()
        assert "class BillingController" in content
        assert "def update_plan" in content
        assert "def webhook" in content

    def test_tenant_middleware_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        mw = app / "app" / "middleware" / "tenant.py"
        assert mw.exists()
        assert "TenantMiddleware" in mw.read_text()
        assert "subdomain" in mw.read_text()

    def test_team_admin_resource_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        assert (app / "app" / "admin" / "team_resource.py").exists()

    def test_migrations_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        names = [f.name for f in (app / "database" / "migrations").iterdir()]
        assert any("teams" in n for n in names)
        assert any("memberships" in n for n in names)

    def test_teams_migration_has_plan_column(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        mig = next(
            f for f in (app / "database" / "migrations").iterdir()
            if "teams" in f.name
        )
        assert "plan" in mig.read_text()
        assert "stripe_customer_id" in mig.read_text()

    def test_billing_view_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        billing = app / "resources" / "views" / "billing" / "show.html"
        assert billing.exists()
        assert "Plan" in billing.read_text()

    def test_admin_routes_have_metrics(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        routes = (app / "routes" / "admin.py").read_text()
        assert "TeamResource" in routes
        assert "Total Teams" in routes

    def test_readme_created(self, tmp_path, monkeypatch):
        app = self._apply(tmp_path, monkeypatch)
        readme = app / "README.md"
        assert readme.exists()
        content = readme.read_text()
        assert "SaaS Starter" in content
        assert "TenantMiddleware" in content
        assert "Stripe" in content
