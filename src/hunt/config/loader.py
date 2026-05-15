from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any


def load_config_directory(config_path: Path) -> dict[str, Any]:
    """Load all Python files in config/ and return merged dict keyed by filename stem."""
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
