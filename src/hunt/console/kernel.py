from __future__ import annotations

import click

from hunt.console.commands.serve import serve_command
from hunt.console.commands.migrate import migrate_group, migrate_run, migrate_rollback, migrate_fresh, migrate_status
from hunt.console.commands.tinker import tinker_command
from hunt.console.commands.route_list import route_list_command
from hunt.console.commands.new import new_command
from hunt.console.commands.key_generate import key_generate_command
from hunt.console.commands.cache import cache_group
from hunt.console.commands.queue_work import queue_work_command
from hunt.console.commands.db.seed import db_seed_command

from hunt.console.commands.make.model import make_model_command
from hunt.console.commands.make.controller import make_controller_command
from hunt.console.commands.make.migration import make_migration_command
from hunt.console.commands.make.middleware import make_middleware_command
from hunt.console.commands.make.request import make_request_command
from hunt.console.commands.make.event import make_event_command
from hunt.console.commands.make.listener import make_listener_command
from hunt.console.commands.make.seeder import make_seeder_command
from hunt.console.commands.make.factory import make_factory_command
from hunt.console.commands.make.job import make_job_command
from hunt.admin.console.make_admin_resource import make_admin_resource_command


@click.group()
@click.version_option(prog_name="hunt")
def cli() -> None:
    """hunt — A Python web framework."""


# Top-level
cli.add_command(serve_command, name="serve")
cli.add_command(tinker_command, name="tinker")
cli.add_command(route_list_command, name="route:list")
cli.add_command(new_command, name="new")
cli.add_command(key_generate_command, name="key:generate")
cli.add_command(queue_work_command, name="queue:work")
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
cli.add_command(make_admin_resource_command, name="make:admin-resource")
