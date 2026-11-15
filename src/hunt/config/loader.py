from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path
from typing import Any

#: Location of the config cache written by ``hunt config:cache``, relative to the application root.
CONFIG_CACHE_RELATIVE_PATH = Path("storage") / "framework" / "config.json"


def config_cache_path(config_path: Path) -> Path:
    """Return the cache file that belongs to the application owning *config_path*."""
    return Path(config_path).parent / CONFIG_CACHE_RELATIVE_PATH


def load_config_directory(config_path: Path, use_cache: bool = True) -> dict[str, Any]:
    """Load all Python files in config/ and return merged dict keyed by filename stem.

    When ``hunt config:cache`` has written a valid cache file for this application
    it is returned instead and the config/*.py files are not executed. Remove it with
    ``hunt config:clear`` after changing configuration or environment variables.
    Pass ``use_cache=False`` to always evaluate the Python files.
    """
    if use_cache:
        cached = load_config_cache(config_cache_path(config_path))
        if cached is not None:
            return cached
    return load_config_files(config_path)


def load_config_files(config_path: Path) -> dict[str, Any]:
    """Execute every config/*.py file and return the merged dict keyed by filename stem."""
    configs: dict[str, Any] = {}
    if not config_path.is_dir():
        return configs
    root = config_path.resolve()
    for file in sorted(config_path.glob("*.py")):
        # Reject paths that escape config_path (e.g. via symlinks)
        if not str(file.resolve()).startswith(str(root) + os.sep):
            continue
        spec = importlib.util.spec_from_file_location(file.stem, file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
        if hasattr(module, "config"):
            configs[file.stem] = module.config
        else:
            configs[file.stem] = {k: v for k, v in vars(module).items() if not k.startswith("_")}
    return configs


def load_config_cache(cache_file: Path) -> dict[str, Any] | None:
    """Return the cached configuration, or None when there is no cache that can be trusted.

    The cache holds resolved secrets and replaces the config files, so it is only
    honoured when it is a regular file (not a symlink) that is owned by the current
    user and not writable by group or others, and that parses to a JSON object.
    Anything else is ignored and the caller falls back to config/*.py.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(cache_file, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if hasattr(os, "getuid"):
            if info.st_uid != os.getuid():
                return None
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return None
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    finally:
        if fd != -1:
            os.close(fd)
    if not isinstance(data, dict):
        return None
    return data


def write_config_cache(cache_file: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* as JSON to *cache_file* with mode 0600.

    Raises ``TypeError``/``ValueError`` (before touching the filesystem) when *data*
    cannot be represented in JSON without loss.
    """
    payload = json.dumps(data, indent=2, allow_nan=False)
    if json.loads(payload) != data:
        raise ValueError("configuration does not survive a JSON round trip (tuples or non-string keys?)")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, cache_file)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
