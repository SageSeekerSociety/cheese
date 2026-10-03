"""The executor, installed the way a machine's bootstrap installs it.

A fixture that starts an executor starts the runtime a machine would run, with
the files it reads beside it (`runtime.RELEASE_FILES`), staged and activated
by the bootstrap's own `stage_release` and `activate_release`. So a file the
runtime comes to need is installed here the moment the launch ships it.

Standard library only, and it loads the runtime and bootstrap from their
source files rather than through `app`: the contract scripts under
`scripts/remote_execution` run with a bare interpreter and use this too.
"""

import base64
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SOURCE = BACKEND / "app/domain/agent/harness/claude_code/remote_execution"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"executor_release_{name}", SOURCE / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(platform_dir: Path) -> Path:
    """Install the executor under `platform_dir`, as `~/.cheese` is on a
    machine; return the runtime to start."""
    runtime, bootstrap = _load("runtime"), _load("bootstrap")
    platform_dir = Path(platform_dir)
    platform_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "files": {
            name: base64.b64encode((BACKEND / source).read_bytes()).decode()
            for name, source in runtime.RELEASE_FILES.items()
        }
    }
    # A machine keeps its releases beside its rooms (`bootstrap.release_store`);
    # a fixture's own directory is as good a place to keep its one.
    release, contents = bootstrap.stage_release(
        platform_dir / "executor-releases", platform_dir, payload
    )
    bootstrap.activate_release(platform_dir, release, contents)
    return platform_dir / "remote-execution/runtime.py"
