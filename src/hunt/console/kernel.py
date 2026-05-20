from __future__ import annotations

import click

from hunt import __version__
from hunt.admin.console.make_admin_resource import make_admin_resource_command
from hunt.admin.console.publish import admin_publish_command
from hunt.console.commands.cache import cache_group
from hunt.console.commands.config_cache import config_cache_command, config_clear_command
from hunt.console.commands.db.seed import db_seed_command
from hunt.console.commands.job_list import job_list_command
from hunt.console.commands.job_run import job_run_command
from hunt.console.commands.key_generate import key_generate_command
from hunt.console.commands.make.command import make_command_command
from hunt.console.commands.make.controller import make_controller_command
from hunt.console.commands.make.event import make_event_command
from hunt.console.commands.make.factory import make_factory_command
from hunt.console.commands.make.job import make_job_command
from hunt.console.commands.make.listener import make_listener_command
from hunt.console.commands.make.mail import make_mail_command
from hunt.console.commands.make.middleware import make_middleware_command
from hunt.console.commands.make.migration import make_migration_command
from hunt.console.commands.make.model import make_model_command
from hunt.console.commands.make.notification import make_notification_command
from hunt.console.commands.make.observer import make_observer_command
from hunt.console.commands.make.policy import make_policy_command
from hunt.console.commands.make.request import make_request_command
from hunt.console.commands.make.resource import make_resource_command
from hunt.console.commands.make.rule import make_rule_command
from hunt.console.commands.make.seeder import make_seeder_command
from hunt.console.commands.make.two_factor import make_two_factor_command
from hunt.console.commands.migrate import migrate_fresh, migrate_group, migrate_rollback, migrate_run, migrate_status
from hunt.console.commands.new import new_command
from hunt.console.commands.queue_failed import (
    queue_failed_command,
    queue_flush_command,
    queue_retry_command,
)
from hunt.console.commands.queue_table import queue_table_command
from hunt.console.commands.queue_work import queue_work_command
from hunt.console.commands.route_list import route_list_command
from hunt.console.commands.schedule_list import schedule_list_command
from hunt.console.commands.schedule_run import schedule_run_command
from hunt.console.commands.serve import serve_command
from hunt.console.commands.storage_link import storage_link_command
from hunt.console.commands.tinker import tinker_command
from hunt.console.commands.upgrade import upgrade_command
from hunt.console.commands.view_cache import view_cache_command, view_clear_command


def _load_app_commands(cli: click.Group) -> None:
    """Load app/console/kernel.py and call register(cli) if it exists."""
    import importlib.util
    import sys
    from pathlib import Path

    kernel_file = Path.cwd() / "app" / "console" / "kernel.py"
    if not kernel_file.exists():
        return
    sys.path.insert(0, str(Path.cwd()))
    try:
        spec = importlib.util.spec_from_file_location("app.console.kernel", kernel_file)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if callable(getattr(mod, "register", None)):
            mod.register(cli)
    except Exception as exc:
        click.echo(f"  Warning: could not load app/console/kernel.py — {exc}", err=True)


@click.group()
@click.version_option(version=__version__, prog_name="hunt")
def cli() -> None:
    """hunt — A Python web framework."""


# Top-level
cli.add_command(serve_command, name="serve")
cli.add_command(tinker_command, name="tinker")
cli.add_command(route_list_command, name="route:list")
cli.add_command(new_command, name="new")
cli.add_command(upgrade_command, name="upgrade")
cli.add_command(key_generate_command, name="key:generate")
cli.add_command(queue_work_command, name="queue:work")
cli.add_command(queue_failed_command, name="queue:failed")
cli.add_command(queue_retry_command, name="queue:retry")
cli.add_command(queue_flush_command, name="queue:flush")
cli.add_command(queue_table_command, name="queue:table")
cli.add_command(job_list_command, name="job:list")
cli.add_command(job_run_command, name="job:run")
cli.add_command(db_seed_command, name="db:seed")

# Cache group
cli.add_command(cache_group, name="cache")
cli.add_command(cache_group.commands["clear"], name="cache:clear")
cli.add_command(cache_group.commands["forget"], name="cache:forget")

# Migrate subcommands
cli.add_command(migrate_group, name="migrate")
cli.add_command(migrate_run, name="migrate:run")
cli.add_command(migrate_rollback, name="migrate:rollback")
cli.add_command(migrate_fresh, name="migrate:fresh")
cli.add_command(migrate_status, name="migrate:status")

# make:*
cli.add_command(make_model_command, name="make:model")
cli.add_command(make_controller_command, name="make:controller")
cli.add_command(make_migration_command, name="make:migration")
cli.add_command(make_middleware_command, name="make:middleware")
cli.add_command(make_request_command, name="make:request")
cli.add_command(make_event_command, name="make:event")
cli.add_command(make_listener_command, name="make:listener")
cli.add_command(make_seeder_command, name="make:seeder")
cli.add_command(make_factory_command, name="make:factory")
cli.add_command(make_job_command, name="make:job")
cli.add_command(make_mail_command, name="make:mail")
cli.add_command(make_notification_command, name="make:notification")
cli.add_command(make_admin_resource_command, name="make:admin-resource")
cli.add_command(admin_publish_command, name="admin:publish")
cli.add_command(make_command_command, name="make:command")
cli.add_command(make_policy_command, name="make:policy")
cli.add_command(make_observer_command, name="make:observer")
cli.add_command(make_rule_command, name="make:rule")
cli.add_command(make_resource_command, name="make:resource")
cli.add_command(make_two_factor_command, name="make:2fa-controllers")

# Config / view cache commands
cli.add_command(config_cache_command, name="config:cache")
cli.add_command(config_clear_command, name="config:clear")
cli.add_command(view_cache_command, name="view:cache")
cli.add_command(view_clear_command, name="view:clear")

# Schedule commands
cli.add_command(schedule_run_command, name="schedule:run")
cli.add_command(schedule_list_command, name="schedule:list")

# Storage commands
cli.add_command(storage_link_command, name="storage:link")

# Load app-level commands from app/console/kernel.py if present
_load_app_commands(cli)
