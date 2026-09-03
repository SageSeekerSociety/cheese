"""The end-of-turn snapshot must back work up without rewriting where it is.

The incident: an agent proving a test went red had temporarily reverted a file.
The turn ended, the snapshot committed that revert and pushed it, and the
agent's next `git checkout HEAD -- <file>` — the ordinary way back — restored
the REVERT, because HEAD was no longer the commit the agent had read. Thirty
three lines of production code left the shared branch with every test still
green and a clean `git status`.

Bisecting, A/B comparison, "prove it fails first", a temporary print: every one
of them assumes HEAD stays where the agent left it. So the snapshot may push,
and must not commit.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.agent.harness.claude_code.device_launch import build_launch_script

_IDENT = ("-c", "user.email=t@cheese.local", "-c", "user.name=t")
_GOOD = "def guard():\n    return True\n"
_REVERTED = "def guard():\n    pass\n"


def _sync_body() -> str:
    script = build_launch_script(sync_on_stop=True)
    return script.split("'SYNC'")[1].split("SYNC")[0]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_IDENT, *args], cwd=cwd, capture_output=True, text=True
    )


def _workspace(root: Path) -> tuple[Path, str]:
    """A device workspace on `topic/abc`, one commit deep, remote in sync."""
    remote = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True)
    work = root / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@cheese.local")
    _git(work, "config", "user.name", "t")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "app.py").write_text(_GOOD)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "the agent's own commit")
    _git(work, "checkout", "-q", "-B", "topic/abc")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/topic/abc")
    return work, str(remote)


def _run(work: Path, remote: str, hook_log: Path) -> subprocess.CompletedProcess:
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cheese-hook").write_text(f'#!/bin/sh\ncat >> "{hook_log}"\n')
    (bindir / "cheese-hook").chmod(0o755)
    sync = work.parent / "cheese-sync"
    sync.write_text(_sync_body())
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CHEESE_GIT_REMOTE": remote,
        "CHEESE_GIT_BRANCH": "topic/abc",
        "CHEESE_WORK": str(work),
    }
    return subprocess.run(
        ["sh", str(sync)], env=env, capture_output=True, text=True, timeout=120
    )


def _remote_ref(remote: str, ref: str) -> str:
    out = subprocess.run(
        ["git", "--git-dir", remote, "rev-parse", "-q", "--verify", ref],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_restoring_from_head_after_a_snapshot_gives_back_the_agents_own_work():
    """The incident, start to finish."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work, remote = _workspace(root)
        (work / "app.py").write_text(_REVERTED)  # temporary, to prove a red test

        _run(work, remote, root / "hook.log")
        _git(work, "checkout", "HEAD", "--", "app.py")  # the ordinary way back

        assert (work / "app.py").read_text() == _GOOD, (
            "the snapshot moved HEAD, so restoring from it froze the temporary "
            "revert instead of undoing it"
        )


def test_the_snapshot_does_not_move_head():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work, remote = _workspace(root)
        before = _git(work, "rev-parse", "HEAD").stdout.strip()
        (work / "app.py").write_text(_REVERTED)
        (work / "scratch.py").write_text("debug print\n")

        _run(work, remote, root / "hook.log")

        assert _git(work, "rev-parse", "HEAD").stdout.strip() == before
        dirty = _git(work, "status", "--porcelain").stdout
        assert "app.py" in dirty and "scratch.py" in dirty, (
            "the working tree stopped showing the agent's own edits: " + dirty
        )


def test_the_shared_branch_never_receives_work_the_agent_did_not_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work, remote = _workspace(root)
        head = _git(work, "rev-parse", "HEAD").stdout.strip()
        (work / "app.py").write_text(_REVERTED)

        _run(work, remote, root / "hook.log")

        assert _remote_ref(remote, "refs/heads/topic/abc") == head, (
            "a half-finished working tree was published as delivered work"
        )


def test_uncommitted_work_still_reaches_the_remote():
    """The snapshot's real job. A machine can vanish between turns, so work that
    is not committed still has to leave the machine — just not onto the branch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work, remote = _workspace(root)
        (work / "brand_new.py").write_text("only copy\n")

        _run(work, remote, root / "hook.log")

        saved = _remote_ref(remote, "refs/cheese/snapshots/topic/abc")
        assert saved, "uncommitted work never left the machine"
        listed = subprocess.run(
            ["git", "--git-dir", remote, "show", f"{saved}:brand_new.py"],
            capture_output=True,
            text=True,
        )
        assert listed.stdout == "only copy\n", listed.stderr


def test_commits_the_agent_made_are_still_pushed_every_turn():
    """The property that must survive the fix: only what is pushed exists."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work, remote = _workspace(root)
        (work / "app.py").write_text(_GOOD + "# finished\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", "more of the agent's own work")
        head = _git(work, "rev-parse", "HEAD").stdout.strip()

        _run(work, remote, root / "hook.log")

        assert _remote_ref(remote, "refs/heads/topic/abc") == head
