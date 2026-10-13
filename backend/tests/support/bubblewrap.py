"""A machine as cheesehost leaves it on Linux: the bwrap a room's sandbox is
made with placed under the owner's footprint (`cli/internal/devenv`). Here it
is the system's own, linked into place."""

import shutil
from pathlib import Path


def placed(home: Path) -> Path:
    """Where cheesehost places bwrap under ``home``."""
    return home / ".cheese/runtime/bubblewrap/bwrap"


def place(home: Path) -> None:
    found = shutil.which("bwrap")
    if found is None:
        return
    target = placed(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.symlink_to(found)
