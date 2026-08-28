"""What a machine that owns its own tree does, for tests that need a commit.

Nothing on the platform writes anybody's working tree, so this is the only way a
commit reaches a topic branch: the machine clones the project's repo over git,
commits, and pushes the branch back (`api/routes/git_http.py` serves the same
thing over HTTP). A test that wants work on a branch does it the way the real
thing does, rather than reaching into the platform's own checkout — which is
also what keeps these tests honest if that checkout ever changes shape.
"""

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.domain.workspace import service as ws

CHEESE_NAME = "芝士"
CHEESE_EMAIL = "cheese@zhishi.local"


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
    repo = ws.ensure_repo(project_id)
    branch = ws.branch_for_tree(ws.tree_for_place(place_id))
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
            _git(work, "checkout", "-q", "-b", branch)
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
