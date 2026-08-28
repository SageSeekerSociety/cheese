"""Bringing a device's workspace up must never cost the agent its work.

Three separate incidents, one shape: the launcher decided whether a workspace
was usable by asking `[ -d "$CHEESE_WORK/.git" ]`, and that question has a wrong
answer. git points HEAD at `refs/heads/.invalid` for the whole duration of a
clone and only names the real branch as the very last step, so a clone that is
killed in between — a container torn down, a machine rebooted — leaves a `.git`
that satisfies `[ -d ]` and answers `git log` with "fatal: your current branch
appears to be broken". Every later launch stepped straight over it.

The clone itself was written `2>/dev/null || true`, so when it failed there was
nothing anywhere to say so; and because git refuses to clone into a directory
that is not empty, a workspace that had lost only its `.git` got no repository
at all while its files sat there looking fine.

These tests run the launcher's own generated shell against real repositories and
assert on git state, never on the script's text.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.agent.harness.claude_code.device_launch import build_launch_script

_MARKER = "# A device starts with an empty work dir."
_IDENT = ("-c", "user.email=t@cheese.local", "-c", "user.name=t")


def _bringup_body() -> str:
    """The workspace bring-up section of the real generated launcher.

    Taken from the shipped script rather than re-typed, so the test cannot pass
    against a fix that never reached the launcher. The section is one top-level
    `if ... fi`, so it ends at the first `fi` in column zero.
    """
    script = build_launch_script()
    tail = script.split(_MARKER, 1)[1].splitlines()
    body = []
    for line in tail:
        body.append(line)
        if line == "fi":
            break
    return "\n".join(body) + "\n"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_IDENT, *args], cwd=cwd, capture_output=True, text=True
    )


def _origin_with_a_commit(root: Path) -> str:
    """A bare remote holding `topic/abc` with one committed file."""
    remote = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True)
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    (seed / "app.py").write_text("production line\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", str(remote), "HEAD:refs/heads/topic/abc")
    return str(remote)


def _run(work: Path, remote: str, hook_log: Path) -> subprocess.CompletedProcess:
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cheese-hook").write_text(
        f'#!/bin/sh\ncat >> "{hook_log}"\necho >> "{hook_log}"\n'
    )
    (bindir / "cheese-hook").chmod(0o755)
    script = work.parent / "bringup.sh"
    script.write_text(_bringup_body())
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CHEESE_GIT_REMOTE": remote,
        "CHEESE_GIT_BRANCH": "topic/abc",
        "CHEESE_WORK": str(work),
        "CHEESE_TOKEN": "t",
        "CHEESE_GIT_AUTHOR_NAME": "芝士",
        "CHEESE_GIT_AUTHOR_EMAIL": "cheese@zhishi.local",
    }
    return subprocess.run(
        ["sh", str(script)], env=env, capture_output=True, text=True, timeout=120
    )


def _head_ref(work: Path) -> str:
    return _git(work, "symbolic-ref", "-q", "HEAD").stdout.strip()


def test_a_clone_killed_midway_is_repaired_instead_of_stepped_over():
    """The state a torn-down container leaves behind: objects and remote refs
    arrived, HEAD never got past the placeholder, no files in the tree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _origin_with_a_commit(root)
        work = root / "work"
        work.mkdir()
        subprocess.run(["git", "clone", "-q", remote, str(work)], capture_output=True)
        for leftover in work.iterdir():
            if leftover.name != ".git":
                leftover.unlink()
        (work / ".git" / "HEAD").write_text("ref: refs/heads/.invalid\n")

        result = _run(work, remote, root / "hook.log")

        assert result.returncode == 0, result.stderr
        assert _head_ref(work) == "refs/heads/topic/abc", (
            "the launcher stepped over a half-made clone and left HEAD broken: "
            + (work / ".git" / "HEAD").read_text()
        )
        assert (work / "app.py").read_text() == "production line\n"


def test_a_clone_that_fails_leaves_a_trace_instead_of_a_silent_ruin():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        hook_log = root / "hook.log"

        result = _run(work, str(root / "no-such-remote"), hook_log)

        assert result.returncode == 0, result.stderr
        assert hook_log.exists(), "a workspace that could not be cloned said nothing"
        reported = hook_log.read_text()
        assert "CheeseWorkspace" in reported, reported
        assert "no-such-remote" in reported, (
            "the report must carry what git actually said, not a generic failure"
        )


def test_a_workspace_that_lost_its_git_keeps_the_files_the_agent_wrote():
    """git refuses to clone into a non-empty directory. The old launcher asked
    it to anyway and threw the refusal away, so the agent was left with its
    files and no repository at all — and nothing said so."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _origin_with_a_commit(root)
        work = root / "work"
        work.mkdir()
        (work / "app.py").write_text("production line\nthe agent's edit\n")
        (work / "brand_new.py").write_text("only copy\n")

        result = _run(work, remote, root / "hook.log")

        assert result.returncode == 0, result.stderr
        assert (work / "brand_new.py").read_text() == "only copy\n"
        assert (work / "app.py").read_text() == "production line\nthe agent's edit\n"
        assert _head_ref(work) == "refs/heads/topic/abc", (
            "the files survived but the workspace is not a repository"
        )
        dirty = _git(work, "status", "--porcelain").stdout
        assert "app.py" in dirty and "brand_new.py" in dirty, dirty


def test_repairing_a_workspace_never_moves_a_branch_that_has_unpushed_commits():
    """Repair must not become the third way to lose work: a branch that is ahead
    of the remote is the agent's only copy of those commits."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _origin_with_a_commit(root)
        work = root / "work"
        work.mkdir()
        subprocess.run(
            ["git", "clone", "-q", "-b", "topic/abc", remote, str(work)],
            capture_output=True,
        )
        (work / "app.py").write_text("production line\nnever pushed\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", "the agent's own commit")
        local = _git(work, "rev-parse", "HEAD").stdout.strip()
        (work / ".git" / "HEAD").write_text("ref: refs/heads/.invalid\n")

        result = _run(work, remote, root / "hook.log")

        assert result.returncode == 0, result.stderr
        assert _head_ref(work) == "refs/heads/topic/abc"
        assert _git(work, "rev-parse", "HEAD").stdout.strip() == local, (
            "the repair reset the branch onto the remote and dropped a commit "
            "that existed nowhere else"
        )
        assert (work / "app.py").read_text() == "production line\nnever pushed\n"


def test_a_git_too_broken_to_read_is_set_aside_not_deleted():
    """Whatever is left in an unreadable repository may still be the only copy
    of something. The launcher may replace it; it may not throw it away."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _origin_with_a_commit(root)
        work = root / "work"
        work.mkdir()
        (work / ".git").mkdir()
        (work / ".git" / "HEAD").write_text("ref: refs/heads/.invalid\n")
        (work / ".git" / "objects").mkdir()
        (work / ".git" / "objects" / "keepsake").write_text("the only copy\n")

        result = _run(work, remote, root / "hook.log")

        assert result.returncode == 0, result.stderr
        assert _head_ref(work) == "refs/heads/topic/abc", "no usable workspace"
        rescued = [
            p / "objects" / "keepsake"
            for p in work.iterdir()
            if p.name.startswith(".git.broken")
        ]
        assert rescued and rescued[0].read_text() == "the only copy\n", (
            "the unreadable repository was deleted rather than set aside: "
            + str(sorted(p.name for p in work.iterdir()))
        )


def test_a_healthy_workspace_is_left_exactly_as_it_was():
    """The guard on the fix itself. Bringing a workspace up runs on EVERY turn,
    so a repair that also fires on a healthy tree would reset the agent's
    commits and overwrite its edits once per turn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _origin_with_a_commit(root)
        work = root / "work"
        work.mkdir()
        subprocess.run(
            ["git", "clone", "-q", "-b", "topic/abc", remote, str(work)],
            capture_output=True,
        )
        (work / "app.py").write_text("production line\nnever pushed\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", "the agent's own commit")
        (work / "app.py").write_text("production line\nmid-edit\n")
        (work / "scratch.py").write_text("uncommitted\n")
        before = _git(work, "rev-parse", "HEAD").stdout.strip()

        result = _run(work, remote, root / "hook.log")

        assert result.returncode == 0, result.stderr
        assert _git(work, "rev-parse", "HEAD").stdout.strip() == before
        assert (work / "app.py").read_text() == "production line\nmid-edit\n"
        assert (work / "scratch.py").read_text() == "uncommitted\n"
