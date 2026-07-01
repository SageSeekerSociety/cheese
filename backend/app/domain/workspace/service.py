"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

每个 project = 一个 git 仓,主仓用 Jujutsu (jj) colocate(`.git` + `.jj` 并存),
每个话题 = 一个 jj workspace(取代 git worktree)。这样沙箱容器里的原生
Bash/Write/Edit 改动会被 jj 自动快照(无需手动 commit),而 git 侧照常工作:
话题分支用 jj bookmark 导出成 git branch,采纳/diff 仍走 git(colocation)。
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


def _jj(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["jj", "--no-pager", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValidationError(f"jj {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _ensure_jj(repo: Path) -> None:
    """Colocate a jj repo onto the git repo (idempotent). jj then auto-snapshots
    the working copy, while git refs stay live for merge/diff."""
    if (repo / ".jj").exists():
        return
    _jj(repo, "git", "init", "--colocate")
    _jj(repo, "config", "set", "--repo", "user.name", "芝士")
    _jj(repo, "config", "set", "--repo", "user.email", "cheese@zhishi.local")


def ensure_repo(project_id: uuid.UUID) -> Path:
    repo = _repo(project_id)
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q", "-b", DEFAULT_BRANCH)
        _git(repo, "config", "user.email", "cheese@zhishi.local")
        _git(repo, "config", "user.name", "芝士")
    _ensure_base_commit(repo)  # main must have a real commit before jj colocates
    _ensure_jj(repo)
    return repo


def _has_commit(repo: Path) -> bool:
    # HEAD specifically (not --all): after jj colocate there are jj refs, so
    # `rev-list --all` would falsely report a base commit while main is unborn.
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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
    """每话题一个独立 jj workspace（沙箱地基）：分身在自己的工作目录里干活，
    jj 自动快照其改动；并行话题互不覆盖。导出一个同名 git 分支供采纳/diff。"""
    main = ensure_repo(project_id)
    _ensure_base_commit(main)  # a workspace needs a base commit to fork from
    wt = _worktree_path(project_id, branch)
    if (wt / ".jj").exists():  # already a jj workspace
        return wt
    # Migrate a stale git worktree left by the pre-jj design.
    if wt.exists():
        try:
            _git(main, "worktree", "remove", "--force", str(wt))
        except ValidationError:
            pass
        shutil.rmtree(wt, ignore_errors=True)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _jj(main, "workspace", "add", "--name", branch.replace("/", "_"), str(wt))
    # Export a git branch (= jj bookmark) for this topic so merge/diff use git.
    _jj(wt, "bookmark", "set", branch, "-r", "@", "--allow-backwards")
    _jj(wt, "git", "export")
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


def list_files(
    project_id: uuid.UUID, topic_id: uuid.UUID | None = None
) -> list[dict]:
    tree = _tree(project_id, topic_id)
    files: list[dict] = []
    for p in sorted(tree.rglob("*")):
        if ".git" in p.parts or ".jj" in p.parts or p.is_dir():
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


def write_file(
    project_id: uuid.UUID, path: str, content: str, topic_id: uuid.UUID | None = None
) -> None:
    """Write a file in the topic's worktree (人改文件即指令 — the agent reads the
    latest on its next turn, like 改文档即指令). _safe_path guards traversal + .git."""
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


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
    """Snapshot whatever the agent changed in the topic's workspace this turn as a
    jj commit, so native Bash/Write/Edit edits become version history (no manual
    commit needed). The topic's git branch (bookmark) is moved to the new commit
    so 采纳/diff still work via git."""
    branch = branch_for_topic(topic_id)
    wt = _ensure_worktree(project_id, branch)
    if not _jj(wt, "diff", "-s").strip():
        return  # nothing changed this turn
    _jj(wt, "commit", "-m", message)
    # The just-committed work is @- (jj commit started a fresh empty @).
    _jj(wt, "bookmark", "set", branch, "-r", "@-", "--allow-backwards")
    _jj(wt, "git", "export")


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


def container_name(topic_id: uuid.UUID) -> str:
    """Deterministic name of a topic's long-lived sandbox container."""
    return f"cheesex-sbx-{topic_id.hex[:12]}"


def stop_topic_container(topic_id: uuid.UUID) -> None:
    """Remove a topic's sandbox container (e.g. when the topic is merged/archived
    or its worktree is recreated). Best-effort: a missing container is fine."""
    if not sandbox_available():
        return
    subprocess.run(
        ["docker", "rm", "-f", container_name(topic_id)],
        capture_output=True,
        text=True,
    )


def reap_sandbox_containers() -> int:
    """Remove all CheeseX sandbox containers (label cheesex-sandbox=1). Called at
    startup: containers from a previous run hold stale mounts, so we drop them and
    let each topic recreate its own on the next turn. Returns how many were removed."""
    if not sandbox_available():
        return 0
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=cheesex-sandbox=1"],
        capture_output=True,
        text=True,
    )
    ids = [c for c in listed.stdout.split() if c]
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True)
    return len(ids)
