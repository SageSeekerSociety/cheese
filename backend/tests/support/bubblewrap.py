"""A machine as cheesehost leaves it on Linux: the bwrap a room's sandbox is
made with, and the host tools a platform host's sandbox helper runs as root,
placed under the owner's footprint (`cli/internal/devenv`). Here they are the
system's own, linked into place."""

import shutil
from pathlib import Path

from app.domain.agent.harness.claude_code.remote_execution import bootstrap

SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def placed(home: Path) -> Path:
    """Where cheesehost places bwrap under ``home``."""
    return home / ".cheese/runtime/bubblewrap/bwrap"


def place(home: Path) -> None:
    targets = {"bwrap": placed(home)}
    tools = home / ".cheese/runtime/host-tools/bin"
    targets.update({name: tools / name for name in bootstrap.HOST_TOOLS})
    for name, target in targets.items():
        found = shutil.which(name, path=SYSTEM_PATH)
        if found is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(found)
