"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

芝士 authors files through the structured `write_file` tool (not raw Bash, spec
§9.1), and every write is a git commit, so files have version history and diffs
(Phase 4 执行面板: Git/文件). Running code still needs a real sandbox (PVE, §9.1);
this implements the platform/version-control layer that works without it.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import ValidationError


def _repo(project_id: uuid.UUID) -> Path:
    return (Path(settings.workspace_root) / str(project_id)).resolve()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise ValidationError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def ensure_repo(project_id: uuid.UUID) -> Path:
    repo = _repo(project_id)
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "cheese@zhishi.local")
        _git(repo, "config", "user.name", "芝士")
    return repo


def _safe_path(repo: Path, rel: str) -> Path:
    target = (repo / rel).resolve()
    if repo not in target.parents and target != repo:
        raise ValidationError("path escapes the project workspace")
    if ".git" in target.parts:
        raise ValidationError("cannot touch .git")
    return target


def write_file(
    project_id: uuid.UUID, *, path: str, content: str, author: str = "芝士"
) -> dict:
    repo = ensure_repo(project_id)
    target = _safe_path(repo, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", str(target.relative_to(repo)))
    # Commit only if there's a staged change.
    status = _git(repo, "status", "--porcelain")
    if status.strip():
        _git(
            repo,
            "-c",
            f"user.name={author}",
            "commit",
            "-q",
            "-m",
            f"{path}: update via 芝士",
        )
    return {"path": path, "bytes": len(content.encode("utf-8"))}


def list_files(project_id: uuid.UUID) -> list[dict]:
    repo = ensure_repo(project_id)
    files: list[dict] = []
    for p in sorted(repo.rglob("*")):
        if ".git" in p.parts or p.is_dir():
            continue
        rel = p.relative_to(repo)
        files.append({"path": str(rel), "bytes": p.stat().st_size})
    return files


def read_file(project_id: uuid.UUID, path: str) -> str:
    repo = ensure_repo(project_id)
    target = _safe_path(repo, path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target.read_text(encoding="utf-8", errors="replace")


def git_log(project_id: uuid.UUID, limit: int = 50) -> list[dict]:
    repo = ensure_repo(project_id)
    # A fresh repo has no commits yet — `git log` would exit non-zero. Return an
    # empty history instead of erroring.
    if not _git(repo, "rev-list", "-n", "1", "--all").strip():
        return []
    out = _git(repo, "log", f"-{limit}", "--pretty=format:%h\t%an\t%s")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"hash": parts[0], "author": parts[1], "message": parts[2]})
    return rows


def git_diff(project_id: uuid.UUID, ref: str | None = None) -> str:
    repo = ensure_repo(project_id)
    # Default: the last commit's diff; falls back to working-tree diff.
    if ref:
        return _git(repo, "show", ref, "--", ".")
    has_commit = _git(repo, "rev-list", "-n", "1", "--all").strip()
    if has_commit:
        return _git(repo, "show", "HEAD")
    return _git(repo, "diff")
