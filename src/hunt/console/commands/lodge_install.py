from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Available services
# ---------------------------------------------------------------------------

_SERVICES = ("pgsql", "mysql", "redis", "mailpit", "minio")

# Image tags are pinned to explicit releases so a rebuild never silently pulls a
# different major/minor version. Bump them deliberately.
_IMAGE_PGSQL = "postgres:16.16"
_IMAGE_MYSQL = "mysql:8.0.46"
_IMAGE_REDIS = "redis:7.4.12-alpine"
_IMAGE_MAILPIT = "axllent/mailpit:v1.33.1"
# MinIO no longer publishes community images under minio/minio. This is the
# same MinIO server built by Chainguard, which is publicly pullable; its free
# tier only offers the "latest" tag, so it is pinned by digest
# (RELEASE.2026-11-12T01-15-23Z). Override with LODGE_MINIO_IMAGE.
_IMAGE_MINIO = "cgr.dev/chainguard/minio@sha256:18c8fa28697b25e0f34c6faae171079e88a03efb8e76a21d3b5d07b4e3cbef3e"

# Published ports bind to loopback only. Containers reach each other over the
# private "lodge" network, so nothing needs to listen on other interfaces.
# FORWARD_*_PORT is the host side of a mapping; DB_PORT, REDIS_PORT, MAIL_PORT
# and friends stay the in-container ports the application connects to.
_BIND = "127.0.0.1"

_APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_PYTHON_VERSION_RE = re.compile(r"^\d+\.\d+$")

# ---------------------------------------------------------------------------
# Compose YAML builders
# ---------------------------------------------------------------------------

_SERVICE_PGSQL = """\
  pgsql:
    image: "__IMAGE_PGSQL__"
    ports:
      - "__BIND__:${FORWARD_DB_PORT:-5432}:5432"
    environment:
      POSTGRES_DB: "${DB_DATABASE:-hunt}"
      POSTGRES_USER: "${DB_USERNAME:-hunt}"
      POSTGRES_PASSWORD: "${DB_PASSWORD:?Set DB_PASSWORD in .env}"
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
    image: "__IMAGE_MYSQL__"
    ports:
      - "__BIND__:${FORWARD_DB_PORT:-3306}:3306"
    environment:
      MYSQL_ROOT_PASSWORD: "${DB_PASSWORD:?Set DB_PASSWORD in .env}"
      MYSQL_ROOT_HOST: "localhost"
      MYSQL_DATABASE: "${DB_DATABASE:-hunt}"
      MYSQL_USER: "${DB_USERNAME:-hunt}"
      MYSQL_PASSWORD: "${DB_PASSWORD:?Set DB_PASSWORD in .env}"
    volumes:
      - "lodge-mysql:/var/lib/mysql"
    networks:
      - lodge
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-p${DB_PASSWORD:?Set DB_PASSWORD in .env}"]
      retries: 3
      timeout: 5s
"""

_SERVICE_REDIS = """\
  redis:
    image: "__IMAGE_REDIS__"
    ports:
      - "__BIND__:${FORWARD_REDIS_PORT:-6379}:6379"
    environment:
      # Lets `redis-cli` (health check, `./lodge redis`) authenticate.
      REDISCLI_AUTH: "${REDIS_PASSWORD:?Set REDIS_PASSWORD in .env}"
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?Set REDIS_PASSWORD in .env}"]
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
    image: "__IMAGE_MAILPIT__"
    ports:
      - "__BIND__:${FORWARD_MAILPIT_PORT:-1025}:1025"
      - "__BIND__:${FORWARD_MAILPIT_DASHBOARD_PORT:-8025}:8025"
    networks:
      - lodge
"""

_SERVICE_MINIO = """\
  minio:
    image: "${LODGE_MINIO_IMAGE:-__IMAGE_MINIO__}"
    ports:
      - "__BIND__:${FORWARD_MINIO_PORT:-9000}:9000"
      - "__BIND__:${FORWARD_MINIO_CONSOLE_PORT:-9001}:9001"
    environment:
      MINIO_ROOT_USER: "${AWS_ACCESS_KEY_ID:?Set AWS_ACCESS_KEY_ID in .env}"
      MINIO_ROOT_PASSWORD: "${AWS_SECRET_ACCESS_KEY:?Set AWS_SECRET_ACCESS_KEY in .env}"
    volumes:
      - "lodge-minio:/data"
    networks:
      - lodge
    # Explicit entrypoint + command: works whether or not the image already
    # sets `minio` as its entrypoint.
    entrypoint: ["minio"]
    command: ["server", "/data", "--console-address", ":9001"]
    healthcheck:
      # `mc` ships in the image; curl does not.
      test: ["CMD", "mc", "ready", "local"]
      retries: 3
      timeout: 5s
"""


def _render_service(template: str) -> str:
    return (
        template.replace("__BIND__", _BIND)
        .replace("__IMAGE_PGSQL__", _IMAGE_PGSQL)
        .replace("__IMAGE_MYSQL__", _IMAGE_MYSQL)
        .replace("__IMAGE_REDIS__", _IMAGE_REDIS)
        .replace("__IMAGE_MAILPIT__", _IMAGE_MAILPIT)
        .replace("__IMAGE_MINIO__", _IMAGE_MINIO)
    )


_SERVICE_BLOCKS = {
    "pgsql": _render_service(_SERVICE_PGSQL),
    "mysql": _render_service(_SERVICE_MYSQL),
    "redis": _render_service(_SERVICE_REDIS),
    "mailpit": _render_service(_SERVICE_MAILPIT),
    "minio": _render_service(_SERVICE_MINIO),
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
      - "{_BIND}:${{APP_PORT:-8000}}:8000"
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
        "REDIS_PORT": "6379",
        "REDIS_PASSWORD": "secret",
        "CACHE_DRIVER": "redis",
    },
    "mailpit": {
        "MAIL_MAILER": "smtp",
        "MAIL_HOST": "mailpit",
        "MAIL_PORT": "1025",
        # Mailpit speaks plain SMTP; "tls" would fail the STARTTLS handshake.
        "MAIL_ENCRYPTION": "none",
    },
    "minio": {
        "FILESYSTEM_DISK": "s3",
        "AWS_ENDPOINT_URL": "http://minio:9000",
        "AWS_ACCESS_KEY_ID": "lodge",
        "AWS_SECRET_ACCESS_KEY": "password",
        "AWS_BUCKET": "local",
    },
}

# Values a developer may already have set to something real. They are only
# written when the key is missing or empty, never replaced.
_PRESERVED_KEYS = frozenset(
    {
        "DB_USERNAME",
        "DB_PASSWORD",
        "REDIS_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BUCKET",
    }
)

# Keys that get a random value in .env instead of the well-known default.
_GENERATED_SECRETS = frozenset({"DB_PASSWORD", "REDIS_PASSWORD", "AWS_SECRET_ACCESS_KEY"})

# Python packages a service needs that hunt does not install by default.
_DRIVER_EXTRAS: dict[str, tuple[str, str]] = {
    "pgsql": ("postgres", "psycopg2-binary"),
    "redis": ("redis", "redis"),
    "minio": ("s3", "boto3"),
}


def _get_env(content: str, key: str) -> str | None:
    """Return the value of *key* in .env content, or None when it is not defined."""
    m = re.search(rf"^{re.escape(key)}=(.*)$", content, re.MULTILINE)
    if m is None:
        return None
    return m.group(1).strip().strip("\"'")


def _set_env(content: str, key: str, value: str) -> str:
    """Set an env key in .env content, appending if missing."""
    pattern = rf"^{re.escape(key)}=.*"
    line = f"{key}={value}"
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, lambda _m: line, content, flags=re.MULTILINE)
    return content.rstrip("\n") + f"\n{line}\n"


def _looks_like_sqlite_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith((".sqlite", ".sqlite3", ".db")) or value == ":memory:"


def _apply_service_env(content: str, service: str, *, generate_secrets: bool) -> tuple[str, list[str]]:
    """Apply a service's env defaults to .env content.

    Wiring keys (hosts, ports, driver names) are set to the values the compose
    services use. Credentials that already hold a non-empty value are left
    untouched. Returns the new content and the list of keys that were kept.
    """
    kept: list[str] = []
    for key, value in _ENV_UPDATES.get(service, {}).items():
        current = _get_env(content, key)
        if current:
            if key in _PRESERVED_KEYS:
                kept.append(key)
                continue
            if key == "DB_DATABASE" and not _looks_like_sqlite_path(current):
                kept.append(key)
                continue
        if generate_secrets and key in _GENERATED_SECRETS:
            value = secrets.token_urlsafe(18)
        content = _set_env(content, key, value)
    return content, kept


def _driver_hint(service: str) -> str | None:
    """Return install instructions for the driver package *service* needs, if any."""
    extra = _DRIVER_EXTRAS.get(service)
    if extra is None:
        return None
    extra_name, package = extra
    hint = (
        f"  {service} needs the '{package}' package, which hunt does not install by default.\n"
        f'    Install it:  pip install "hunt-framework[{extra_name}]"   (or: pip install {package})\n'
        f"    and add it to the dependencies in pyproject.toml so the Lodge image includes it."
    )
    if service == "minio":
        hint += (
            "\n  The MinIO service uses the Chainguard build of MinIO, pinned by digest.\n"
            "    Set LODGE_MINIO_IMAGE in .env to use a different MinIO image."
        )
    return hint


def _confirm(message: str) -> bool:
    """Ask a yes/no question; a closed or non-interactive stdin counts as "no"."""
    try:
        return click.confirm(message, default=False)
    except click.Abort:
        click.echo("")
        return False


def _write_guarded(path: Path, content: str, label: str, *, force: bool, mode: int | None = None) -> bool:
    """Write a generated file without silently replacing an existing one.

    Returns True when the file now holds *content*.
    """
    if path.exists():
        try:
            current: str | None = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current == content:
            if mode is not None:
                path.chmod(mode)
            click.echo(f"  =  {label} (unchanged)")
            return True
        if not force and not _confirm(f"  {label} already exists. Overwrite?"):
            click.echo(f"  -  {label} kept (use --force to overwrite)")
            return False
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    click.echo(f"  ✓  {label}")
    return True


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
@click.option("--force", is_flag=True, help="Overwrite existing Lodge files without asking.")
def lodge_install_command(with_services: str, python_version: str | None, force: bool) -> None:
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
                    app_name = m.group(1)
                    break
    # The name becomes a Docker image repository inside compose.yaml, so it
    # must be a plain lowercase token: nothing that could break out of the
    # quoted YAML scalar or be rejected by Docker.
    app_name = app_name.replace("-", "_").lower()
    if not _APP_NAME_RE.match(app_name):
        click.echo(
            f"  Invalid application name {app_name!r}: use letters, digits, '.', '_' or '-' "
            "(set [project].name in pyproject.toml).",
            err=True,
        )
        raise SystemExit(1)

    # ── Python version ────────────────────────────────────────────────────────
    if python_version is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    elif not _PYTHON_VERSION_RE.match(python_version):
        click.echo(f"  Invalid --python value {python_version!r}. Expected MAJOR.MINOR, e.g. 3.12.", err=True)
        raise SystemExit(1)

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

    click.echo(f"\n  Setting up Lodge for {app_name} (Python {python_version})")
    if services:
        click.echo(f"  Services: {', '.join(services)}")

    # Every generated file is confirmed separately: an existing file is only
    # replaced with --force or an explicit "y".
    # ── Write compose.yaml ────────────────────────────────────────────────────
    compose_file = cwd / "compose.yaml"
    compose_content = _build_compose(app_name, python_version, services)
    compose_written = _write_guarded(compose_file, compose_content, "compose.yaml", force=force)

    # ── Write docker/Dockerfile ───────────────────────────────────────────────
    docker_dir = cwd / "docker"
    docker_dir.mkdir(exist_ok=True)
    _write_guarded(docker_dir / "Dockerfile", _DOCKERFILE_DEV, "docker/Dockerfile", force=force)
    _write_guarded(
        docker_dir / "docker-entrypoint.sh", _ENTRYPOINT, "docker/docker-entrypoint.sh", force=force, mode=0o755
    )

    # ── Write lodge script ────────────────────────────────────────────────────
    _write_guarded(cwd / "lodge", _LODGE_SCRIPT, "lodge", force=force, mode=0o755)

    # ── Update .env / .env.example ────────────────────────────────────────────
    # Only when compose.yaml matches what was requested: pointing .env at
    # services a kept compose.yaml does not define would break the app.
    if not compose_written:
        if services:
            click.echo("  -  .env not modified because compose.yaml was kept")
    else:
        env_file = cwd / ".env"
        if env_file.exists() and services:
            content = env_file.read_text()
            kept: list[str] = []
            for svc in services:
                content, svc_kept = _apply_service_env(content, svc, generate_secrets=True)
                kept.extend(svc_kept)
            env_file.write_text(content, encoding="utf-8")
            click.echo("  ✓  .env updated")
            if kept:
                click.echo(f"     kept existing values for: {', '.join(kept)}")

        env_example = cwd / ".env.example"
        if env_example.exists() and services:
            content = env_example.read_text()
            for svc in services:
                content, _ = _apply_service_env(content, svc, generate_secrets=False)
            env_example.write_text(content, encoding="utf-8")

    # ── Driver packages the selected services need ───────────────────────────
    hints = [hint for hint in (_driver_hint(svc) for svc in services) if hint]
    if hints:
        click.echo("")
        for hint in hints:
            click.echo(hint)
    if "minio" in services:
        click.echo(f"\n  MinIO image: {_IMAGE_MINIO} (set LODGE_MINIO_IMAGE in .env to use a mirror).")

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

  Service ports are published on 127.0.0.1 only. Change the host side with
  FORWARD_DB_PORT, FORWARD_REDIS_PORT, FORWARD_MAILPIT_PORT, ... in .env.

  Add services later with:  hunt lodge:add <service>
  Available services: {", ".join(_SERVICES)}
"""
    )
