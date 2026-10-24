"""Where a test's machine owner lives when its sandboxes get the platform's
limits: no sandbox runs without a disk limit, and that takes a home on an XFS
filesystem with project quotas (`sandbox_host.py`), as a platform host's data
disk is. CI mounts one and names a directory on it
(`CHEESE_TEST_SANDBOX_DISK`, `.github/scripts/sandbox-disk.sh`)."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

DISK = os.environ.get("CHEESE_TEST_SANDBOX_DISK", "")


def available() -> bool:
    return bool(DISK) and os.path.isdir(DISK)


@contextlib.contextmanager
def owner(tmp_path: Path):
    """The machine owner's home, on the quota disk when there is one."""
    if not available():
        yield tmp_path / "owner"
        return
    base = Path(tempfile.mkdtemp(dir=DISK))
    try:
        yield base / "owner"
    finally:
        shutil.rmtree(base, ignore_errors=True)
