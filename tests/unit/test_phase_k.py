"""Phase K — CLI Generators tests."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def runner(tmp_path: Path) -> CliRunner:
    return CliRunner()


def invoke(command, args, cwd: Path):
    r = CliRunner()
    with r.isolated_filesystem(temp_dir=cwd):
        return r.invoke(command, args, catch_exceptions=False)


def invoke_cwd(command, args, cwd: Path):
    """Invoke command with cwd set to `cwd` (create tmp dir structure)."""
    from click.testing import CliRunner
    r = CliRunner()
    result = r.invoke(command, args, catch_exceptions=False)
    return result


# ===========================================================================
# 1. make:command
# ===========================================================================

class TestMakeCommand:
    def test_creates_file(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(make_command_command, ["SendEmails"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created Command" in result.output

    def test_file_path_is_snake_case(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_command_command, ["SendEmails"], catch_exceptions=False)
            out = Path(td) / "app" / "console" / "commands" / "send_emails.py"
            assert out.exists()

    def test_generated_content_has_click_command(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_command_command, ["SendEmails"], catch_exceptions=False)
            content = (Path(td) / "app" / "console" / "commands" / "send_emails.py").read_text()
        assert "@click.command(" in content
        assert "import click" in content

    def test_custom_command_name_option(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_command_command, ["SendEmails", "--command", "emails:send"], catch_exceptions=False)
            content = (Path(td) / "app" / "console" / "commands" / "send_emails.py").read_text()
        assert '"emails:send"' in content

    def test_refuses_to_overwrite_existing_file(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            # Create first time
            r.invoke(make_command_command, ["MyCmd"], catch_exceptions=False)
            # Attempt to overwrite
            result = r.invoke(make_command_command, ["MyCmd"], catch_exceptions=False)
        assert "Already exists" in result.output

    def test_pascal_class_name_in_content(self, tmp_path):
        from hunt.console.commands.make.command import make_command_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_command_command, ["sync_orders"], catch_exceptions=False)
            content = (Path(td) / "app" / "console" / "commands" / "sync_orders.py").read_text()
        assert "SyncOrders" in content


# ===========================================================================
# 2. make:policy
# ===========================================================================

class TestMakePolicy:
    def test_creates_file(self, tmp_path):
        from hunt.console.commands.make.policy import make_policy_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(make_policy_command, ["PostPolicy"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created Policy" in result.output

    def test_file_in_policies_dir(self, tmp_path):
        from hunt.console.commands.make.policy import make_policy_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_policy_command, ["PostPolicy"], catch_exceptions=False)
            out = Path(td) / "app" / "policies" / "post_policy.py"
            assert out.exists()

    def test_generated_class_methods(self, tmp_path):
        from hunt.console.commands.make.policy import make_policy_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_policy_command, ["PostPolicy"], catch_exceptions=False)
            content = (Path(td) / "app" / "policies" / "post_policy.py").read_text()
        assert "def view_any" in content
        assert "def view" in content
        assert "def create" in content
        assert "def update" in content
        assert "def delete" in content

    def test_model_option_adds_import(self, tmp_path):
        from hunt.console.commands.make.policy import make_policy_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_policy_command, ["PostPolicy", "--model", "Post"], catch_exceptions=False)
            content = (Path(td) / "app" / "policies" / "post_policy.py").read_text()
        assert "from app.models.post import Post" in content

    def test_refuses_to_overwrite(self, tmp_path):
        from hunt.console.commands.make.policy import make_policy_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            r.invoke(make_policy_command, ["PostPolicy"], catch_exceptions=False)
            result = r.invoke(make_policy_command, ["PostPolicy"], catch_exceptions=False)
        assert "Already exists" in result.output


# ===========================================================================
# 3. make:observer
# ===========================================================================

class TestMakeObserver:
    def test_creates_file(self, tmp_path):
        from hunt.console.commands.make.observer import make_observer_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(make_observer_command, ["UserObserver"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created Observer" in result.output

    def test_file_in_observers_dir(self, tmp_path):
        from hunt.console.commands.make.observer import make_observer_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_observer_command, ["UserObserver"], catch_exceptions=False)
            assert (Path(td) / "app" / "observers" / "user_observer.py").exists()

    def test_generated_lifecycle_methods(self, tmp_path):
        from hunt.console.commands.make.observer import make_observer_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_observer_command, ["PostObserver"], catch_exceptions=False)
            content = (Path(td) / "app" / "observers" / "post_observer.py").read_text()
        for method in ("creating", "created", "updating", "updated", "saving", "saved", "deleting", "deleted"):
            assert f"def {method}" in content

    def test_model_option_adds_import(self, tmp_path):
        from hunt.console.commands.make.observer import make_observer_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_observer_command, ["PostObserver", "--model", "Post"], catch_exceptions=False)
            content = (Path(td) / "app" / "observers" / "post_observer.py").read_text()
        assert "from app.models.post import Post" in content

    def test_refuses_to_overwrite(self, tmp_path):
        from hunt.console.commands.make.observer import make_observer_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            r.invoke(make_observer_command, ["UserObserver"], catch_exceptions=False)
            result = r.invoke(make_observer_command, ["UserObserver"], catch_exceptions=False)
        assert "Already exists" in result.output


# ===========================================================================
# 4. make:rule
# ===========================================================================

class TestMakeRule:
    def test_creates_file(self, tmp_path):
        from hunt.console.commands.make.rule import make_rule_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(make_rule_command, ["MustBeAdult"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created Rule" in result.output

    def test_file_in_rules_dir(self, tmp_path):
        from hunt.console.commands.make.rule import make_rule_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_rule_command, ["MustBeAdult"], catch_exceptions=False)
            assert (Path(td) / "app" / "rules" / "must_be_adult.py").exists()

    def test_generated_passes_and_message(self, tmp_path):
        from hunt.console.commands.make.rule import make_rule_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_rule_command, ["MustBeAdult"], catch_exceptions=False)
            content = (Path(td) / "app" / "rules" / "must_be_adult.py").read_text()
        assert "def passes" in content
        assert "def message" in content
        assert "MustBeAdult" in content

    def test_refuses_to_overwrite(self, tmp_path):
        from hunt.console.commands.make.rule import make_rule_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            r.invoke(make_rule_command, ["MustBeAdult"], catch_exceptions=False)
            result = r.invoke(make_rule_command, ["MustBeAdult"], catch_exceptions=False)
        assert "Already exists" in result.output

    def test_class_name_is_pascal_case(self, tmp_path):
        from hunt.console.commands.make.rule import make_rule_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_rule_command, ["must_be_adult"], catch_exceptions=False)
            content = (Path(td) / "app" / "rules" / "must_be_adult.py").read_text()
        assert "class MustBeAdult" in content


# ===========================================================================
# 5. make:resource
# ===========================================================================

class TestMakeResource:
    def test_creates_file(self, tmp_path):
        from hunt.console.commands.make.resource import make_resource_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(make_resource_command, ["PostResource"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created Resource" in result.output

    def test_file_in_resources_dir(self, tmp_path):
        from hunt.console.commands.make.resource import make_resource_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_resource_command, ["PostResource"], catch_exceptions=False)
            assert (Path(td) / "app" / "resources" / "post_resource.py").exists()

    def test_generated_has_to_array_and_collection(self, tmp_path):
        from hunt.console.commands.make.resource import make_resource_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_resource_command, ["PostResource"], catch_exceptions=False)
            content = (Path(td) / "app" / "resources" / "post_resource.py").read_text()
        assert "def to_array" in content
        assert "def collection" in content
        assert "PostResource" in content

    def test_refuses_to_overwrite(self, tmp_path):
        from hunt.console.commands.make.resource import make_resource_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            r.invoke(make_resource_command, ["PostResource"], catch_exceptions=False)
            result = r.invoke(make_resource_command, ["PostResource"], catch_exceptions=False)
        assert "Already exists" in result.output

    def test_to_array_returns_dict(self, tmp_path):
        from hunt.console.commands.make.resource import make_resource_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_resource_command, ["UserResource"], catch_exceptions=False)
            content = (Path(td) / "app" / "resources" / "user_resource.py").read_text()
        assert "return {" in content or 'return {' in content


# ===========================================================================
# 6. config:cache / config:clear
# ===========================================================================

class TestConfigCache:
    def _write_config(self, td: str, name: str, content: str) -> None:
        cfg_dir = Path(td) / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / f"{name}.py").write_text(content)

    def test_creates_config_json(self, tmp_path):
        from hunt.console.commands.config_cache import config_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_config(td, "app", 'app = {"name": "MyApp", "debug": False}')
            result = r.invoke(config_cache_command, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "cached" in result.output.lower()

    def test_cached_file_contains_config_values(self, tmp_path):
        from hunt.console.commands.config_cache import config_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_config(td, "app", 'app = {"name": "MyApp", "debug": False}')
            r.invoke(config_cache_command, [], catch_exceptions=False)
            cached = Path(td) / "storage" / "framework" / "config.json"
            assert cached.exists()
            data = json.loads(cached.read_text())
        assert data  # non-empty

    def test_error_when_no_config_dir(self, tmp_path):
        from hunt.console.commands.config_cache import config_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(config_cache_command, [], catch_exceptions=False)
        assert result.exit_code == 0  # click.echo(err=True) doesn't change exit code
        assert "not found" in result.output.lower() or "config" in result.output.lower()

    def test_config_clear_removes_file(self, tmp_path):
        from hunt.console.commands.config_cache import config_cache_command, config_clear_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_config(td, "app", 'app = {"name": "MyApp"}')
            r.invoke(config_cache_command, [], catch_exceptions=False)
            assert (Path(td) / "storage" / "framework" / "config.json").exists()
            r.invoke(config_clear_command, [], catch_exceptions=False)
            assert not (Path(td) / "storage" / "framework" / "config.json").exists()

    def test_config_clear_when_already_empty(self, tmp_path):
        from hunt.console.commands.config_cache import config_clear_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(config_clear_command, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "already empty" in result.output.lower()

    def test_config_clear_outputs_confirmation(self, tmp_path):
        from hunt.console.commands.config_cache import config_cache_command, config_clear_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_config(td, "app", 'app = {"x": 1}')
            r.invoke(config_cache_command, [], catch_exceptions=False)
            result = r.invoke(config_clear_command, [], catch_exceptions=False)
        assert "cleared" in result.output.lower()


# ===========================================================================
# 7. view:cache / view:clear
# ===========================================================================

class TestViewCache:
    def _write_view(self, td: str, name: str, content: str) -> None:
        views_dir = Path(td) / "resources" / "views"
        views_dir.mkdir(parents=True, exist_ok=True)
        (views_dir / name).write_text(content)

    def test_compiles_templates(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_view(td, "welcome.html", "<h1>Hello</h1>")
            result = r.invoke(view_cache_command, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "compiled" in result.output.lower() or "template" in result.output.lower()

    def test_creates_cached_files(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_view(td, "welcome.html", "<h1>Hello</h1>")
            r.invoke(view_cache_command, [], catch_exceptions=False)
            cache_dir = Path(td) / "storage" / "framework" / "views"
            assert cache_dir.exists()
            assert any(cache_dir.iterdir())

    def test_count_matches_template_count(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_view(td, "home.html", "<p>Home</p>")
            self._write_view(td, "about.html", "<p>About</p>")
            result = r.invoke(view_cache_command, [], catch_exceptions=False)
        assert "2 template" in result.output

    def test_error_when_no_views_dir(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(view_cache_command, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "views" in result.output.lower()

    def test_view_clear_removes_cached_files(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command, view_clear_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_view(td, "home.html", "<p>Hello</p>")
            r.invoke(view_cache_command, [], catch_exceptions=False)
            cache_dir = Path(td) / "storage" / "framework" / "views"
            assert any(cache_dir.iterdir())
            r.invoke(view_clear_command, [], catch_exceptions=False)
            assert not any(cache_dir.iterdir())

    def test_view_clear_outputs_confirmation(self, tmp_path):
        from hunt.console.commands.view_cache import view_clear_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path):
            result = r.invoke(view_clear_command, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "cleared" in result.output.lower()

    def test_blade_directives_processed(self, tmp_path):
        from hunt.console.commands.view_cache import view_cache_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            self._write_view(td, "page.html", "@if(True)\n<p>Yes</p>\n@endif\n")
            r.invoke(view_cache_command, [], catch_exceptions=False)
            cache_dir = Path(td) / "storage" / "framework" / "views"
            cached_files = list(cache_dir.iterdir())
            assert cached_files
            cached_content = cached_files[0].read_text()
        # Preprocessed Jinja2 syntax (not @if)
        assert "{% if" in cached_content


# ===========================================================================
# 8. Kernel registration
# ===========================================================================

class TestKernelRegistration:
    def test_make_command_registered(self):
        from hunt.console.kernel import cli
        assert "make:command" in cli.commands

    def test_make_policy_registered(self):
        from hunt.console.kernel import cli
        assert "make:policy" in cli.commands

    def test_make_observer_registered(self):
        from hunt.console.kernel import cli
        assert "make:observer" in cli.commands

    def test_make_rule_registered(self):
        from hunt.console.kernel import cli
        assert "make:rule" in cli.commands

    def test_make_resource_registered(self):
        from hunt.console.kernel import cli
        assert "make:resource" in cli.commands

    def test_config_cache_registered(self):
        from hunt.console.kernel import cli
        assert "config:cache" in cli.commands

    def test_config_clear_registered(self):
        from hunt.console.kernel import cli
        assert "config:clear" in cli.commands

    def test_view_cache_registered(self):
        from hunt.console.kernel import cli
        assert "view:cache" in cli.commands

    def test_view_clear_registered(self):
        from hunt.console.kernel import cli
        assert "view:clear" in cli.commands
