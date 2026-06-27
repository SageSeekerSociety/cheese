"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

芝士 authors files through the structured `write_file` tool (not raw Bash, spec
§9.1), and every write is a git commit, so files have version history and diffs
(Phase 4 执行面板: Git/文件). Running code still needs a real sandbox (PVE, §9.1);
this implements the platform/version-control layer that works without it.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import ValidationError

DEFAULT_BRANCH = "main"
# 沙箱镜像：芝士分身在容器里跑代码/测试，碰不到宿主机 (spec §9.1).
SANDBOX_IMAGE = "python:3.12-slim"


def _repo(project_id: uuid.UUID) -> Path:
    return (Path(settings.workspace_root) / str(project_id)).resolve()


def branch_for_topic(topic_id: uuid.UUID) -> str:
    """话题 = git 分支 (spec §6.3). Deterministic from the topic id."""
    return f"topic/{topic_id.hex[:8]}"


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
        _git(repo, "init", "-q", "-b", DEFAULT_BRANCH)
        _git(repo, "config", "user.email", "cheese@zhishi.local")
        _git(repo, "config", "user.name", "芝士")
    return repo


def _has_commit(repo: Path) -> bool:
    return bool(_git(repo, "rev-list", "-n", "1", "--all").strip())


def _ensure_base_commit(repo: Path) -> None:
    # Branches need a base commit to fork from.
    if not _has_commit(repo):
        _git(
            repo,
            "-c",
            "user.name=芝士",
            "-c",
            "user.email=cheese@zhishi.local",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "init",
        )


def _branch_exists(repo: Path, branch: str) -> bool:
    return bool(_git(repo, "branch", "--list", branch).strip())


def _base_branch(repo: Path) -> str:
    if _branch_exists(repo, DEFAULT_BRANCH):
        return DEFAULT_BRANCH
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or DEFAULT_BRANCH


def _worktree_path(project_id: uuid.UUID, branch: str) -> Path:
    safe = branch.replace("/", "_")
    return (
        Path(settings.workspace_root) / ".worktrees" / str(project_id) / safe
    ).resolve()


def _ensure_worktree(project_id: uuid.UUID, branch: str) -> Path:
    """每话题一个独立 git worktree（沙箱地基）：分身在自己的工作目录里干活，
    并行话题互不覆盖。共享同一个 .git，按需创建。"""
    main = ensure_repo(project_id)
    _ensure_base_commit(main)  # a worktree needs a base commit to fork from
    wt = _worktree_path(project_id, branch)
    if (wt / ".git").exists():  # already a registered worktree (.git is a file)
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(main, branch):
        _git(main, "worktree", "add", "-q", str(wt), branch)
    else:
        _git(main, "worktree", "add", "-q", "-b", branch, str(wt), _base_branch(main))
    return wt


def _tree(project_id: uuid.UUID, topic_id: uuid.UUID | None) -> Path:
    """The working dir for an operation: the topic's worktree, or the base repo
    for project-level (no topic)."""
    if topic_id is None:
        return ensure_repo(project_id)
    return _ensure_worktree(project_id, branch_for_topic(topic_id))


def _safe_path(repo: Path, rel: str) -> Path:
    target = (repo / rel).resolve()
    if repo not in target.parents and target != repo:
        raise ValidationError("path escapes the project workspace")
    if ".git" in target.parts:
        raise ValidationError("cannot touch .git")
    return target


def write_file(
    project_id: uuid.UUID,
    *,
    path: str,
    content: str,
    author: str = "芝士",
    topic_id: uuid.UUID | None = None,
) -> dict:
    # 话题 = 分支 = 独立 worktree (spec §6.3): a topic's writes land in its own
    # working dir on its own branch; project-level writes go to the base repo.
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(tree, "add", str(target.relative_to(tree)))
    # Commit only if there's a staged change.
    status = _git(tree, "status", "--porcelain")
    if status.strip():
        _git(
            tree,
            "-c",
            f"user.name={author}",
            "commit",
            "-q",
            "-m",
            f"{path}: update via 芝士",
        )
    return {"path": path, "bytes": len(content.encode("utf-8"))}


def list_files(
    project_id: uuid.UUID, topic_id: uuid.UUID | None = None
) -> list[dict]:
    tree = _tree(project_id, topic_id)
    files: list[dict] = []
    for p in sorted(tree.rglob("*")):
        if ".git" in p.parts or p.is_dir():
            continue
        rel = p.relative_to(tree)
        files.append({"path": str(rel), "bytes": p.stat().st_size})
    return files


def read_file(
    project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
) -> str:
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target.read_text(encoding="utf-8", errors="replace")


def grep(
    project_id: uuid.UUID, pattern: str, topic_id: uuid.UUID | None = None
) -> str:
    """Search tracked files in the topic's worktree (read-only). Empty on no
    match. git grep is jailed to the tree, so it can't read outside the repo."""
    tree = _tree(project_id, topic_id)
    try:
        out = _git(tree, "grep", "-n", "-I", "--no-color", "-e", pattern)
    except ValidationError:
        return ""  # git grep exits non-zero when nothing matches
    return out[:8000]


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
        # Reject option-injection (e.g. ref='--help'); refs never start with '-'.
        if ref.startswith("-"):
            raise ValidationError("invalid ref")
        return _git(repo, "show", "--end-of-options", ref, "--", ".")
    has_commit = _git(repo, "rev-list", "-n", "1", "--all").strip()
    if has_commit:
        return _git(repo, "show", "HEAD")
    return _git(repo, "diff")


def topic_diff(project_id: uuid.UUID, topic_id: uuid.UUID) -> str:
    """Full diff of a topic's branch vs the base (what 采纳 would merge)."""
    repo = ensure_repo(project_id)
    branch = branch_for_topic(topic_id)
    if not _branch_exists(repo, branch):
        return ""
    base = _base_branch(repo)
    return _git(repo, "diff", f"{base}...{branch}")


def merge_topic(project_id: uuid.UUID, topic_id: uuid.UUID) -> dict:
    """采纳 = merge (spec §6.3): merge the topic's branch into the base branch.
    Best-effort — on conflict it aborts and reports, never half-merges."""
    repo = ensure_repo(project_id)
    branch = branch_for_topic(topic_id)
    if not _branch_exists(repo, branch):
        return {"merged": False, "reason": "no topic branch"}
    base = _base_branch(repo)
    if branch == base:
        return {"merged": False, "reason": "topic is the base branch"}
    _git(repo, "checkout", "-q", base)
    try:
        _git(
            repo,
            "-c",
            "user.name=芝士",
            "-c",
            "user.email=cheese@zhishi.local",
            "merge",
            "--no-ff",
            "-q",
            "-m",
            f"采纳 {branch} → {base}",
            branch,
        )
    except ValidationError as exc:
        try:
            _git(repo, "merge", "--abort")
        except ValidationError:
            pass
        return {"merged": False, "reason": str(exc)}
    return {"merged": True, "branch": branch, "into": base}


def topic_worktree(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Host path of a topic's git worktree (created on demand), world-writable so
    the sandbox container's non-root `node` user can write into the mount."""
    wt = _ensure_worktree(project_id, branch_for_topic(topic_id))
    import os

    os.chmod(wt, 0o777)
    return wt


_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"


def session_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Persistent per-topic ~/.claude (mounted into the ephemeral container) so
    the agent session / --resume survives across turns. Also seeds the `cheese`
    skill here (= ~/.claude/skills, the user source) — one mount holds both the
    session and the skill, with no host settings leaking in."""
    import os
    import shutil

    d = (
        Path(settings.workspace_root) / ".sessions" / str(project_id) / topic_id.hex[:8]
    ).resolve()
    d.mkdir(parents=True, exist_ok=True)
    skills_dst = d / "skills"
    if _SKILL_SRC.is_dir():
        shutil.copytree(_SKILL_SRC, skills_dst, dirs_exist_ok=True)
    for root, _dirs, files in os.walk(d):
        os.chmod(root, 0o777)
        for f in files:
            os.chmod(os.path.join(root, f), 0o666)
    return d


def snapshot_worktree(
    project_id: uuid.UUID, topic_id: uuid.UUID, message: str = "芝士 edits"
) -> None:
    """Commit whatever the agent changed in the topic's worktree this turn, so
    native Bash/Write edits become version history (no manual commit needed)."""
    wt = _ensure_worktree(project_id, branch_for_topic(topic_id))
    _git(wt, "add", "-A")
    if _git(wt, "status", "--porcelain").strip():
        _git(
            wt,
            "-c",
            "user.name=芝士",
            "-c",
            "user.email=cheese@zhishi.local",
            "commit",
            "-q",
            "-m",
            message,
        )


def sandbox_available() -> bool:
    return shutil.which("docker") is not None


def exec_in_sandbox(
    project_id: uuid.UUID,
    command: str,
    *,
    topic_id: uuid.UUID | None = None,
    timeout: int = 30,
) -> dict:
    """Run a shell command in an isolated container with the topic's worktree
    mounted at /work (沙箱, spec §9.1): --network none, mem/cpu caps, ephemeral
    (--rm). 芝士 can run code/tests without touching the host."""
    tree = _tree(project_id, topic_id)
    if not sandbox_available():
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "sandbox 不可用：未找到 docker（需要 Docker 在运行）",
        }
    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "512m", "--cpus", "1",
                "--pids-limit", "256",
                "-v", f"{tree}:/work", "-w", "/work",
                SANDBOX_IMAGE,
                "sh", "-lc", command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": f"执行超时（>{timeout}s）"}
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:4000],
    }
