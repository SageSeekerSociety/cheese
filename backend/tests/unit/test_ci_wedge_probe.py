"""TEMPORARY, reverted in the next commit: wedges past the pure layer's
timeout on its first run on a machine and passes on the retry, to show the
hang log and the retry warning on a real CI run."""

import os
import tempfile
import time
from pathlib import Path


def test_wedges_once_then_passes():
    mark = Path(tempfile.gettempdir()) / f"cheese-ci-wedge-probe-{os.getuid()}"
    if not mark.exists():
        mark.write_text("")
        time.sleep(60)
