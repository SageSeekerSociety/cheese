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
import uuid

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


def _git_in(repo, *args):
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=repo,
        check=True,
    )


def _rev_parse(repo, ref):
    return subprocess.run(  # noqa: S607
        ["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


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

    def test_dirty_shared_tree_does_not_block_the_sync(self, tmp_path):
        """The shared directory is a mirror of the base tip, not a place work
        is kept — so a local modification there must be discarded, not treated
        as an obstacle. Plain `git checkout <base>` refuses ("Your local
        changes ... would be overwritten by checkout"), which wedged a real
        accept on 2026-08-11 AFTER its merge had already landed."""
        repo = self._repo(tmp_path)
        (repo / "f.txt").write_text("one\n")
        _git_in(repo, "add", "f.txt")
        _git_in(repo, "commit", "-q", "-m", "one")
        main_sha = _rev_parse(repo, "HEAD")
        # A second branch that changes the same file, and a HEAD parked on it.
        _git_in(repo, "checkout", "-q", "-b", "other")
        (repo / "f.txt").write_text("two\n")
        _git_in(repo, "commit", "-q", "-am", "two")
        # ...plus an uncommitted edit to that same file: the exact shape git
        # refuses to check out over.
        (repo / "f.txt").write_text("uncommitted\n")

        ws._sync_shared_checkout(repo, "main", main_sha)  # must not raise

        assert (repo / "f.txt").read_text() == "one\n"
        assert _rev_parse(repo, "HEAD") == main_sha

    def test_discarded_local_modifications_are_named_in_the_log(self, tmp_path, caplog):
        """Discarding is the point — but the shared tree is writable
        (`write_file`/`exec_in_sandbox` with topic_id=None both land here and
        nothing ever commits them), so a discard can destroy something a human
        typed. The forced checkout must therefore say what it threw away:
        trading "permanently fails" for "silently loses data" would be a net
        loss, since the second is the harder one to diagnose."""
        repo = self._repo(tmp_path)
        (repo / "kept.txt").write_text("v1\n")
        (repo / "also-kept.txt").write_text("v1\n")
        _git_in(repo, "add", "kept.txt", "also-kept.txt")
        _git_in(repo, "commit", "-q", "-m", "one")
        sha = _rev_parse(repo, "HEAD")
        (repo / "kept.txt").write_text("someone's unsaved work\n")
        (repo / "also-kept.txt").write_text("and more\n")
        # Untracked files survive both the forced checkout and the reset, so
        # naming one here would be a false alarm.
        (repo / "untracked.txt").write_text("survives\n")

        with caplog.at_level("WARNING", logger=ws.logger.name):
            ws._sync_shared_checkout(repo, "main", sha)

        warnings = "\n".join(
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        )
        assert "kept.txt" in warnings
        assert "also-kept.txt" in warnings
        assert "untracked.txt" not in warnings
        assert (repo / "untracked.txt").exists()

    def test_a_clean_shared_tree_logs_no_discard_warning(self, tmp_path, caplog):
        """The warning must mean something when it appears — a sync that threw
        nothing away has to stay silent, or the log is noise."""
        repo = self._repo(tmp_path)
        (repo / "f.txt").write_text("v1\n")
        _git_in(repo, "add", "f.txt")
        _git_in(repo, "commit", "-q", "-m", "one")
        sha = _rev_parse(repo, "HEAD")

        with caplog.at_level("WARNING", logger=ws.logger.name):
            ws._sync_shared_checkout(repo, "main", sha)

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

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


class TestMergeSurvivesSharedCheckoutFailure:
    """The shared-tree sync runs AFTER the compare-and-swap that advances the
    base branch, so by then the merge is durable. If the sync fails anyway, the
    caller must still be told the merge happened — reporting it as a failed
    merge told a user "采纳未完成：合并出错" about work already sitting on main,
    and invited a re-accept of an already-merged topic (2026-08-11)."""

    def _project_repo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        (repo / "base.txt").write_text("base\n")
        _git_in(repo, "add", "base.txt")
        _git_in(repo, "commit", "-q", "-m", "base")
        _git_in(repo, "checkout", "-q", "-b", "topic")
        (repo / "topic.txt").write_text("topic\n")
        _git_in(repo, "add", "topic.txt")
        _git_in(repo, "commit", "-q", "-m", "topic work")
        _git_in(repo, "checkout", "-q", "main")
        return repo

    def test_merge_reported_as_merged_when_sync_fails(self, tmp_path, monkeypatch):
        repo = self._project_repo(tmp_path, monkeypatch)
        before = _rev_parse(repo, "main")

        def _boom(*_args, **_kwargs):
            raise ws.ValidationError("git checkout failed: simulated dirty tree")

        monkeypatch.setattr(ws, "_sync_shared_checkout", _boom)

        result = ws._merge_ref_into_base(
            uuid.uuid4(), repo, "main", "topic", "采纳 topic → main"
        )

        assert result["merged"] is True, "the ref move already landed"
        assert "sync_failed" in result
        after = _rev_parse(repo, "main")
        assert after != before, "base branch must actually have advanced"
        assert (
            "topic work"
            in subprocess.run(  # noqa: S607
                ["git", "log", "--oneline", "main"],
                cwd=repo,
                capture_output=True,
                text=True,
            ).stdout
        )

    def test_successful_sync_reports_no_failure_key(self, tmp_path, monkeypatch):
        repo = self._project_repo(tmp_path, monkeypatch)

        result = ws._merge_ref_into_base(
            uuid.uuid4(), repo, "main", "topic", "采纳 topic → main"
        )

        assert result["merged"] is True
        assert "sync_failed" not in result
        assert (repo / "topic.txt").exists(), "shared tree really was synced"


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
