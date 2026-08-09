"""git subprocess timeout handling (2026-08-09 incident follow-up): a hung
subprocess must be confirmed dead (SIGTERM, escalating to SIGKILL) before
anything it was holding — a lock file, a worktree — gets touched. Exercises
`_run_subprocess`/`_terminate_confirmed`/`_clear_own_lock` directly with real
subprocesses; the merge-isolation behavior itself is covered in
tests/integration/test_workspace.py."""

import sys

import pytest

from app.domain.workspace import service as ws


@pytest.fixture(autouse=True)
def _short_grace_periods(monkeypatch):
    """The escalation grace periods default to 5s/5s in production — shrink
    them so a test that deliberately triggers the timeout path doesn't burn
    real wall-clock time. Read at call time (not bound as defaults), so this
    monkeypatch actually takes effect."""
    monkeypatch.setattr(ws, "_TERMINATE_GRACE_S", 0.3)
    monkeypatch.setattr(ws, "_KILL_GRACE_S", 0.3)


def test_fast_process_completes_normally(tmp_path):
    result = ws._run_subprocess(
        [sys.executable, "-c", "print('ok')"], tmp_path, timeout=5
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_slow_but_under_timeout_process_is_not_killed(tmp_path):
    """The escalation path must never fire for an operation that's merely slow
    — only for one that's genuinely still running past its deadline."""
    result = ws._run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('ok')"],
        tmp_path,
        timeout=5,
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_hung_process_is_terminated_then_killed_and_confirmed_dead(tmp_path):
    """A process that ignores SIGTERM must still end up dead (via SIGKILL),
    and _run_subprocess must not return/raise until that's actually confirmed
    — not just assumed. Proven by two markers: one written on receiving
    SIGTERM (graceful path was tried first), one only written if the process
    ran to completion (must NOT exist — it was killed, not left running)."""
    marker_term = tmp_path / "got_sigterm"
    marker_done = tmp_path / "finished"
    script = tmp_path / "hang.py"
    script.write_text(
        "import pathlib, signal, time\n"
        f"marker_term = pathlib.Path({str(marker_term)!r})\n"
        "def _on_term(signum, frame):\n"
        "    marker_term.write_text('1')\n"
        "signal.signal(signal.SIGTERM, _on_term)\n"
        "time.sleep(30)\n"
        f"pathlib.Path({str(marker_done)!r}).write_text('1')\n"
    )

    with pytest.raises(ws.GitTimeoutError, match="已确认终止"):
        ws._run_subprocess([sys.executable, str(script)], tmp_path, timeout=0.2)

    assert marker_term.exists(), "SIGTERM was never tried before escalating"
    assert not marker_done.exists(), "the process must be killed, not left running"


def test_hung_process_index_lock_is_cleared_after_confirmed_death(tmp_path):
    """The lock a timed-out process was holding must be dropped so the NEXT
    operation on this directory isn't wedged the way 2026-08-09's incident
    wedged every accept on the project — but only after the process is
    confirmed dead, never speculatively."""
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"

    script = tmp_path / "hang.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(30)\n"
    )
    lock.write_text("")  # simulate the lock the about-to-hang process is holding

    with pytest.raises(ws.GitTimeoutError):
        ws._run_subprocess([sys.executable, str(script)], tmp_path, timeout=0.2)

    assert not lock.exists()


def test_clear_own_lock_is_a_noop_for_a_linked_worktree(tmp_path):
    """A linked worktree's `.git` is a FILE (pointer to the real admin dir
    under the main repo), not a directory — its lock lives elsewhere and gets
    discarded wholesale when the worktree itself is removed, not surgically
    here."""
    (tmp_path / ".git").write_text("gitdir: /somewhere/else\n")
    ws._clear_own_lock(tmp_path)  # must not raise
    assert (tmp_path / ".git").read_text() == "gitdir: /somewhere/else\n"
