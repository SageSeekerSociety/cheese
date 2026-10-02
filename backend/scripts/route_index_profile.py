"""Run route inventory commands with one fixed, process-local Settings profile.

The parent imports no application code. Only the child intercepts the actual
Settings constructor, before app.core.config creates its cached singleton.
Neither a settings replacement nor an inherited deployment environment is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

PROFILE_NAME = "route-index-local-uploads-v1"
PROFILE_SETTINGS = MappingProxyType(
    {
        "environment": "test",
        "deployed_via_compose": False,
        "storage_type": "local",
        "storage_local_url": "/uploads",
        "storage_local_path": "./uploads",
        "database_url": (
            "postgresql+asyncpg://route_index:route_index@127.0.0.1:1/route_index"
        ),
        "redis_url": "redis://127.0.0.1:1/0",
    }
)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_WORKER_ACTIVE = False
_OS_ENV_KEYS = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TEMP",
    "TMP",
)
_BOOTSTRAP = (
    "from scripts.route_index_profile import _worker_main; "
    "raise SystemExit(_worker_main())"
)


def profile_header() -> dict:
    """Generated snapshot metadata; no current Settings or environment is read."""
    return {
        "name": PROFILE_NAME,
        "env_file": None,
        "settings_environment": "OS-only child; no inherited Settings variables",
        "settings_overrides": dict(PROFILE_SETTINGS),
        "other_settings": "app.core.config.Settings code defaults",
    }


def in_profile_worker() -> bool:
    """Allow route_index.main to delegate once before its application import."""
    return _WORKER_ACTIVE


def run_profiled(
    args: Sequence[str], *, timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    """Run the same collector for snapshot generation and contract assertions.

    Arguments are route_index CLI arguments. Relative file paths resolve under
    backend; use absolute paths for artifacts outside that directory.
    """
    child_env = {
        key: value
        for key in _OS_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    child_env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP, *args],
        cwd=_BACKEND_ROOT,
        env=child_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def _worker_main() -> int:
    """Construct the singleton once under the profile, then run the collector."""
    global _WORKER_ACTIVE
    if any(name == "app" or name.startswith("app.") for name in sys.modules):
        raise RuntimeError("The route profile must precede every application import")

    from pydantic_settings import BaseSettings

    original_init = BaseSettings.__init__
    initialized = False

    def init_with_profile(instance, *args, **kwargs):
        nonlocal initialized
        if (
            type(instance).__module__ == "app.core.config"
            and type(instance).__name__ == "Settings"
        ):
            kwargs = {**kwargs, **PROFILE_SETTINGS, "_env_file": None}
            initialized = True
        original_init(instance, *args, **kwargs)

    _WORKER_ACTIVE = True
    try:
        with patch.object(BaseSettings, "__init__", init_with_profile):
            from scripts.route_index import main

            result = main()
        if not initialized:
            raise RuntimeError("The collector did not construct the profiled Settings")
        return result
    finally:
        _WORKER_ACTIVE = False


def main() -> int:
    result = run_profiled(sys.argv[1:])
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
