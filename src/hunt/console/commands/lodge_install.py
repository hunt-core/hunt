from __future__ import annotations

import re
import sys
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Available services
# ---------------------------------------------------------------------------

_SERVICES = ("pgsql", "mysql", "redis", "mailpit", "minio")

# ---------------------------------------------------------------------------
# Compose YAML builders
# ---------------------------------------------------------------------------

_SERVICE_PGSQL = """\
  pgsql:
    image: "postgres:16"
    ports:
      - "${DB_PORT:-5432}:5432"
    environment:
      POSTGRES_DB: "${DB_DATABASE:-hunt}"
      POSTGRES_USER: "${DB_USERNAME:-hunt}"
      POSTGRES_PASSWORD: "${DB_PASSWORD:-secret}"
    volumes:
      - "lodge-pgsql:/var/lib/postgresql/data"
    networks:
      - lodge
    healthcheck:
      test: ["CMD", "pg_isready", "-q", "-d", "${DB_DATABASE:-hunt}", "-U", "${DB_USERNAME:-hunt}"]
      retries: 3
      timeout: 5s
"""

_SERVICE_MYSQL = """\
  mysql:
    image: "mysql:8.0"
    ports:
      - "${DB_PORT:-3306}:3306"
    environment:
      MYSQL_ROOT_PASSWORD: "${DB_PASSWORD:-secret}"
      MYSQL_ROOT_HOST: "%"
      MYSQL_DATABASE: "${DB_DATABASE:-hunt}"
      MYSQL_USER: "${DB_USERNAME:-hunt}"
      MYSQL_PASSWORD: "${DB_PASSWORD:-secret}"
    volumes:
      - "lodge-mysql:/var/lib/mysql"
    networks:
      - lodge
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-p${DB_PASSWORD:-secret}"]
      retries: 3
      timeout: 5s
"""

_SERVICE_REDIS = """\
  redis:
    image: "redis:alpine"
    ports:
      - "${REDIS_PORT:-6379}:6379"
    volumes:
      - "lodge-redis:/data"
    networks:
      - lodge
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      retries: 3
      timeout: 5s
"""

_SERVICE_MAILPIT = """\
  mailpit:
    image: "axllent/mailpit:latest"
    ports:
      - "${MAIL_PORT:-1025}:1025"
      - "${MAILPIT_DASHBOARD_PORT:-8025}:8025"
    networks:
      - lodge
"""

_SERVICE_MINIO = """\
  minio:
    image: "minio/minio:latest"
    ports:
      - "${MINIO_PORT:-9000}:9000"
      - "${MINIO_CONSOLE_PORT:-9001}:9001"
    environment:
      MINIO_ROOT_USER: "${AWS_ACCESS_KEY_ID:-lodge}"
      MINIO_ROOT_PASSWORD: "${AWS_SECRET_ACCESS_KEY:-password}"
    volumes:
      - "lodge-minio:/data"
    networks:
      - lodge
    command: minio server /data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      retries: 3
      timeout: 5s
"""

_SERVICE_BLOCKS = {
    "pgsql": _SERVICE_PGSQL,
    "mysql": _SERVICE_MYSQL,
    "redis": _SERVICE_REDIS,
    "mailpit": _SERVICE_MAILPIT,
    "minio": _SERVICE_MINIO,
}

# Services that expose a healthcheck (others use service_started)
_HAS_HEALTHCHECK = {"pgsql", "mysql", "redis", "minio"}

# Per-service volumes
_SERVICE_VOLUMES = {
    "pgsql": "lodge-pgsql",
    "mysql": "lodge-mysql",
    "redis": "lodge-redis",
    "minio": "lodge-minio",
}


def _build_compose(app_name: str, python_version: str, services: list[str]) -> str:
    depends_lines = []
    for svc in services:
        condition = "service_healthy" if svc in _HAS_HEALTHCHECK else "service_started"
        depends_lines.append(f"      {svc}:\n        condition: {condition}")

    depends_block = ""
    if depends_lines:
        depends_block = "\n    depends_on:\n" + "\n".join(depends_lines)

    compose = f"""\
services:
  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
      args:
        PYTHON_VERSION: "{python_version}"
    image: "{app_name}/app"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${{APP_PORT:-8000}}:8000"
    env_file:
      - .env
    volumes:
      - ".:/app"
      - "/app/.venv"
    networks:
      - lodge{depends_block}

"""

    for svc in services:
        compose += _SERVICE_BLOCKS[svc] + "\n"

    compose += """\
networks:
  lodge:
    driver: bridge
"""

    volumes = [_SERVICE_VOLUMES[svc] for svc in services if svc in _SERVICE_VOLUMES]
    if volumes:
        compose += "\nvolumes:\n"
        for vol in volumes:
            compose += f"  {vol}:\n    driver: local\n"

    return compose


# ---------------------------------------------------------------------------
# Dockerfile (dev-optimised — bind-mount, non-root user, build tools)
# ---------------------------------------------------------------------------

_DOCKERFILE_DEV = """\
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

# Build essentials + libpq for asyncpg/psycopg2
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        git curl build-essential libpq-dev default-libmysqlclient-dev \\
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching host UID 1000
RUN groupadd --force -g 1000 hunt \\
    && useradd -ms /bin/bash --no-user-group -g 1000 -u 1000 hunt

# Layer-cached dependency install
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint
RUN chmod +x /usr/local/bin/docker-entrypoint \\
    && chown -R hunt:1000 /app

USER hunt

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint"]
CMD ["hunt", "serve", "--host", "0.0.0.0", "--port", "8000", "--reload"]
"""

_ENTRYPOINT = """\
#!/bin/sh
set -e
exec "$@"
"""

# ---------------------------------------------------------------------------
# lodge bash wrapper
# ---------------------------------------------------------------------------

_LODGE_SCRIPT = """\
#!/usr/bin/env bash
# lodge — hunt framework Docker Compose wrapper
# Usage: ./lodge <command> [options]

set -e

# ── Detect Docker Compose ────────────────────────────────────────────────────
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    echo "  Error: Docker Compose is not installed." >&2
    exit 1
fi

# ── Verify Docker is running ─────────────────────────────────────────────────
if ! docker info >/dev/null 2>&1; then
    echo "  Error: Docker is not running. Start Docker Desktop and try again." >&2
    exit 1
fi

# ── TTY detection (interactive terminal vs CI/pipe) ──────────────────────────
if [ -t 0 ]; then
    TTY="-it"
else
    TTY=""
fi

APP="app"

# ── No arguments → show status ───────────────────────────────────────────────
if [ $# -eq 0 ]; then
    $DC ps
    exit 0
fi

# ── Command dispatch ─────────────────────────────────────────────────────────
case "$1" in
    up)
        shift
        $DC up "$@"
        ;;
    down)
        shift
        $DC down "$@"
        ;;
    stop)
        shift
        $DC stop "$@"
        ;;
    restart)
        shift
        $DC restart "${@:-$APP}"
        ;;
    build)
        shift
        $DC build "$@"
        ;;
    ps|status)
        $DC ps
        ;;
    shell|bash)
        $DC exec $TTY $APP bash
        ;;
    python|py)
        shift
        $DC exec $TTY $APP python "$@"
        ;;
    hunt)
        shift
        $DC exec $TTY $APP hunt "$@"
        ;;
    test|pytest)
        shift
        $DC exec $TTY $APP pytest "$@"
        ;;
    pip)
        shift
        $DC exec $TTY $APP pip "$@"
        ;;
    logs)
        shift
        $DC logs --follow "${@:-$APP}"
        ;;
    share)
        # Expose the app port publicly via a localhost tunnel
        if ! command -v cloudflared >/dev/null 2>&1; then
            echo "  Error: cloudflared is not installed." >&2
            echo "  Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
            exit 1
        fi
        cloudflared tunnel --url "http://localhost:${APP_PORT:-8000}"
        ;;
    *)
        # Pass-through anything else directly to docker compose
        $DC "$@"
        ;;
esac
"""

# ---------------------------------------------------------------------------
# .env key updates per service
# ---------------------------------------------------------------------------

_ENV_UPDATES: dict[str, dict[str, str]] = {
    "pgsql": {
        "DB_CONNECTION": "pgsql",
        "DB_HOST": "pgsql",
        "DB_PORT": "5432",
        "DB_DATABASE": "hunt",
        "DB_USERNAME": "hunt",
        "DB_PASSWORD": "secret",
    },
    "mysql": {
        "DB_CONNECTION": "mysql",
        "DB_HOST": "mysql",
        "DB_PORT": "3306",
        "DB_DATABASE": "hunt",
        "DB_USERNAME": "hunt",
        "DB_PASSWORD": "secret",
    },
    "redis": {
        "REDIS_HOST": "redis",
        "CACHE_DRIVER": "redis",
    },
    "mailpit": {
        "MAIL_MAILER": "smtp",
        "MAIL_HOST": "mailpit",
        "MAIL_PORT": "1025",
        "MAILPIT_DASHBOARD_PORT": "8025",
    },
    "minio": {
        "FILESYSTEM_DISK": "s3",
        "AWS_ENDPOINT": "http://minio:9000",
        "AWS_ACCESS_KEY_ID": "lodge",
        "AWS_SECRET_ACCESS_KEY": "password",
        "AWS_BUCKET": "local",
    },
}


def _set_env(content: str, key: str, value: str) -> str:
    """Set an env key in .env content, appending if missing."""
    pattern = rf"^{re.escape(key)}=.*"
    line = f"{key}={value}"
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, line, content, flags=re.MULTILINE)
    return content.rstrip("\n") + f"\n{line}\n"


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("lodge:install")
@click.option(
    "--with",
    "with_services",
    default="",
    help=f"Comma-separated list of services to include: {', '.join(_SERVICES)}",
)
@click.option(
    "--python",
    "python_version",
    default=None,
    help="Python version for the Docker image (default: current runtime version)",
)
def lodge_install_command(with_services: str, python_version: str | None) -> None:
    """Scaffold a Lodge (Docker Compose) development environment."""
    cwd = Path.cwd()

    # ── Detect app name from pyproject.toml or directory name ────────────────
    app_name = cwd.name
    pyproject = cwd / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("name"):
                m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', line)
                if m:
                    app_name = m.group(1).replace("-", "_")
                    break

    # ── Python version ────────────────────────────────────────────────────────
    if python_version is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    # ── Parse requested services ──────────────────────────────────────────────
    services: list[str] = []
    if with_services:
        for svc in with_services.split(","):
            svc = svc.strip().lower()
            if svc not in _SERVICES:
                click.echo(f"  Unknown service '{svc}'. Available: {', '.join(_SERVICES)}", err=True)
                raise SystemExit(1)
            if svc not in services:
                services.append(svc)

    # Guard: can't have both pgsql and mysql
    if "pgsql" in services and "mysql" in services:
        click.echo("  Cannot include both pgsql and mysql. Choose one.", err=True)
        raise SystemExit(1)

    # ── Guard: ensure this is a hunt project ─────────────────────────────────
    if not (cwd / "bootstrap" / "app.py").exists() and not pyproject.exists():
        click.echo("  No hunt project detected. Run `hunt new <name>` first.", err=True)
        raise SystemExit(1)

    # ── Check for existing compose.yaml ──────────────────────────────────────
    compose_file = cwd / "compose.yaml"
    if compose_file.exists():
        if not click.confirm("  compose.yaml already exists. Overwrite?", default=False):
            click.echo("  Aborted.")
            return

    click.echo(f"\n  Setting up Lodge for {app_name} (Python {python_version})")
    if services:
        click.echo(f"  Services: {', '.join(services)}")

    # ── Write compose.yaml ────────────────────────────────────────────────────
    compose_content = _build_compose(app_name, python_version, services)
    compose_file.write_text(compose_content, encoding="utf-8")
    click.echo("  ✓  compose.yaml")

    # ── Write docker/Dockerfile ───────────────────────────────────────────────
    docker_dir = cwd / "docker"
    docker_dir.mkdir(exist_ok=True)
    dockerfile = docker_dir / "Dockerfile"
    dockerfile.write_text(_DOCKERFILE_DEV, encoding="utf-8")
    click.echo("  ✓  docker/Dockerfile")

    entrypoint = docker_dir / "docker-entrypoint.sh"
    entrypoint.write_text(_ENTRYPOINT, encoding="utf-8")
    entrypoint.chmod(0o755)
    click.echo("  ✓  docker/docker-entrypoint.sh")

    # ── Write lodge script ────────────────────────────────────────────────────
    lodge_script = cwd / "lodge"
    lodge_script.write_text(_LODGE_SCRIPT, encoding="utf-8")
    lodge_script.chmod(0o755)
    click.echo("  ✓  lodge")

    # ── Update .env ───────────────────────────────────────────────────────────
    env_file = cwd / ".env"
    if env_file.exists():
        content = env_file.read_text()
        for svc in services:
            for key, value in _ENV_UPDATES[svc].items():
                content = _set_env(content, key, value)
        env_file.write_text(content, encoding="utf-8")
        click.echo("  ✓  .env updated")

    # ── Update .env.example ───────────────────────────────────────────────────
    env_example = cwd / ".env.example"
    if env_example.exists():
        content = env_example.read_text()
        for svc in services:
            for key, value in _ENV_UPDATES[svc].items():
                content = _set_env(content, key, value)
        env_example.write_text(content, encoding="utf-8")

    # ── Suggest adding lodge to .gitignore ────────────────────────────────────
    gitignore = cwd / ".gitignore"
    if gitignore.exists():
        gi_content = gitignore.read_text()
        if "lodge" not in gi_content:
            click.echo("\n  Note: The lodge script can be safely committed — it contains no secrets.")

    click.echo(
        f"""
  Lodge is ready! Start your environment with:

    ./lodge up -d           Start containers in the background
    ./lodge hunt migrate:run  Run database migrations
    ./lodge hunt serve        Start the development server
    ./lodge shell             Open a shell inside the app container
    ./lodge logs              Tail app logs
    ./lodge down              Stop and remove containers

  Add services later with:  hunt lodge:add <service>
  Available services: {", ".join(_SERVICES)}
"""
    )
