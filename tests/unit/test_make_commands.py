"""Tests for all hunt make:* CLI commands."""
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from hunt.console.commands.make.controller import make_controller_command
from hunt.console.commands.make.event import make_event_command
from hunt.console.commands.make.factory import make_factory_command
from hunt.console.commands.make.job import make_job_command
from hunt.console.commands.make.listener import make_listener_command
from hunt.console.commands.make.middleware import make_middleware_command
from hunt.console.commands.make.migration import make_migration_command
from hunt.console.commands.make.model import make_model_command
from hunt.console.commands.make.request import make_request_command
from hunt.console.commands.make.seeder import make_seeder_command


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# make:model
# ---------------------------------------------------------------------------

class TestMakeModel:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_model_command, ["Post"])
        assert result.exit_code == 0
        assert (project / "app" / "models" / "post.py").exists()

    def test_class_name_pascal(self, project):
        CliRunner().invoke(make_model_command, ["blog_post"])
        source = (project / "app" / "models" / "blog_post.py").read_text()
        assert "class BlogPost" in source

    def test_table_name_plural_snake(self, project):
        CliRunner().invoke(make_model_command, ["Post"])
        source = (project / "app" / "models" / "post.py").read_text()
        assert 'table = "posts"' in source

    def test_migration_flag_creates_migration(self, project):
        (project / "database" / "migrations").mkdir(parents=True)
        CliRunner().invoke(make_model_command, ["Post", "-m"])
        migrations = list((project / "database" / "migrations").iterdir())
        assert len(migrations) == 1
        assert "post" in migrations[0].name

    def test_controller_flag_creates_controller(self, project):
        CliRunner().invoke(make_model_command, ["Post", "-c"])
        assert (project / "app" / "controllers" / "post_controller.py").exists()


# ---------------------------------------------------------------------------
# make:controller
# ---------------------------------------------------------------------------

class TestMakeController:
    def test_plain_controller(self, project):
        result = CliRunner().invoke(make_controller_command, ["post_controller"])
        assert result.exit_code == 0
        source = (project / "app" / "controllers" / "post_controller.py").read_text()
        assert "class PostController" in source
        assert "def index" in source
        assert "def store" not in source

    def test_resource_controller(self, project):
        CliRunner().invoke(make_controller_command, ["post_controller", "--resource"])
        source = (project / "app" / "controllers" / "post_controller.py").read_text()
        for method in ("index", "create", "store", "show", "edit", "update", "destroy"):
            assert f"def {method}" in source

    def test_api_controller(self, project):
        CliRunner().invoke(make_controller_command, ["post_controller", "--api"])
        source = (project / "app" / "controllers" / "post_controller.py").read_text()
        assert "def create" not in source
        assert "def edit" not in source
        assert "JsonResponse" in source

    def test_output_path(self, project):
        CliRunner().invoke(make_controller_command, ["user_controller"])
        assert (project / "app" / "controllers" / "user_controller.py").exists()


# ---------------------------------------------------------------------------
# make:migration
# ---------------------------------------------------------------------------

class TestMakeMigration:
    def _only_migration(self, project) -> Path:
        files = list((project / "database" / "migrations").iterdir())
        assert len(files) == 1
        return files[0]

    def test_creates_file_with_timestamp(self, project):
        result = CliRunner().invoke(make_migration_command, ["add_slug_to_posts"])
        assert result.exit_code == 0
        files = list((project / "database" / "migrations").iterdir())
        assert len(files) == 1
        assert re.match(r"\d{4}_\d{2}_\d{2}_\d{6}_add_slug_to_posts\.py", files[0].name)

    def test_blank_stub_by_default(self, project):
        CliRunner().invoke(make_migration_command, ["add_slug_to_posts"])
        source = self._only_migration(project).read_text()
        assert "def up" in source
        assert "def down" in source
        assert "Schema.create" not in source

    def test_create_stub_with_create_option(self, project):
        CliRunner().invoke(make_migration_command, ["CreatePostsTable", "--create", "posts"])
        source = self._only_migration(project).read_text()
        assert 'Schema.create("posts"' in source
        assert 'Schema.drop_if_exists("posts")' in source

    def test_update_stub_with_table_option(self, project):
        CliRunner().invoke(make_migration_command, ["AddSlugToPosts", "--table", "posts"])
        source = self._only_migration(project).read_text()
        assert 'Schema.table("posts"' in source

    def test_infers_create_stub_from_name(self, project):
        CliRunner().invoke(make_migration_command, ["create_orders_table"])
        source = self._only_migration(project).read_text()
        assert 'Schema.create("orders"' in source

    def test_class_name_pascal(self, project):
        CliRunner().invoke(make_migration_command, ["create_posts_table"])
        source = self._only_migration(project).read_text()
        assert "class CreatePostsTable" in source


# ---------------------------------------------------------------------------
# make:middleware
# ---------------------------------------------------------------------------

class TestMakeMiddleware:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_middleware_command, ["AuthMiddleware"])
        assert result.exit_code == 0
        assert (project / "app" / "middleware" / "auth_middleware.py").exists()

    def test_class_name(self, project):
        CliRunner().invoke(make_middleware_command, ["rate_limit"])
        source = (project / "app" / "middleware" / "rate_limit.py").read_text()
        assert "class RateLimit(Middleware)" in source

    def test_stub_has_handle_method(self, project):
        CliRunner().invoke(make_middleware_command, ["Foo"])
        source = (project / "app" / "middleware" / "foo.py").read_text()
        assert "async def handle" in source


# ---------------------------------------------------------------------------
# make:request
# ---------------------------------------------------------------------------

class TestMakeRequest:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_request_command, ["StorePost"])
        assert result.exit_code == 0
        assert (project / "app" / "requests" / "store_post_request.py").exists()

    def test_appends_request_suffix(self, project):
        CliRunner().invoke(make_request_command, ["StorePost"])
        source = (project / "app" / "requests" / "store_post_request.py").read_text()
        assert "class StorePostRequest" in source

    def test_does_not_duplicate_request_suffix(self, project):
        CliRunner().invoke(make_request_command, ["StorePostRequest"])
        source = (project / "app" / "requests" / "store_post_request.py").read_text()
        assert "class StorePostRequest" in source
        assert "RequestRequest" not in source

    def test_no_overwrite(self, project):
        (project / "app" / "requests").mkdir(parents=True)
        (project / "app" / "requests" / "store_post_request.py").write_text("# original\n")
        CliRunner().invoke(make_request_command, ["StorePost"])
        assert (project / "app" / "requests" / "store_post_request.py").read_text() == "# original\n"

    def test_stub_has_rules_method(self, project):
        CliRunner().invoke(make_request_command, ["StorePost"])
        source = (project / "app" / "requests" / "store_post_request.py").read_text()
        assert "def rules" in source
        assert "def authorize" in source


# ---------------------------------------------------------------------------
# make:event
# ---------------------------------------------------------------------------

class TestMakeEvent:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_event_command, ["UserRegistered"])
        assert result.exit_code == 0
        assert (project / "app" / "events" / "UserRegistered.py").exists()

    def test_class_name(self, project):
        CliRunner().invoke(make_event_command, ["UserRegistered"])
        source = (project / "app" / "events" / "UserRegistered.py").read_text()
        assert "class UserRegistered" in source

    def test_no_overwrite(self, project):
        (project / "app" / "events").mkdir(parents=True)
        (project / "app" / "events" / "OrderPlaced.py").write_text("# original\n")
        result = CliRunner().invoke(make_event_command, ["OrderPlaced"])
        assert result.exit_code == 0
        assert "Already exists" in result.output
        assert (project / "app" / "events" / "OrderPlaced.py").read_text() == "# original\n"


# ---------------------------------------------------------------------------
# make:listener
# ---------------------------------------------------------------------------

class TestMakeListener:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_listener_command, ["SendWelcomeEmail"])
        assert result.exit_code == 0
        assert (project / "app" / "listeners" / "SendWelcomeEmail.py").exists()

    def test_class_name(self, project):
        CliRunner().invoke(make_listener_command, ["SendWelcomeEmail"])
        source = (project / "app" / "listeners" / "SendWelcomeEmail.py").read_text()
        assert "class SendWelcomeEmail" in source
        assert "def handle" in source

    def test_no_overwrite(self, project):
        (project / "app" / "listeners").mkdir(parents=True)
        (project / "app" / "listeners" / "Notify.py").write_text("# original\n")
        result = CliRunner().invoke(make_listener_command, ["Notify"])
        assert "Already exists" in result.output
        assert (project / "app" / "listeners" / "Notify.py").read_text() == "# original\n"

    def test_event_option_accepted(self, project):
        result = CliRunner().invoke(make_listener_command, ["Notify", "--event", "UserRegistered"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# make:job
# ---------------------------------------------------------------------------

class TestMakeJob:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_job_command, ["ProcessPayment"])
        assert result.exit_code == 0
        assert (project / "app" / "jobs" / "ProcessPayment.py").exists()

    def test_stub_contents(self, project):
        CliRunner().invoke(make_job_command, ["ProcessPayment"])
        source = (project / "app" / "jobs" / "ProcessPayment.py").read_text()
        assert "class ProcessPayment(Job)" in source
        assert "def handle" in source
        assert "def failed" in source

    def test_no_overwrite(self, project):
        (project / "app" / "jobs").mkdir(parents=True)
        (project / "app" / "jobs" / "SendEmail.py").write_text("# original\n")
        result = CliRunner().invoke(make_job_command, ["SendEmail"])
        assert "Already exists" in result.output
        assert (project / "app" / "jobs" / "SendEmail.py").read_text() == "# original\n"


# ---------------------------------------------------------------------------
# make:factory
# ---------------------------------------------------------------------------

class TestMakeFactory:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_factory_command, ["Post"])
        assert result.exit_code == 0
        assert (project / "database" / "factories" / "PostFactory.py").exists()

    def test_appends_factory_suffix(self, project):
        CliRunner().invoke(make_factory_command, ["Post"])
        source = (project / "database" / "factories" / "PostFactory.py").read_text()
        assert "class PostFactory(Factory)" in source

    def test_does_not_duplicate_factory_suffix(self, project):
        CliRunner().invoke(make_factory_command, ["PostFactory"])
        assert (project / "database" / "factories" / "PostFactory.py").exists()

    def test_model_option(self, project):
        CliRunner().invoke(make_factory_command, ["Post", "--model", "Article"])
        source = (project / "database" / "factories" / "PostFactory.py").read_text()
        assert "Article" in source

    def test_model_derived_from_name(self, project):
        CliRunner().invoke(make_factory_command, ["Post"])
        source = (project / "database" / "factories" / "PostFactory.py").read_text()
        assert "Post" in source

    def test_no_overwrite(self, project):
        (project / "database" / "factories").mkdir(parents=True)
        (project / "database" / "factories" / "UserFactory.py").write_text("# original\n")
        result = CliRunner().invoke(make_factory_command, ["User"])
        assert "Already exists" in result.output
        assert (project / "database" / "factories" / "UserFactory.py").read_text() == "# original\n"


# ---------------------------------------------------------------------------
# make:seeder
# ---------------------------------------------------------------------------

class TestMakeSeeder:
    def test_creates_file(self, project):
        result = CliRunner().invoke(make_seeder_command, ["Post"])
        assert result.exit_code == 0
        assert (project / "database" / "seeders" / "PostSeeder.py").exists()

    def test_appends_seeder_suffix(self, project):
        CliRunner().invoke(make_seeder_command, ["Post"])
        source = (project / "database" / "seeders" / "PostSeeder.py").read_text()
        assert "class PostSeeder(Seeder)" in source

    def test_does_not_duplicate_seeder_suffix(self, project):
        CliRunner().invoke(make_seeder_command, ["PostSeeder"])
        assert (project / "database" / "seeders" / "PostSeeder.py").exists()

    def test_stub_has_run_method(self, project):
        CliRunner().invoke(make_seeder_command, ["Post"])
        source = (project / "database" / "seeders" / "PostSeeder.py").read_text()
        assert "def run" in source

    def test_no_overwrite(self, project):
        (project / "database" / "seeders").mkdir(parents=True)
        (project / "database" / "seeders" / "UserSeeder.py").write_text("# original\n")
        result = CliRunner().invoke(make_seeder_command, ["User"])
        assert "Already exists" in result.output
        assert (project / "database" / "seeders" / "UserSeeder.py").read_text() == "# original\n"
