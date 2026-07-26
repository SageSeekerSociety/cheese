"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

每个 project = 一个 git 仓,主仓用 Jujutsu (jj) colocate(`.git` + `.jj` 并存),
每个话题 = 一个 jj workspace(取代 git worktree)。这样沙箱容器里的原生
Bash/Write/Edit 改动会被 jj 自动快照(无需手动 commit),而 git 侧照常工作:
话题分支用 jj bookmark 导出成 git branch,采纳/diff 仍走 git(colocation)。
"""

import os
import re
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


def _git(repo: Path, *args: str, timeout: int = 20) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        # git reports merge conflicts on stdout with an empty stderr — fall back
        # so the caller's error isn't blank.
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValidationError(f"git {args[0]} failed: {detail}")
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
    _make_world_writable(wt)
    return wt


def _make_world_writable(root: Path) -> None:
    """The sandbox's non-root user must be able to edit a worktree the backend
    (possibly root) materialized — found live when 芝士 hit Permission denied on
    a freshly seeded repo. Adds rw bits while PRESERVING exec bits: git tracks
    the user-exec bit, so a blind 666 would dirty every executable's mode."""
    for dirpath, _dirnames, filenames in os.walk(root):
        try:
            os.chmod(dirpath, os.stat(dirpath).st_mode | 0o777)
        except OSError:
            continue
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, os.stat(p).st_mode | 0o666)
            except OSError:
                pass


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


# Never listed (nor descended into): VCS internals + dependency/cache dirs a
# turn may create in the worktree (npm ci → 13k node_modules entries).
_SKIP_DIRS = {
    ".git",
    ".jj",
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    "dist",
    ".next",
}


def list_files(project_id: uuid.UUID, topic_id: uuid.UUID | None = None) -> list[dict]:
    tree = _tree(project_id, topic_id)
    files: list[dict] = []
    for root, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(root) / name
            rel = p.relative_to(tree)
            files.append({"path": str(rel), "bytes": p.stat().st_size})
    files.sort(key=lambda f: f["path"])
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


def read_file_bytes(
    project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
) -> bytes:
    """Raw bytes of a worktree file (binary-safe — images/attachments; the text
    reader would mangle them). Same traversal guard as read_file."""
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target.read_bytes()


def write_file_bytes(
    project_id: uuid.UUID, path: str, data: bytes, topic_id: uuid.UUID | None = None
) -> None:
    """Binary-safe write into the topic's worktree (聊天图片等附件落盘 — 进版本库，
    文件面板可见，沙箱里芝士可直接 Read). Same guards as write_file."""
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


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
    # Fold any pending working-copy changes into the branch first: a human may
    # have edited files (人改文件即指令) with no agent turn afterwards to
    # snapshot them — accepting must deliver what the reviewer actually saw.
    try:
        snapshot_worktree(project_id, topic_id, "采纳前快照")
    except ValidationError:
        pass  # no workspace/jj state yet — nothing pending to fold
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
            conflicts = (
                _git(repo, "diff", "--name-only", "--diff-filter=U")
                .strip()
                .splitlines()
            )
        except ValidationError:
            conflicts = []
        try:
            _git(repo, "merge", "--abort")
        except ValidationError:
            pass
        reason = "合并冲突：" + "、".join(conflicts[:20]) if conflicts else str(exc)
        return {"merged": False, "reason": reason, "conflicts": conflicts}
    return {"merged": True, "branch": branch, "into": base}


# ---- 上游仓库 (spec §6.3): 关联已有 repo + 同步上游 ----------------------------

UPSTREAM_REMOTE = "upstream"

# URL shapes we accept for the upstream remote: https, ssh, scp-style git@, or an
# absolute local path. Everything else (option-looking strings, ext:: transport,
# whitespace tricks) is rejected — the URL goes straight to `git fetch`.
_UPSTREAM_URL_RE = re.compile(r"^(https://|ssh://|git@|/)[\w.@:/~+-]+$")


def get_upstream(project_id: uuid.UUID) -> str | None:
    """The project's linked upstream URL, or None when not linked. The git remote
    itself is the storage — no separate DB field to drift out of sync."""
    repo = ensure_repo(project_id)
    result = subprocess.run(
        ["git", "remote", "get-url", UPSTREAM_REMOTE],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def set_upstream(project_id: uuid.UUID, url: str) -> str | None:
    """Link (or with an empty url, unlink) the project's upstream repo."""
    repo = ensure_repo(project_id)
    url = url.strip()
    if not url:
        subprocess.run(  # removing a missing remote is fine — unlink is idempotent
            ["git", "remote", "remove", UPSTREAM_REMOTE],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        return None
    if not _UPSTREAM_URL_RE.match(url):
        raise ValidationError("上游地址不合法（支持 https/ssh/git@/绝对路径）")
    if get_upstream(project_id) is None:
        _git(repo, "remote", "add", UPSTREAM_REMOTE, url)
    else:
        _git(repo, "remote", "set-url", UPSTREAM_REMOTE, url)
    return url


def _upstream_ref(repo: Path) -> str:
    """The upstream branch to sync from: main, falling back to master."""
    for name in (DEFAULT_BRANCH, "master"):
        ref = f"{UPSTREAM_REMOTE}/{name}"
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", ref],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ref
    raise ValidationError("上游仓库没有 main/master 分支")


def sync_upstream(project_id: uuid.UUID) -> dict:
    """同步上游: fetch the upstream remote and merge its default branch into the
    project's base branch. The first sync of a seeded/fresh repo is an
    unrelated-histories merge; a conflict aborts cleanly (never half-merges) and
    reports back — same contract as merge_topic."""
    repo = ensure_repo(project_id)
    if get_upstream(project_id) is None:
        return {"synced": False, "reason": "未关联上游仓库"}
    try:
        _git(repo, "fetch", UPSTREAM_REMOTE, timeout=120)
        ref = _upstream_ref(repo)
    except ValidationError as exc:
        return {"synced": False, "reason": str(exc)}
    base = _base_branch(repo)
    behind = int(_git(repo, "rev-list", "--count", f"{base}..{ref}").strip() or "0")
    if behind == 0:
        return {"synced": True, "commits": 0, "reason": "已是最新"}
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
            "--allow-unrelated-histories",
            "-m",
            f"同步上游 {ref} → {base}",
            ref,
        )
    except ValidationError as exc:
        # Name the conflicted files before aborting — "同步失败" without saying
        # where is undebuggable for the user.
        try:
            conflicts = (
                _git(repo, "diff", "--name-only", "--diff-filter=U")
                .strip()
                .splitlines()
            )
        except ValidationError:
            conflicts = []
        try:
            _git(repo, "merge", "--abort")
        except ValidationError:
            pass
        reason = "合并冲突：" + "、".join(conflicts[:20]) if conflicts else str(exc)
        return {"synced": False, "reason": reason, "conflicts": conflicts}
    return {"synced": True, "commits": behind}


def prepare_conflict_resolution(
    project_id: uuid.UUID, topic_id: uuid.UUID
) -> list[str]:
    """采纳冲突 → 派芝士解决的前置：在话题的 jj workspace 里创建 branch×base 的
    合并提交，冲突以标记形式materialize 在文件里；返回冲突文件列表。芝士改完文件、
    平台照常快照（merge commit 连同解决一起入 bookmark），重试采纳即可干净合并。"""
    branch = branch_for_topic(topic_id)
    wt = _ensure_worktree(project_id, branch)
    base = _base_branch(ensure_repo(project_id))
    # The workspace's jj view lags the git side — pull the base branch's latest
    # commits in first, or the merge would use a stale bookmark (and possibly
    # see no conflict at all).
    try:
        _jj(wt, "git", "import")
    except ValidationError:
        pass
    _jj(wt, "new", branch, base)
    out = _jj(wt, "resolve", "--list")
    files = [line.split()[0] for line in out.splitlines() if line.strip()]
    # Move the bookmark onto the (conflicted) merge so the snapshot/export path
    # keeps working; the resolution edits amend this same commit.
    _jj(wt, "bookmark", "set", branch, "-r", "@", "--allow-backwards")
    return files


def upstream_default_branch(repo: Path) -> str | None:
    """The upstream's own default branch (what its HEAD points at), so a push
    lands where that repo actually keeps its trunk instead of a guessed name."""
    try:
        out = _git(repo, "ls-remote", "--symref", UPSTREAM_REMOTE, "HEAD", timeout=60)
    except ValidationError:
        return None
    for line in out.splitlines():
        if line.startswith("ref:"):
            ref = line.split()[1]
            return ref.rsplit("/", 1)[-1]
    return None


def push_back(project_id: uuid.UUID, topic_id: uuid.UUID) -> dict:
    """采纳即上线: propagate an accepted merge to the upstream repo.

    采纳 IS the merge — the topic branch is already merged into the project's
    base by the time we get here — so the upstream should receive THAT merge,
    fast-forward, not a side branch waiting for someone to decide again. Landing
    it must never need cheese-specific setup in the target repo (the whole point
    of "import a repo and it just works").

    Order:
      1. fast-forward the upstream's own default branch (never forced — a
         rejected push means the upstream moved or protects the branch, which is
         information, not something to overwrite);
      2. if that is refused, fall back to pushing ``dogfood/<topic>`` so the work
         is never stuck on our side, and say so — the caller surfaces it instead
         of leaving the user to wonder why nothing shipped.

    Auth comes from the HOST's git credentials, never from the DB. A local-path
    upstream additionally runs its ``scripts/on-dogfood-push.sh`` DETACHED
    (operator-trusted only for local paths)."""
    repo = ensure_repo(project_id)
    url = get_upstream(project_id)
    if not url:
        return {"pushed": False, "mode": "none", "reason": "无上游，跳过回推"}
    base = _base_branch(repo)
    branch = f"dogfood/{topic_id.hex[:8]}"
    target = upstream_default_branch(repo) or base
    if not url.startswith("/"):
        # 120s: the first remote push negotiates history (the remote already has
        # upstream's objects, so the delta stays small — but be safe).
        try:
            _git(repo, "push", UPSTREAM_REMOTE, f"{base}:{target}", timeout=120)
            return {"pushed": True, "mode": "upstream", "target": target}
        except ValidationError as exc:
            reason = str(exc)[-400:]
            _git(repo, "push", "-f", UPSTREAM_REMOTE, f"{base}:{branch}", timeout=120)
            return {
                "pushed": True,
                "mode": "branch",
                "branch": branch,
                "target": target,
                "reason": reason,
            }
    # Local-path upstream: keep the branch + hook flow (the hook merges/deploys).
    # -f: re-accepting (after revoke / a follow-up round) moves the same branch.
    _git(repo, "push", "-f", UPSTREAM_REMOTE, f"{base}:{branch}", timeout=120)
    hook = Path(url) / "scripts" / "on-dogfood-push.sh"
    hook_started = False
    if hook.is_file() and os.access(hook, os.X_OK):
        log = Path(url) / "tmp_dogfood_push.log"
        with open(log, "a") as out:
            subprocess.Popen(  # noqa: S603 — operator-trusted local repo hook
                [str(hook), branch],
                cwd=url,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # survives our own redeploy
            )
        hook_started = True
    return {"pushed": True, "mode": "branch", "branch": branch, "hook": hook_started}


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


def spool_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Host path of the topic's hook-event spool (WAL). The tmux container writes
    here via CHEESE_HOOK_SPOOL=/home/node/.claude/cheese-spool (the session dir
    mounts to /home/node/.claude), and the backend reconciles from it. Mirrors
    session_dir's base so both sides agree on ONE location."""
    return (
        Path(settings.workspace_root)
        / ".sessions"
        / str(project_id)
        / topic_id.hex[:8]
        / "cheese-spool"
    ).resolve()


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
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                "-v",
                f"{tree}:/work",
                "-w",
                "/work",
                SANDBOX_IMAGE,
                "sh",
                "-lc",
                command,
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


# 运行环境预览: every topic container publishes this in-container port to a
# random localhost port at creation (claude-sbx). The AI starts whatever server
# the project needs on 0.0.0.0:$CHEESE_APP_PORT and declares it (cheese serve).
APP_PORT = 3000


def app_preview_url(topic_id: uuid.UUID) -> str | None:
    """http://127.0.0.1:<host-port> for the topic container's published app
    port, or None (container down / mapping missing — old container)."""
    if not sandbox_available():
        return None
    result = subprocess.run(
        ["docker", "port", container_name(topic_id), str(APP_PORT)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # e.g. "127.0.0.1:55007" (possibly one line per address family).
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    port = line.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}" if port.isdigit() else None


def container_name(topic_id: uuid.UUID) -> str:
    """Deterministic name of a topic's long-lived SDK sandbox container."""
    return f"cheesex-sbx-{topic_id.hex[:12]}"


def tmux_container_name(topic_id: uuid.UUID) -> str:
    """Deterministic name of a topic's long-lived tmux-backend container — distinct
    from the SDK one so the two backends never collide. Lives here (the shared
    workspace layer) so the accept/archive reaper can free it WITHOUT importing the
    provider; TmuxHooksProvider references this as its single source of truth."""
    return f"cheesex-tmux-{topic_id.hex[:12]}"


def stop_topic_container(topic_id: uuid.UUID) -> None:
    """Remove a topic's long-lived sandbox container(s) — BOTH the SDK and tmux
    backends' boxes — e.g. when the topic is merged/archived or its worktree is
    recreated. Best-effort: a missing container is fine. Freeing BOTH matters
    because a topic may have run on either backend and each leaves its own box;
    reaping only the SDK one (the old behavior) leaked every tmux container forever."""
    if not sandbox_available():
        return
    for name in (container_name(topic_id), tmux_container_name(topic_id)):
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
        )


def list_sandbox_containers() -> list[str]:
    """Names of all live cheesex sandbox containers (both backends' labels)."""
    if not sandbox_available():
        return []
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "label=cheesex-sandbox=1",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def remove_container(name: str) -> None:
    """Remove ONE container by exact name (the idle reaper's primitive).
    Best-effort; a missing container is fine."""
    if not sandbox_available():
        return
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


GATE_TAIL_CHARS = 4000


def run_check_command(
    cwd: Path,
    command: str,
    *,
    timeout: int = 600,
    log_path: Path | None = None,
) -> dict:
    """机器闸门 (spec §4.4/§9, eval C2): run the project's operator-configured
    check command in the given worktree. Runs on the HOST — same trust model as
    the on-dogfood-push hook: the command comes from Project.settings (set by
    the project's people), never from AI output; and real check commands
    (uv/pytest/npm) need the host toolchain + network, which exec_in_sandbox's
    network-less slim image deliberately lacks.

    Full output goes to `log_path` (timestamped header + combined stdout/stderr);
    the returned `tail` is a bounded summary for the card / nudge message."""
    from datetime import UTC, datetime

    try:
        result = subprocess.run(  # noqa: S602 — operator-trusted project config
            ["sh", "-lc", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        exit_code = result.returncode
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        raw = exc.stdout
        partial = (
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, bytes)
            else (raw or "")
        )
        output = f"{partial}\n检查超时（>{timeout}s），已中止。"
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"[{datetime.now(UTC).isoformat()}] $ {command}\n"
            f"(cwd: {cwd}, exit: {exit_code})\n"
        )
        log_path.write_text(header + output, encoding="utf-8")
    return {"exit_code": exit_code, "tail": output[-GATE_TAIL_CHARS:]}


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
