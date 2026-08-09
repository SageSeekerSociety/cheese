"""git subprocess timeout handling (2026-08-09 incident follow-up): a hung
subprocess must be confirmed dead (SIGTERM, escalating to SIGKILL) before
anything it was holding — a lock file, a worktree — gets touched. Exercises
`_run_subprocess`/`_terminate_confirmed`/`_clear_own_lock` directly with real
subprocesses; the merge-isolation behavior itself is covered in
tests/integration/test_workspace.py."""

import os
import subprocess
import sys
import time

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


def _backdate(path, age_s: float) -> None:
    now = time.time()
    os.utime(path, (now - age_s, now - age_s))


class TestLockStaleness:
    """2026-08-09 incident, round 2: a redeploy SIGKILLed the backend
    mid-checkout in the SHARED repo directory (not an isolated worktree —
    there's no process handle to confirm death against here, unlike
    `_run_subprocess`'s own timeout path), leaving a lock nobody will ever
    clear. `_is_lock_stale` is the heuristic that decides when it's safe to
    drop: old enough that no legitimate operation could still be running it,
    AND not currently held open by any live process."""

    def test_fresh_lock_is_never_stale_even_if_unheld(self, tmp_path):
        lock = tmp_path / "index.lock"
        lock.write_text("")
        assert ws._is_lock_stale(lock, age_threshold_s=60) is False

    def test_old_unheld_lock_is_stale(self, tmp_path):
        lock = tmp_path / "index.lock"
        lock.write_text("")
        _backdate(lock, 120)
        assert ws._is_lock_stale(lock, age_threshold_s=60) is True

    def test_old_lock_still_held_open_is_not_stale(self, tmp_path):
        lock = tmp_path / "index.lock"
        held = open(lock, "w")  # noqa: SIM115 — must stay open for the assertion
        try:
            _backdate(lock, 120)
            assert ws._is_lock_stale(lock, age_threshold_s=60) is False
        finally:
            held.close()

    def test_missing_lock_is_not_stale(self, tmp_path):
        assert ws._is_lock_stale(tmp_path / "gone", age_threshold_s=60) is False


class TestSyncSharedCheckout:
    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "init",
            ],
            cwd=repo,
            check=True,
        )
        return repo

    def test_stale_lock_is_cleared_and_checkout_proceeds(self, tmp_path):
        repo = self._repo(tmp_path)
        sha = subprocess.run(  # noqa: S607
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        lock = repo / ".git" / "index.lock"
        lock.write_text("")
        _backdate(lock, 120)

        ws._sync_shared_checkout(repo, "main", sha)  # must not raise

        assert not lock.exists()

    def test_live_lock_is_retried_then_raises_without_being_deleted(
        self, tmp_path, monkeypatch
    ):
        """A lock that's NOT stale (fresh, or held open) must never be
        deleted out from under whatever legitimately holds it — the caller
        retries a few times and then surfaces a clear failure instead."""
        monkeypatch.setattr(ws, "_SHARED_CHECKOUT_RETRY_DELAY_S", 0.01)
        repo = self._repo(tmp_path)
        sha = subprocess.run(  # noqa: S607
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        lock = repo / ".git" / "index.lock"
        lock.write_text("")  # fresh — never considered stale regardless of holder

        with pytest.raises(ws.ValidationError):
            ws._sync_shared_checkout(repo, "main", sha)

        assert lock.exists(), "a lock that isn't provably stale must survive"


class TestReapOrphanedMergeWorktrees:
    def test_old_worktree_dir_is_removed(self, tmp_path, monkeypatch):
        import uuid as uuid_mod

        monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
        pid = uuid_mod.uuid4()
        repo = ws.ensure_repo(pid)
        merge_root = ws._merge_worktree_path(pid)
        merge_root.mkdir(parents=True)
        stale = merge_root / "deadbeef"
        stale.mkdir()
        _backdate(stale, ws._ORPHANED_MERGE_WORKTREE_AFTER_S + 60)

        ws._reap_orphaned_merge_worktrees(repo, pid)

        assert not stale.exists()

    def test_fresh_worktree_dir_is_left_alone(self, tmp_path, monkeypatch):
        import uuid as uuid_mod

        monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
        pid = uuid_mod.uuid4()
        repo = ws.ensure_repo(pid)
        merge_root = ws._merge_worktree_path(pid)
        merge_root.mkdir(parents=True)
        fresh = merge_root / "cafebabe"
        fresh.mkdir()

        ws._reap_orphaned_merge_worktrees(repo, pid)

        assert fresh.exists()
