"""Suite-wide test isolation.

Several commands under test call ``load_dotenv()`` or ``os.chdir()``; without a
guard, one test's environment (``LOG_CHANNEL``, ``DB_*``, ``APP_ENV``, ...) or
working directory leaks into every test that runs after it.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _restore_process_state():
    """Put ``os.environ`` and the working directory back after every test."""
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    try:
        yield
    finally:
        for key in set(os.environ) - set(saved_env):
            del os.environ[key]
        for key, value in saved_env.items():
            if os.environ.get(key) != value:
                os.environ[key] = value
        if os.getcwd() != saved_cwd:
            os.chdir(saved_cwd)
