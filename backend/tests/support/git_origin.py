"""A repository on disk that git reaches the way it reaches a forge.

`merge_check` fetches a PR's base and head by sha without file contents, so the
repository standing in for the forge serves both (`uploadpack.allowFilter`,
`uploadpack.allowAnySHA1InWant`, which Forgejo also turns on). Its address is a
`file://` URL.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.test",
        },
    ).stdout.strip()


@dataclass(frozen=True)
class Proposal:
    url: str
    base: str
    head: str


def proposal(root: Path, *, conflicting: bool) -> Proposal:
    """A base branch that moved on after the head branched off it: the two
    merge cleanly, or, when ``conflicting``, both changed the same line."""
    work = root / "work"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("line\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "start")
    _git(work, "checkout", "-q", "-b", "task")
    if conflicting:
        (work / "README.md").write_text("task's line\n")
    else:
        (work / "TASK.md").write_text("task\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "task")
    head = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "main")
    (work / "README.md").write_text("main's line\n")
    _git(work, "commit", "-qam", "main moves on")
    base = _git(work, "rev-parse", "HEAD")
    origin = root / "origin.git"
    _git(root, "clone", "-q", "--bare", str(work), str(origin))
    _git(origin, "config", "uploadpack.allowFilter", "true")
    _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(origin, "fetch", "-q", str(work), "task:task")
    return Proposal(url=origin.as_uri(), base=base, head=head)


def repositories(root: Path, count: int) -> list[Proposal]:
    """``count`` repositories, each at its own address, holding the same clean
    proposal (copies of one, which is faster than building each)."""
    pr = proposal(root / "first", conflicting=False)
    first = root / "first" / "origin.git"
    copies = []
    for i in range(count):
        copy = root / f"copy-{i}.git"
        shutil.copytree(first, copy)
        copies.append(Proposal(url=copy.as_uri(), base=pr.base, head=pr.head))
    return copies


def proposals_over_time(root: Path, count: int) -> tuple[str, str, list[str]]:
    """One repository whose task branch was pushed ``count`` times: its base,
    and the head of each push, every one of which merges cleanly."""
    pr = proposal(root, conflicting=False)
    work = root / "work"
    _git(work, "checkout", "-q", "task")
    heads = [pr.head]
    for i in range(count - 1):
        (work / "TASK.md").write_text(f"task {i}\n")
        _git(work, "commit", "-qam", f"push {i}")
        heads.append(_git(work, "rev-parse", "HEAD"))
    _git(root / "origin.git", "fetch", "-q", str(work), "+task:task")
    return pr.url, pr.base, heads
