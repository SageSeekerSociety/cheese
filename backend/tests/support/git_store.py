"""Local repositories representing the remote forge in API tests."""

import json
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings


def path(project_id: uuid.UUID) -> Path:
    return (Path(settings.workspace_root) / str(project_id)).resolve()


def run(argv, cwd, timeout=60, env=None):
    return subprocess.run(
        argv, cwd=cwd, timeout=timeout, env=env, capture_output=True, text=True
    )


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], repo)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def ensure_repo(project_id: uuid.UUID) -> Path:
    repo = path(project_id)
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Test Agent")
        git(repo, "config", "user.email", "agent@example.test")
        git(repo, "commit", "--allow-empty", "-qm", "Initialize test repository")
    return repo


def bind_task(task_id: uuid.UUID, *, branch: str, directory: str, base: str) -> None:
    descriptor = (
        Path(settings.workspace_root) / ".task-workspaces" / f"{task_id.hex}.json"
    )
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_text(
        json.dumps({"branch": branch, "directory": directory, "base": base})
    )


def task(task_id: uuid.UUID) -> dict[str, str]:
    descriptor = (
        Path(settings.workspace_root) / ".task-workspaces" / f"{task_id.hex}.json"
    )
    return json.loads(descriptor.read_text())


def branch_for_task(task_id: uuid.UUID) -> str:
    return task(task_id)["branch"]


def head(project_id: uuid.UUID, revision: str = "main") -> str:
    return git(path(project_id), "rev-parse", revision).strip()


def merge_task(project_id: uuid.UUID, task_id: uuid.UUID, *, message: str) -> str:
    repo = path(project_id)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--squash", branch_for_task(task_id))
    git(repo, "commit", "-qm", message)
    return head(project_id)
