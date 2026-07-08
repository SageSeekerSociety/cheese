"""Functional test for the Claude Code cheeselet driver's `say` send path.

The driver is JavaScript (runs in the client's goja runtime), so we exercise the
real driver source in Node via a small harness that stubs the `cheese` API and
records the terminal write sequence. This guards the bracketed-paste vs Enter race:
`say` must paste the body and defer the submitting Enter to a later frame, never
emitting the CR in the same step as the paste (which leaves long/multiline messages
stuck at the "[Pasted text #N +L lines]" placeholder, un-submitted).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "claude_driver_harness.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_say_defers_enter_as_separate_step() -> None:
    result = subprocess.run(
        ["node", str(_HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"driver harness failed:\n{result.stdout}\n{result.stderr}"
    assert "OK:" in result.stdout
