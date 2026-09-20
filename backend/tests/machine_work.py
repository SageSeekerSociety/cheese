"""Clone, commit and push as an executor against the test forge's Git store."""

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from tests.support import git_store

CHEESE_NAME = "芝士"
CHEESE_EMAIL = "cheese@zhishi.local"


def declare_task(project_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """Declare delivery ownership explicitly in filesystem-only unit fixtures."""
    repo = git_store.ensure_repo(project_id)
    base = git_store.git(repo, "symbolic-ref", "--short", "HEAD").strip()
    git_store.bind_task(
        task_id,
        branch=f"task/{task_id.hex[:8]}",
        directory=f"task_{task_id.hex[:8]}",
        base=base,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, f"git {' '.join(args)} failed: {done.stderr}"
    return done


def machine_commits(
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    files: dict[str, str],
    message: str = "chore: work from the machine",
    *,
    author: tuple[str, str] = (CHEESE_NAME, CHEESE_EMAIL),
) -> str:
    """Write `files` on this place's branch and push it back. Returns the sha."""
    repo = git_store.ensure_repo(project_id)
    branch = git_store.branch_for_task(place_id)
    work = Path(tempfile.mkdtemp(prefix="cheese-machine-"))
    try:
        _git(work.parent, "clone", "-q", str(repo), str(work))
        _git(work, "config", "user.name", author[0])
        _git(work, "config", "user.email", author[1])
        known = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"origin/{branch}"],
            cwd=work,
            capture_output=True,
            text=True,
        )
        if known.returncode == 0:
            _git(work, "checkout", "-q", "-B", branch, f"origin/{branch}")
        else:
            base = git_store.task(place_id)["base"]
            _git(work, "checkout", "-q", "-b", branch, f"origin/{base}")
        for path, content in files.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", message)
        _git(work, "push", "-q", "origin", branch)
        return _git(work, "rev-parse", "HEAD").stdout.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)
