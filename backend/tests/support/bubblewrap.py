"""A machine as cheesehost leaves it on Linux: the bwrap a room's sandbox is
made with, and the host tools a platform host's sandbox helper runs as root,
placed under the owner's footprint (`cli/internal/devenv`). CI selects the
pinned bwrap; other tools use the system's own executables, linked into place."""

import contextlib
import os
import shutil
import sys
from pathlib import Path

from app.domain.agent import sandbox_image
from app.domain.agent.harness.claude_code.remote_execution import bootstrap

SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def placed(home: Path) -> Path:
    """Where cheesehost places bwrap under ``home``."""
    return home / ".cheese/runtime/bubblewrap/bwrap"


def image(monkeypatch, tmp_path):
    """Use the runner's native files as the lower for generic isolation tests.

    OCI export/cache behavior is covered separately. These tests need native
    executables and exercise the real overlay, namespaces, limits and sockets.
    """
    if os.environ.get("CHEESE_TEST_ROOTFS"):
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    lock = tmp_path / "image-lease"
    lock.touch()

    @contextlib.contextmanager
    def lease(_reference):
        with lock.open("rb") as held:
            yield Path(os.environ["CHEESE_TEST_ROOTFS"]), held.fileno()

    run_path = bootstrap.runpy.run_path

    def load(path, *args, **kwargs):
        if Path(path).name == "sandbox_image.py":
            return {"lease": lease, "namespace": sandbox_image.namespace}
        return run_path(path, *args, **kwargs)

    monkeypatch.setattr(bootstrap.runpy, "run_path", load)


def place(home: Path) -> None:
    targets = {"bwrap": placed(home)}
    tools = home / ".cheese/runtime/host-tools/bin"
    targets.update({name: tools / name for name in bootstrap.HOST_TOOLS})
    for name, target in targets.items():
        found = (
            os.environ.get("CHEESE_TEST_BWRAP") if name == "bwrap" else None
        ) or shutil.which(name, path=SYSTEM_PATH)
        if found is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(found)
