"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

每个 project = 一个 git 仓,主仓用 Jujutsu (jj) colocate(`.git` + `.jj` 并存),
每个话题 = 一个 jj workspace(取代 git worktree)。这样沙箱容器里的原生
Bash/Write/Edit 改动会被 jj 自动快照(无需手动 commit),而 git 侧照常工作:
话题分支用 jj bookmark 导出成 git branch,采纳/diff 仍走 git(colocation)。
"""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.domain.workspace.dogfood_notices import watch_dogfood_push
from app.domain.workspace.textfile import (
    MAX_TEXT_BYTES,
    content_version,
    decode_text,
    looks_binary,
)

DEFAULT_BRANCH = "main"
# 沙箱镜像：芝士分身在容器里跑代码/测试，碰不到宿主机 (spec §9.1).
SANDBOX_IMAGE = "python:3.12-slim"

# The ONE uid/gid that may touch a project's jj store.
#
# `sandbox_vcs_mounts` below bind-mounts the project's main-repo `.jj`/`.git`
# into every sandbox container read-write, so the backend process and the
# in-container agent operate on the same store. jj creates its store objects
# (op_store/, index/, store/extra/, workspace_store/index, working_copy/*) and
# `.jj/repo/config-id` with a hardcoded 0600 — a tempfile that gets persisted,
# NOT `0666 & ~umask` — so the first writer owns the store and every later jj
# command from the other uid dies with "Failed to determine the secure config
# for a repo … Permission denied". No umask, shared group, or default ACL can
# widen a mode the writer sets explicitly; the only fix is for both sides to be
# the same uid.
#
# 1000 = `node` in the sandbox image (node:22 + USER node, started with
# `--user node`), which is the side we do not fully control — a project can
# point `sandbox_image` at any other node-based image. The backend image is
# built to match (backend/Dockerfile); tests/unit/test_workspace_uid_alignment.py
# pins all three together.
AGENT_UID = 1000
AGENT_GID = 1000

logger = logging.getLogger("cheesex.workspace")


class GitTimeoutError(ValidationError):
    """A git subprocess ran past its timeout. Only ever raised AFTER the
    process is confirmed dead (SIGTERM, escalating to SIGKILL, then waited
    on) — never while it might still be running, so callers can safely clean
    up whatever it was holding (worktree, lock file)."""


# Grace periods for the terminate→kill escalation below — generous enough for
# git to unwind a real operation cleanly on SIGTERM, short enough that a
# genuinely stuck process doesn't stall the caller for long.
_TERMINATE_GRACE_S = 5.0
_KILL_GRACE_S = 5.0


def _terminate_confirmed(proc: subprocess.Popen) -> None:
    """Escalate a timed-out subprocess to a CONFIRMED exit. SIGTERM first (lets
    git release its lock on a clean unwind); SIGKILL only if it ignores that;
    then wait for the exit to actually be observed. Never assume — a caller
    that deletes a lock file out from under a still-running process is exactly
    the failure mode this exists to avoid. Reads the module-level grace
    periods at call time (not as bound defaults) so tests can shrink them via
    monkeypatch instead of waiting out the real 5s/5s in production."""
    proc.terminate()
    try:
        proc.wait(timeout=_TERMINATE_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired as exc:
        # Genuinely stuck (e.g. uninterruptible I/O wait) — surface it rather
        # than silently proceeding as if the process were gone.
        raise ValidationError(f"子进程 pid={proc.pid} 在 SIGKILL 后仍未退出") from exc


def _clear_own_lock(cwd: Path) -> None:
    """Drop `.git/index.lock` left by a subprocess we JUST confirmed dead (via
    _terminate_confirmed) — safe because we killed it ourselves and waited for
    the exit, not because we're guessing at some other process's liveness. A
    no-op for a linked worktree (`.git` there is a file, not a directory):
    its whole admin dir is disposed of by the caller instead."""
    git_dir = cwd / ".git"
    if not git_dir.is_dir():
        return
    try:
        (git_dir / "index.lock").unlink()
    except OSError:
        pass


def _run_subprocess(
    argv: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout that actually cleans up: on expiry, kill
    the process (confirmed), clear any lock it held, then raise
    GitTimeoutError — instead of letting subprocess.TimeoutExpired escape
    unhandled (the previous behavior) with the process's fate unknown."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_confirmed(proc)
        _clear_own_lock(cwd)
        raise GitTimeoutError(
            f"{argv[0] if argv else '?'} 超时（>{timeout}s），子进程已确认终止"
        ) from exc
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _repo(project_id: uuid.UUID) -> Path:
    return (Path(settings.workspace_root) / str(project_id)).resolve()


def branch_for_topic(topic_id: uuid.UUID) -> str:
    """话题 = git 分支 (spec §6.3). Deterministic from the topic id."""
    return f"topic/{topic_id.hex[:8]}"


def _git(
    repo: Path, *args: str, timeout: int = 20, env: dict[str, str] | None = None
) -> str:
    result = _run_subprocess(["git", *args], repo, timeout, env)
    if result.returncode != 0:
        # git reports merge conflicts on stdout with an empty stderr — fall back
        # so the caller's error isn't blank.
        detail = result.stderr.strip() or result.stdout.strip()
        # The main repo's `.git` is shared with the sandbox exactly like `.jj`
        # (sandbox_vcs_mounts mounts both), so it can fail the same way.
        if _permission_denied(detail):
            raise WorkspacePermissionError(_uid_split_hint(f"git {args[0]}: {detail}"))
        raise ValidationError(f"git {args[0]} failed: {detail}")
    return result.stdout


class WorkspacePermissionError(ValidationError):
    """A workspace operation failed because the on-disk repo is owned by another
    uid. Distinct from a generic ValidationError so the failure names its own
    cause: this is the shape a uid split takes, and it used to reach the file
    panel as a bare `jj diff failed: Internal error…` — indistinguishable from
    "the file is missing", which is why it went undiagnosed for a whole project.
    """


# jj reports an unreadable store as an internal error whose *cause* is the EACCES
# — match the OS error rather than the wording of any one jj message.
_PERMISSION_SIGNS = ("Permission denied", "os error 13", "Operation not permitted")


def _permission_denied(text: str) -> bool:
    return any(sign in text for sign in _PERMISSION_SIGNS)


def _uid_split_hint(detail: str) -> str:
    return (
        f"工作区仓库里有当前进程（uid={os.getuid()}）无权访问的文件。"
        f"后端与沙箱容器必须跑在同一个 uid（应为 {AGENT_UID}）——"
        f"jj 的 store 文件是 0600，uid 不一致时先写的一方会把另一方锁死。"
        f"原始报错：{detail}"
    )


def _jj_store(repo: Path) -> Path:
    """The jj store a repo's `.jj` points at: itself for a main repo (where
    `.jj/repo` is the store directory), or the shared main repo's store for a
    workspace (where `.jj/repo` is a file holding a relative path to it)."""
    pointer = repo / ".jj" / "repo"
    if pointer.is_file():
        return Path(os.path.normpath(repo / ".jj" / pointer.read_text().strip()))
    return pointer


def _repair_modes(root: Path, since: float) -> None:
    """Add read bits under `root`, stat'ing FILES only in directories written at
    or after `since`. jj ADDS metadata files rather than rewriting them in
    place, so an untouched directory cannot hold a file whose mode needs fixing.
    That prune is what keeps this off the hot path: at real repo size (17
    directories, 6.7k files) stat'ing every file costs 66ms per jj call and
    grows with the op log forever, against 10ms to walk the tree. `since=0`
    repairs everything."""
    try:
        st = os.stat(root)
        os.chmod(root, st.st_mode | 0o555)
        entries = list(os.scandir(root))
    except OSError:
        return
    fix_files = st.st_mtime >= since
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            _repair_modes(Path(entry.path), since)
        elif fix_files:
            try:
                os.chmod(entry.path, entry.stat().st_mode | 0o444)
            except OSError:
                pass


def _share_jj_modes(repo: Path, since: float = 0.0) -> None:
    """jj (0.42) creates its metadata with a hardcoded 0600 — it ignores umask,
    so no default ACL or umask setting on the host can prevent this. Every call
    therefore leaves files the sandbox's user (a DIFFERENT uid, sharing no
    group) cannot read, and `jj log` inside a sandbox dies with "Permission
    denied" on the newest operation. `_make_world_writable` only runs when a
    worktree is created, so it never covers what LATER calls write.

    Repair both trees a call can touch: the repo's own `.jj`, and the shared
    store it points at — a workspace's operations land in the MAIN repo's
    op_store, not its own `.jj`.

    Read-only (plus the store root, which jj needs to write a temp file into to
    resolve its "secure config"). Deliberately NOT write on op_store/: a sandbox
    reads history with `--ignore-working-copy`; granting it op-log writes would
    let one topic's agent corrupt every other topic's history."""
    store = _jj_store(repo)
    own = repo / ".jj"
    # A main repo's store lives INSIDE its own .jj; a workspace's is elsewhere —
    # and then the `.jj` ABOVE that store needs its exec bit too, or the sandbox
    # cannot traverse into the store it mounts.
    roots = [own] if store.is_relative_to(own) else [own, store.parent]
    for root in roots:
        _repair_modes(root, since)
    try:  # jj writes `<store>/.tmpXXXX` before every command it runs
        os.chmod(store, os.stat(store).st_mode | 0o777)
    except OSError:
        pass


JJ_USER_NAME = "芝士"
JJ_USER_EMAIL = "cheese@zhishi.local"


def _drop_repo_config_id(store: Path) -> None:
    """Delete the store's `config-id` if one exists — the file that took every
    topic on the platform down at 11:39.

    The mode repair above cannot cover this one, because the problem is not the
    mode: jj's per-repo config does NOT live in the repo. `config-id` holds a
    20-hex id naming a directory under the CALLER's `~/.config/jj/repos/`. Run
    jj as a user whose home has no entry for that id — every fresh sandbox
    container — and jj rewrites `config-id` via tmp+rename, so it returns owned
    by whoever ran jj, mode 0600. The backend (uid 1001) then cannot read a file
    the sandbox (uid 1000) owns, `_repair_modes`' chmod raises EPERM and is
    swallowed, and EVERY jj call dies with "Internal error: Failed to determine
    the secure config for a repo" — `jj workspace add` included, so no topic can
    start at all.

    Repairing modes can never win that race: chmod requires being the file's
    owner, and jj replaces the inode on the next rewrite regardless. Deleting
    can:

    - it is possible — unlink needs write permission on the DIRECTORY (which the
      backend owns), not on the file;
    - it is safe — the file is not merely regenerable, it is optional. With it
      absent jj runs normally and does not recreate it; only `jj config set
      --repo` does. It holds no repo data (that is store/, op_store/, index/,
      none of which this touches), only a binding to a per-user config dir. The
      one thing that binding provided — the 芝士 identity — comes from
      JJ_USER/JJ_EMAIL below instead, which is why `_ensure_jj` no longer sets
      per-repo config.
    """
    try:
        (store / "config-id").unlink()
    except OSError:
        pass  # absent (the normal case), or a store we cannot write — _jj reports it


_SECURE_CONFIG_FAILURE = "failed to determine the secure config"


def _jj_failure_message(command: str, detail: str, repo: Path) -> str:
    """Name the real cause. This failure is a file permission problem in the
    workspace, but it reaches the user through a generic wrapper that renders it
    as 「AI 服务返回错误」 — which sent people looking at the model provider for
    what is a chmod."""
    if _SECURE_CONFIG_FAILURE in detail.lower():
        return (
            f"工作区版本库权限异常：{_jj_store(repo) / 'config-id'} "
            "的属主不是后端进程，既读不了、也删不掉（删除需要它所在目录的写权限）。"
            "这不是 AI 服务故障。修法：删掉该文件即可——它是指向 per-user 配置目录的"
            f"索引，可再生，不含任何版本历史。原始报错：{detail}"
        )
    return f"jj {command} failed: {detail}"


def _jj(repo: Path, *args: str) -> str:
    started = time.time() - 1  # -1s: filesystem mtime vs clock granularity slack
    # Before every call, not once at setup: a jj run by any other uid (an agent
    # in a sandbox) recreates config-id and locks the backend out mid-flight.
    _drop_repo_config_id(_jj_store(repo))
    result = subprocess.run(
        ["jj", "--no-pager", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        # Identity per call rather than as per-repo config: setting it with
        # `--repo` is the one thing that creates config-id in the first place.
        env={**os.environ, "JJ_USER": JJ_USER_NAME, "JJ_EMAIL": JJ_USER_EMAIL},
    )
    # Before the returncode check: a FAILED jj call still writes operations, and
    # those unreadable files break the sandbox just as thoroughly.
    _share_jj_modes(repo, since=started)
    if result.returncode != 0:
        detail = result.stderr.strip()
        # config-id has its own remedy (dropped before every call, and named by
        # _jj_failure_message when even deleting fails) — leave that path alone.
        # Every OTHER permission failure in the shared store is the uid split.
        if _permission_denied(detail) and _SECURE_CONFIG_FAILURE not in detail.lower():
            raise WorkspacePermissionError(_uid_split_hint(f"jj {args[0]}: {detail}"))
        raise ValidationError(_jj_failure_message(args[0], detail, repo))
    return result.stdout


def _ensure_jj(repo: Path) -> None:
    """Colocate a jj repo onto the git repo (idempotent). jj then auto-snapshots
    the working copy, while git refs stay live for merge/diff."""
    if (repo / ".jj").exists():
        return
    _jj(repo, "git", "init", "--colocate")
    # Identity is injected per call (JJ_USER/JJ_EMAIL in _jj), NOT written as
    # per-repo config: `jj config set --repo` is what creates `config-id`, the
    # cross-uid tripwire _drop_repo_config_id exists to keep out of the store.


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


# Container path a topic's worktree is bind-mounted to (tmux_provider.py,
# exec_in_sandbox below) — the anchor `sandbox_vcs_mounts` resolves against.
SANDBOX_WORKDIR = "/work"


def sandbox_vcs_mounts(
    project_id: uuid.UUID, branch: str, *, container_workdir: str = SANDBOX_WORKDIR
) -> list[str]:
    """Extra `docker run -v` args so a topic's jj workspace resolves inside its
    sandbox container.

    `jj workspace add` (_ensure_worktree above) writes `<worktree>/.jj/repo` as
    a path *relative to the real host directory nesting* between the worktree
    (workspace_root/.worktrees/<project>/<branch>) and the project's shared
    main repo (workspace_root/<project>) — e.g. `../../../../<project_id>/.jj
    /repo`. A sandbox container only ever gets the worktree, remapped to
    SANDBOX_WORKDIR (much shallower than the host tree), so that relative
    pointer walks off the container's root instead of reaching the real store
    — `jj status` inside the sandbox fails with "Cannot access ../../../../
    <project_id>/.jj/repo: No such file or directory".

    Compute, with the exact same relpath jj used, where that unmodified
    pointer will resolve to once anchored at SANDBOX_WORKDIR instead of the
    real worktree path, and mount the main repo's `.jj` (commit/op store) and
    `.git` (colocated git dir — the store's own internal git_target is itself
    a relative pointer into it) there. Host-native access to the worktree
    (backend catch-up/diff/log, outside any container) is untouched — only the
    container's extra mounts change."""
    main = _repo(project_id)
    wt = _worktree_path(project_id, branch)
    # Full (since=0) repair right before the store is handed to a container:
    # _jj's per-call repair only covers what THAT call wrote, so a repo whose
    # metadata predates this behaviour — every repo already on disk — would stay
    # unreadable forever. Launching a sandbox is the moment it has to be right,
    # and once per container start the cost does not matter.
    _share_jj_modes(wt)
    rel_to_store = os.path.relpath(main / ".jj" / "repo", wt / ".jj")
    store_in_container = Path(
        os.path.normpath(os.path.join(container_workdir, ".jj", rel_to_store))
    )
    main_in_container = store_in_container.parents[1]  # strip "/.jj/repo"
    return [
        "-v",
        f"{main / '.jj'}:{main_in_container / '.jj'}",
        "-v",
        f"{main / '.git'}:{main_in_container / '.git'}",
    ]


def audit_workspace_ownership() -> list[str]:
    """Boot-time check that this process can actually use the workspace it was
    handed — one problem string per finding, empty when healthy.

    A uid split does not announce itself: the backend keeps booting and only the
    file panel dies, project-wide, with a 422 that reads like "file not found".
    So state it at startup instead. Deliberately cheap (the project dirs plus
    each store's `config-id`, not a walk of the whole store): `config-id` is the
    one file whose unreadability fails EVERY jj command, so it is both the
    likeliest and the most damaging finding.

    Non-fatal by design — one stray file must not keep the platform from
    booting, and an operator who sees this in the log has the fix in hand
    (deploy/fix-workspace-ownership.sh).
    """
    problems: list[str] = []
    root = Path(settings.workspace_root)
    if not root.exists():
        return problems
    me = os.getuid()
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        problems.append(
            f"{root} 当前进程（uid={me}）不可读写（属主 uid={root.stat().st_uid}）"
        )
    for entry in sorted(root.iterdir()):
        config_id = entry / ".jj" / "repo" / "config-id"
        try:
            if not config_id.is_file() or os.access(config_id, os.R_OK):
                continue
            owner = config_id.stat().st_uid
        except OSError:
            continue
        problems.append(
            f"{config_id} 属主 uid={owner}，当前进程 uid={me} 读不了"
            f"——该项目的所有 jj 操作都会失败"
        )
    return problems


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
    branch = branch_for_topic(topic_id)
    wt = _ensure_worktree(project_id, branch)
    _catch_up_with_branch(project_id, wt, branch)
    return wt


def _catch_up_with_branch(project_id: uuid.UUID, wt: Path, branch: str) -> None:
    """Materialise commits that reached the branch without going through here.

    A machine that owns its tree pushes straight to the ref. The workspace is the
    thing we read files out of, and nothing moves it, so work that landed was
    invisible in the file tree even though the branch had it — the push looked
    like it had done nothing.

    Only ever a fast-forward, and only when this workspace has nothing pending:
    a human's uncommitted edit here must never be swept aside by a machine's
    push. When both sides have moved, the workspace wins and stays put — its
    changes are the ones a person is looking at.
    """
    repo = ensure_repo(project_id)
    try:
        tip = _git(repo, "rev-parse", branch).strip()
    except ValidationError:
        return  # branch not created yet — nothing to catch up to
    if not tip:
        return
    if _jj(wt, "diff", "-s").strip():
        return  # pending local edits: leave them alone
    current = _jj(wt, "log", "-r", "@-", "--no-graph", "-T", "commit_id").strip()
    if current == tip:
        return
    # Fast-forward only: move only when the workspace has nothing the branch lacks.
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", current, tip],
        cwd=repo,
        capture_output=True,
        timeout=20,
    )
    if current and ancestor.returncode != 0:
        return
    _jj(wt, "git", "import")
    _jj(wt, "new", branch)


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
            # lstat, not stat: a symlink pointing at something that no longer
            # exists is an ordinary thing to find in a worktree, and stat() on it
            # raised FileNotFoundError — one dangling link took the whole file
            # list down with a 500. It is listed, at the link's own size.
            try:
                size = p.lstat().st_size
            except OSError:
                size = 0
            files.append({"path": str(rel), "bytes": size})
    files.sort(key=lambda f: f["path"])
    return files


def read_file(
    project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
) -> str:
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    if not target.is_file():
        raise ValidationError("file not found")
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except PermissionError as exc:
        raise WorkspacePermissionError(_uid_split_hint(f"读 {path}: {exc}")) from exc


def read_text_file(
    project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
) -> dict:
    """Read a worktree file *for editing* — the shape the 文件 panel needs.

    Unlike :func:`read_file` this never pretends binary is text. ``content`` is
    None when the file cannot be edited safely (``binary``) or is too big to send
    at all (``too_large``); the panel renders a read-only view for those instead
    of loading mangled bytes into Monaco and offering a 保存 button. ``version``
    is the token to echo back on save so a lost race is caught (see
    :mod:`app.domain.workspace.textfile`).
    """
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    if not target.is_file():
        raise ValidationError("file not found")
    size = target.stat().st_size
    meta = {"path": path, "bytes": size, "binary": False, "too_large": False}
    if size > MAX_TEXT_BYTES:
        # Deliberately not read: the point is to not build the giant body.
        return {**meta, "content": None, "version": None, "too_large": True}
    data = target.read_bytes()
    text = decode_text(data)
    if text is None:
        return {
            **meta,
            "content": None,
            "version": content_version(data),
            "binary": True,
        }
    return {**meta, "content": text, "version": content_version(data)}


def write_file(
    project_id: uuid.UUID,
    path: str,
    content: str,
    topic_id: uuid.UUID | None = None,
    expected_version: str | None = None,
) -> str:
    """Write a file in the topic's worktree (人改文件即指令 — the agent reads the
    latest on its next turn, like 改文档即指令). _safe_path guards traversal + .git.

    Refuses to overwrite a file that is not text: the only way to reach here with
    a binary target is a client that decoded it lossily, and writing the result
    back destroys the original.

    ``expected_version`` is the version the caller last read. When given, a write
    whose target has changed since is rejected with a conflict rather than
    winning silently — the human's 保存 used to erase 芝士's edits with no hint
    that anything was lost. Callers that legitimately have no read to base a
    write on (the agent writing its own output) omit it and still write through.

    Returns the new version, so a client can keep saving without a re-read.
    """
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    current = target.read_bytes() if target.is_file() else None
    if current is not None and looks_binary(current):
        raise ValidationError("这是二进制文件，不能以文本保存")
    if expected_version is not None:
        actual = content_version(current) if current is not None else None
        if actual != expected_version:
            raise ConflictError(
                "文件已被改动（芝士或其他人写过），你的版本是基于旧内容的",
                data={"path": path, "version": actual},
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    try:
        target.write_bytes(data)
    except PermissionError as exc:
        # 人在文件面板保存芝士刚建的文件时，这里曾经是一个未捕获的 OSError → 500.
        raise WorkspacePermissionError(_uid_split_hint(f"写 {path}: {exc}")) from exc
    return content_version(data)


def read_file_bytes(
    project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
) -> bytes:
    """Raw bytes of a worktree file (binary-safe — images/attachments; the text
    reader would mangle them). Same traversal guard as read_file."""
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    if not target.is_file():
        raise ValidationError("file not found")
    try:
        return target.read_bytes()
    except PermissionError as exc:
        raise WorkspacePermissionError(_uid_split_hint(f"读 {path}: {exc}")) from exc


def write_file_bytes(
    project_id: uuid.UUID, path: str, data: bytes, topic_id: uuid.UUID | None = None
) -> None:
    """Binary-safe write into the topic's worktree (聊天图片等附件落盘 — 进版本库，
    文件面板可见，沙箱里芝士可直接 Read). Same guards as write_file."""
    tree = _tree(project_id, topic_id)
    target = _safe_path(tree, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_bytes(data)
    except PermissionError as exc:
        raise WorkspacePermissionError(_uid_split_hint(f"写 {path}: {exc}")) from exc


def git_log(
    project_id: uuid.UUID, limit: int = 50, topic_id: uuid.UUID | None = None
) -> list[dict]:
    """Commit history. With ``topic_id``: THIS topic's own commits (its branch
    minus the base), which is what the 话题 Git panel asks about.

    Project-level history was the wrong answer twice over: before 采纳 the
    topic's commits live only on its branch, so the panel showed none of them;
    after 采纳 the base is full of OTHER topics' commits, so it showed those.
    Once a topic is merged its own range is empty again — correctly, since the
    commits are the base's now — and the panel says so rather than borrowing
    someone else's history.
    """
    repo = ensure_repo(project_id)
    # A fresh repo has no commits yet — `git log` would exit non-zero. Return an
    # empty history instead of erroring.
    if not _git(repo, "rev-list", "-n", "1", "--all").strip():
        return []
    if topic_id is not None:
        branch = branch_for_topic(topic_id)
        if not _branch_exists(repo, branch):
            return []
        base = _base_branch(repo)
        if branch == base:
            return []
        out = _git(
            repo,
            "log",
            f"-{limit}",
            "--pretty=format:%h\t%an\t%s",
            f"{base}..{branch}",
        )
    else:
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


def _merge_worktree_path(project_id: uuid.UUID) -> Path:
    """Root for throwaway merge worktrees — sibling to (never colliding with)
    the topic worktrees under `.worktrees/{project}/`, which are all named
    after a sanitized branch (`topic_xxxxxxxx`) and never `_merge`."""
    return Path(settings.workspace_root) / ".worktrees" / str(project_id) / "_merge"


# A merge worktree left on disk this long was abandoned by a process that
# never got to run its own `finally` cleanup (2026-08-09 incident, round 2:
# a `docker compose up` redeploy SIGKILLed the backend mid-accept, so nothing
# was left running to remove it) — never by an operation still in flight, since
# a single merge/checkout here finishes in well under a second. Purely disk/
# `.git/worktrees` registry hygiene: each attempt uses a fresh uuid, so debris
# from a killed run is never revisited and can never wedge a future one — it
# just sits there forever unless swept.
_ORPHANED_MERGE_WORKTREE_AFTER_S = 3600.0


def _reap_orphaned_merge_worktrees(repo: Path, project_id: uuid.UUID) -> None:
    root = _merge_worktree_path(project_id)
    if not root.is_dir():
        return
    now = time.time()
    for entry in root.iterdir():
        try:
            age_s = now - entry.stat().st_mtime
        except OSError:
            continue
        if age_s < _ORPHANED_MERGE_WORKTREE_AFTER_S:
            continue
        logger.warning(
            "reaping orphaned merge worktree %s (age=%.0fs) — a prior run "
            "never got to clean it up",
            entry,
            age_s,
        )
        _discard_worktree(repo, entry)


def _discard_worktree(repo: Path, wt: Path) -> None:
    """Best-effort teardown of a throwaway merge worktree. `git worktree
    remove` also frees the linked admin dir under `.git/worktrees/<name>/`
    (where that worktree's OWN index/MERGE_HEAD/lock live — never the shared
    repo's own `.git/index.lock`, so a stuck merge here can never wedge
    another operation). If remove itself fails (e.g. its own subprocess
    timed out — already confirmed dead by `_run_subprocess` by this point),
    fall back to deleting the checkout directory and pruning the now-dangling
    admin entry."""
    try:
        _git(repo, "worktree", "remove", "--force", str(wt))
        return
    except ValidationError:
        pass
    shutil.rmtree(wt, ignore_errors=True)
    try:
        _git(repo, "worktree", "prune")
    except ValidationError:
        pass


@contextlib.contextmanager
def _isolated_worktree(project_id: uuid.UUID, repo: Path, ref: str) -> Iterator[Path]:
    """A throwaway git worktree, detached at `ref`'s current commit, for git
    operations that must not contend with anything else sharing the project's
    main repo directory (2026-08-09 incident: a merge killed mid-flight left
    `.git/index.lock` in that ONE shared directory, jamming every accept on
    the project until a human deleted it by hand). `--detach` (rather than
    checking out `ref` by branch name) matters even outside any timeout: git
    refuses to check out the same branch in two worktrees at once, which a
    concurrent accept on the same base branch would otherwise hit immediately.
    Always removed on the way out, success or failure."""
    wt = (_merge_worktree_path(project_id) / uuid.uuid4().hex).resolve()
    wt.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(repo, "worktree", "add", "--detach", "-q", str(wt), ref)
    except ValidationError:
        _discard_worktree(repo, wt)
        raise
    try:
        yield wt
    finally:
        _discard_worktree(repo, wt)


def _lock_held_by_a_live_process(lock: Path) -> bool:
    """Best-effort: is any process on this host holding `lock` open right
    now? Scans `/proc/*/fd` (Linux; this backend always runs there) instead
    of shelling out to `lsof`, which a slim container image often lacks.
    Fails OPEN (assumes held) on anything unreadable — staleness must never
    be inferred from a probe that couldn't actually see the answer."""
    proc = Path("/proc")
    if not proc.is_dir():
        return True
    try:
        target = str(lock.resolve())
    except OSError:
        return True
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            fds = list((pid_dir / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(fd) == target:
                    return True
            except OSError:
                continue
    return False


def _is_lock_stale(lock: Path, age_threshold_s: float) -> bool:
    try:
        age_s = time.time() - lock.stat().st_mtime
    except OSError:
        return False  # already gone — nothing to clear
    if age_s < age_threshold_s:
        return False  # could still be a legitimate, just-slow operation
    return not _lock_held_by_a_live_process(lock)


# checkout+reset here is plumbing, not a merge — a real one finishes in well
# under a second, so a lock this old was abandoned, not merely slow.
_SHARED_LOCK_STALE_AFTER_S = 60.0
_SHARED_CHECKOUT_ATTEMPTS = 5
_SHARED_CHECKOUT_RETRY_DELAY_S = 0.2


def _sync_shared_checkout(repo: Path, base: str, sha: str) -> None:
    """The ONE piece of a merge that must still touch the shared repo
    directory: point its own working tree at the new base tip, since
    list_files/read_file/exec_in_sandbox (topic_id=None) and the sandbox
    bind-mount all read straight off these files. Deliberately just a
    fast-forward reset — no merge algorithm, no conflict possible — to keep
    the shared directory's exposure to a hang as small as this fix can make
    it.

    2026-08-09 incident, round 2: even with the merge itself isolated, the
    backend process running THIS step can still be SIGKILLed mid-checkout by
    a redeploy (observed live: a `docker compose up` mid-flight left a
    zero-byte `.git/index.lock` here that then wedged every later accept on
    the project until a human deleted it). Unlike `_run_subprocess`'s own
    timeout path (which clears a lock only after confirming — via wait() —
    that ITS OWN subprocess died), there's no process handle to confirm
    against here: it was someone else's process, killed before we ever ran.
    So staleness is inferred instead — old enough that no legitimate
    checkout/reset could still be running it, AND not currently held open by
    any process on the host — and only then cleared, with a retry loop for
    the (much more likely) case of two accepts landing here within
    milliseconds of each other, which needs a brief wait, not a lock clear."""
    last_exc: ValidationError | None = None
    for attempt in range(_SHARED_CHECKOUT_ATTEMPTS):
        lock = repo / ".git" / "index.lock"
        if lock.exists():
            try:
                mtime = lock.stat().st_mtime
            except OSError:
                mtime = None
            if mtime is not None and _is_lock_stale(lock, _SHARED_LOCK_STALE_AFTER_S):
                logger.warning(
                    "clearing stale %s (mtime=%s, age=%.0fs) — no live "
                    "process holds it; a prior operation was killed before "
                    "it could finish",
                    lock,
                    mtime,
                    time.time() - mtime,
                )
                try:
                    lock.unlink()
                except OSError:
                    pass
        try:
            # `--force` is what makes the "no conflict possible" above true.
            # Without it `git checkout` REFUSES whenever the shared tree holds a
            # local modification to a file that differs between the current HEAD
            # and `base` ("Your local changes ... would be overwritten by
            # checkout ... Aborting"), which is precisely the state this function
            # exists to clean up. The `reset --hard` on the very next line
            # discards those modifications anyway, so refusing protects nothing —
            # it only wedges the sync. Observed 2026-08-11: an accept failed here
            # with a file list spanning several unrelated topics, and because
            # this runs AFTER the ref move (see the caller) the merge had already
            # landed while the user was told "采纳未完成：合并出错".
            _git(repo, "checkout", "-q", "--force", base)
            _git(repo, "reset", "-q", "--hard", sha)
            return
        except ValidationError as exc:
            last_exc = exc
            if attempt + 1 < _SHARED_CHECKOUT_ATTEMPTS:
                time.sleep(_SHARED_CHECKOUT_RETRY_DELAY_S)
    assert last_exc is not None
    raise last_exc


_MERGE_RETRY_LIMIT = 5


def _merge_ref_into_base(
    project_id: uuid.UUID,
    repo: Path,
    base: str,
    merge_ref: str,
    message: str,
    *,
    allow_unrelated_histories: bool = False,
) -> dict:
    """Merge `merge_ref` into `base` without ever running the merge itself in
    the project's shared working directory. The merge happens in a throwaway
    detached worktree (own index, own lock, discarded whole regardless of
    outcome); only a fast, always-conflict-free ref move + working-tree sync
    touches the shared repo. That ref move is a compare-and-swap
    (`update-ref old new`) — if another accept landed on `base` in the
    meantime, this retries against the new tip rather than clobbering it or
    silently merging on top of a stale base.

    Never raises for an ordinary merge failure (conflict, or retries
    exhausted) — always returns a dict with a `merged` key, same contract as
    the old direct implementation. Genuine git failures (bad ref, etc.) still
    raise ValidationError."""
    _reap_orphaned_merge_worktrees(repo, project_id)
    for _attempt in range(_MERGE_RETRY_LIMIT):
        old_sha = _git(repo, "rev-parse", base).strip()
        with _isolated_worktree(project_id, repo, old_sha) as wt:
            args = [
                "-c",
                "user.name=芝士",
                "-c",
                "user.email=cheese@zhishi.local",
                "merge",
                "--no-ff",
                "-q",
            ]
            if allow_unrelated_histories:
                args.append("--allow-unrelated-histories")
            args += ["-m", message, merge_ref]
            try:
                _git(wt, *args)
            except ValidationError as exc:
                try:
                    conflicts = (
                        _git(wt, "diff", "--name-only", "--diff-filter=U")
                        .strip()
                        .splitlines()
                    )
                except ValidationError:
                    conflicts = []
                try:
                    _git(wt, "merge", "--abort")
                except ValidationError:
                    pass
                reason = (
                    "合并冲突：" + "、".join(conflicts[:20]) if conflicts else str(exc)
                )
                return {"merged": False, "reason": reason, "conflicts": conflicts}
            new_sha = _git(wt, "rev-parse", "HEAD").strip()
        try:
            _git(repo, "update-ref", f"refs/heads/{base}", new_sha, old_sha)
        except ValidationError:
            continue  # base moved concurrently (another accept landed) — retry
        # Past this point the merge is DURABLE: the CAS above already advanced
        # `base` to new_sha. Syncing the shared working tree is housekeeping for
        # the readers of that directory (list_files/read_file/exec_in_sandbox
        # with topic_id=None, and the sandbox bind-mount) — a failure there
        # leaves them reading stale files, which is worth shouting about, but it
        # is NOT a failed merge. Letting it raise told the user "采纳未完成：
        # 合并出错" about work that was already on the base branch, and invited a
        # re-accept of an already-merged topic (observed 2026-08-11).
        try:
            _sync_shared_checkout(repo, base, new_sha)
        except ValidationError as exc:
            logger.exception(
                "merge landed (%s -> %s) but the shared checkout could not be "
                "synced; the shared directory is stale until the next accept "
                "or sync touches it",
                base,
                new_sha,
            )
            return {
                "merged": True,
                "branch": merge_ref,
                "into": base,
                "sync_failed": str(exc),
            }
        return {"merged": True, "branch": merge_ref, "into": base}
    return {
        "merged": False,
        "reason": f"合并失败：{base} 分支并发更新冲突过多，请重试",
    }


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
        return {"merged": False, "noop": True, "reason": "no topic branch"}
    base = _base_branch(repo)
    if branch == base:
        return {
            "merged": False,
            "noop": True,
            "reason": "topic is the base branch",
        }
    return _merge_ref_into_base(
        project_id, repo, base, branch, f"采纳 {branch} → {base}"
    )


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
    result = _merge_ref_into_base(
        project_id,
        repo,
        base,
        ref,
        f"同步上游 {ref} → {base}",
        allow_unrelated_histories=True,
    )
    if not result["merged"]:
        return {
            "synced": False,
            "reason": result["reason"],
            "conflicts": result.get("conflicts", []),
        }
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


def prepare_upstream_conflict_resolution(
    project_id: uuid.UUID, topic_id: uuid.UUID
) -> list[str]:
    """同步上游冲突 → 派芝士解决的前置。Same contract as
    `prepare_conflict_resolution`, but the side being merged in is the UPSTREAM
    branch rather than a topic branch: the workspace ends up holding a
    base×upstream merge with the conflicts materialized as <<<<<<< markers.

    Why this exists at all: `sync_upstream` aborts cleanly on conflict and
    reports — which is the right thing for the shared repo, but on its own it is
    a dead end. Accepting a topic had an exit (routes/accept.py dispatches 芝士
    at the materialized conflict); syncing did not, so a project whose upstream
    had diverged simply could not pull, and every later sync hit the same wall.

    Accepting the resulting topic finishes the sync: the merge commit carries
    upstream as a parent, so `merge_topic` folding it into base brings the
    upstream history along with the resolution."""
    repo = ensure_repo(project_id)
    # Resolve upstream to a commit id rather than a ref name: jj addresses git
    # remote branches as `main@upstream`, git as `upstream/main`, and a raw sha
    # is unambiguous in both — no name translation to get wrong.
    _git(repo, "fetch", UPSTREAM_REMOTE, timeout=120)
    upstream_sha = _git(repo, "rev-parse", _upstream_ref(repo)).strip()
    base = _base_branch(repo)
    branch = branch_for_topic(topic_id)
    wt = _ensure_worktree(project_id, branch)
    # The workspace's jj view lags the git side — import first, or the merge
    # would run against a stale base (and possibly see no conflict at all).
    try:
        _jj(wt, "git", "import")
    except ValidationError:
        pass
    _jj(wt, "new", base, upstream_sha)
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
        # Take the upstream's new commits FIRST. A fast-forward push is refused
        # whenever the upstream moved since the project was imported — i.e. on any
        # repo with other contributors — and every accept would silently degrade
        # to a side branch (observed live: "remote contains work that you do not
        # have locally"). Syncing here makes landing the normal outcome and keeps
        # the merge semantics identical to 同步上游 (conflicts abort cleanly).
        synced = sync_upstream(project_id)
        if not synced.get("synced"):
            return {
                "pushed": False,
                "mode": "blocked",
                "target": target,
                "reason": f"上游同步失败，未回推：{synced.get('reason', '')}",
            }
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
        # The log is shared across every push-back run for this project (and a
        # re-accept can reuse the same branch name), so grepping it for our
        # branch would risk picking up a stale prior run. Recording the byte
        # offset before we start pins the watcher to exactly this run's output.
        log_offset = log.stat().st_size if log.exists() else 0
        with open(log, "a") as out:
            proc = subprocess.Popen(  # noqa: S603 — operator-trusted local repo hook
                [str(hook), branch],
                cwd=url,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # survives our own redeploy
            )
        hook_started = True
        # Report the eventual result back into the topic timeline without
        # making this call wait for it (accept() must return immediately).
        try:
            asyncio.get_running_loop().create_task(
                watch_dogfood_push(topic_id, proc, log, log_offset, branch)
            )
        except RuntimeError:
            pass  # no running loop (e.g. sync tests/scripts) — nothing to schedule onto
    return {"pushed": True, "mode": "branch", "branch": branch, "hook": hook_started}


def _token_push_env(token: str) -> dict[str, str]:
    """Subprocess env that authenticates one git push with a GitHub token.

    The token travels via env var into an inline credential helper — never argv
    (visible in ps), never disk. The helper list is reset first: the container
    wires a store-file helper through GIT_CONFIG_* (compose), and letting it run
    first would push with the host credential instead of the token's identity.
    """
    helper = (
        "!f() { echo username=x-access-token; "
        'echo "password=$CHEESE_GIT_PUSH_TOKEN"; }; f'
    )
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": helper,
        "CHEESE_GIT_PUSH_TOKEN": token,
    }


def push_topic_branch(project_id: uuid.UUID, topic_id: uuid.UUID, token: str) -> str:
    """Push the topic's branch to the upstream (PR-based accept, #188 §5.1).

    Snapshots the worktree first so the PR head is exactly what the reviewer
    sees. --force-with-lease: a re-push after a conflict fix must move the
    remote branch, but never trample one somebody else moved."""
    repo = ensure_repo(project_id)
    if get_upstream(project_id) is None:
        raise ValidationError("未关联上游仓库，无法推分支")
    try:
        snapshot_worktree(project_id, topic_id, "PR 快照")
    except ValidationError:
        pass  # no workspace/jj state yet — nothing pending to fold
    branch = branch_for_topic(topic_id)
    if not _branch_exists(repo, branch):
        raise ValidationError("话题没有分支，无法推送")
    _git(
        repo,
        "push",
        "--force-with-lease",
        UPSTREAM_REMOTE,
        f"{branch}:{branch}",
        timeout=120,
        env=_token_push_env(token),
    )
    return branch


def pr_base_branch(project_id: uuid.UUID) -> str:
    """两阶段采纳 (PR迭代式): the base branch a topic's PR should target — same
    branch merge_topic() would merge into locally."""
    return _base_branch(ensure_repo(project_id))


def _github_push_url(owner: str, repo: str) -> str:
    """The clone URL a topic's PR branch is pushed to. A seam, not indirection
    for its own sake: the sync-and-retry below has to fetch from the SAME repo
    it pushes to, and tests need both to point at a local repo."""
    return f"https://github.com/{owner}/{repo}.git"


def _is_workflow_permission_rejection(message: str) -> bool:
    """Does this push failure look like GitHub refusing a workflow-file change
    for lack of the `workflows` scope? Verbatim shape:

        ! [remote rejected] topic/xxxx -> cheesex/xxxx (refusing to allow a
          GitHub App to create or update workflow `.github/workflows/e2e.yml`
          without `workflows` permission)

    The wording varies by credential kind (GitHub App / OAuth App / Personal
    Access Token) and names whichever workflow file differs, so match on the
    two stable fragments rather than the whole sentence.

    Why this rejection happens to cards that never touched a workflow file:
    GitHub compares the pushed branch's `.github/workflows/` tree against the
    target repo's DEFAULT BRANCH, not against this push's diff. A topic branch
    forked from the platform's local base is rejected whenever that local base
    lags GitHub's main in any workflow file — which is most of the time, since
    the local base only advances on 同步上游."""
    text = message.lower()
    return "refusing to allow" in text and "workflow" in text


def _remote_default_branch(repo: Path, url: str, env: dict[str, str]) -> str | None:
    """The default branch of the repo we push to (what its HEAD symrefs to) —
    the branch GitHub compares workflow files against."""
    try:
        out = _git(repo, "ls-remote", "--symref", url, "HEAD", timeout=60, env=env)
    except ValidationError:
        return None
    for line in out.splitlines():
        if not line.startswith("ref:"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
            return parts[1][len("refs/heads/") :]
    return None


def _sync_remote_base_into_topic_branch(
    project_id: uuid.UUID,
    repo: Path,
    branch: str,
    *,
    url: str,
    env: dict[str, str],
) -> dict:
    """Merge the push target's default branch into the topic branch, so its
    `.github/workflows/` tree matches what GitHub compares against and the push
    stops looking like a workflow edit.

    Never raises for an ordinary failure (conflict, nothing to sync, concurrent
    branch move) — always returns a dict with a `synced` key and a
    human-readable `reason`, because the caller's only options are "retry the
    push" and "degrade with an explanation on the card".

    The merge itself runs in a throwaway detached worktree and lands via a
    compare-and-swap ref move, same as `_merge_ref_into_base` — but unlike that
    one it must NOT call `_sync_shared_checkout`: this moves a topic branch, and
    the shared repo directory belongs to the base branch."""
    default = _remote_default_branch(repo, url, env) or _base_branch(repo)
    base_ref = f"refs/cheesex/pr-base/{branch.rsplit('/', 1)[-1]}"
    try:
        _git(
            repo,
            "fetch",
            "--no-tags",
            "-q",
            url,
            f"+refs/heads/{default}:{base_ref}",
            timeout=180,
            env=env,
        )
    except ValidationError as exc:
        return {"synced": False, "reason": f"拉取 GitHub {default} 失败：{exc}"[:300]}

    old_sha = _git(repo, "rev-parse", branch).strip()
    remote_tip = _git(repo, "rev-parse", base_ref).strip()
    contained = subprocess.run(
        ["git", "merge-base", "--is-ancestor", remote_tip, old_sha],
        cwd=repo,
        capture_output=True,
        timeout=20,
    )
    if contained.returncode == 0:
        # Already up to date with GitHub's default branch, so the workflow files
        # that GitHub objected to are this card's OWN edits. Syncing again would
        # change nothing and re-pushing would be rejected identically.
        return {
            "synced": False,
            "up_to_date": True,
            "reason": (
                f"话题分支已包含 GitHub {default} 的最新提交，"
                "被拒的 workflow 改动来自这张卡本身"
            ),
        }

    with _isolated_worktree(project_id, repo, old_sha) as wt:
        try:
            _git(
                wt,
                "-c",
                "user.name=芝士",
                "-c",
                "user.email=cheese@zhishi.local",
                "merge",
                "--no-ff",
                "-q",
                "-m",
                f"同步 GitHub {default} → {branch}（推 PR 分支前）",
                base_ref,
            )
        except ValidationError as exc:
            try:
                conflicts = (
                    _git(wt, "diff", "--name-only", "--diff-filter=U")
                    .strip()
                    .splitlines()
                )
            except ValidationError:
                conflicts = []
            try:
                _git(wt, "merge", "--abort")
            except ValidationError:
                pass
            reason = (
                f"与 GitHub {default} 合并冲突：" + "、".join(conflicts[:20])
                if conflicts
                else f"与 GitHub {default} 合并失败：{exc}"
            )
            return {"synced": False, "reason": reason[:300], "conflicts": conflicts}
        new_sha = _git(wt, "rev-parse", "HEAD").strip()

    try:
        _git(repo, "update-ref", f"refs/heads/{branch}", new_sha, old_sha)
    except ValidationError:
        return {"synced": False, "reason": "话题分支被并发更新，本次未同步"}
    _catch_up_topic_workspace(project_id, branch)
    return {"synced": True, "base": default, "head": new_sha}


def _catch_up_topic_workspace(project_id: uuid.UUID, branch: str) -> None:
    """Let the topic's jj workspace see a commit that reached its git branch
    from outside (here: the sync merge above).

    Not cosmetic. The workspace's bookmark would otherwise still point at the
    pre-merge commit, and the next `snapshot_worktree` moves that bookmark to a
    child of it with `--allow-backwards` — dropping the merge from the branch
    and turning the following re-push into a rejected non-fast-forward. Purely
    best-effort: the git ref is what gets pushed, so a jj hiccup must not fail
    the push."""
    wt = _worktree_path(project_id, branch)
    if not (wt / ".jj").exists():
        return  # no workspace yet — nothing to catch up
    try:
        _catch_up_with_branch(project_id, wt, branch)
    except ValidationError as exc:
        logger.warning(
            "topic workspace %s could not catch up with %s after the PR-base sync: %s",
            wt,
            branch,
            exc,
        )


def push_topic_branch_for_github_pr(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    owner: str,
    repo: str,
    remote_branch: str,
    token: str,
) -> dict:
    """两阶段采纳 (PR迭代式): push the topic's OWN branch (not the base) to the
    project's connected GitHub repo under `remote_branch`, authenticated as
    the approving human's own token — never the App's, since attribution is
    the point (see the PR trailer). Distinct from push_back(), which pushes
    the ALREADY-MERGED base branch via the host's own git credentials and the
    `upstream` remote; this instead prepares a branch for review, using
    whichever repo #192 connected the project to (not necessarily the same
    remote `push_topic_branch` above pushes to).

    Auth reuses `_token_push_env` (credential helper via env var, never argv)
    rather than embedding the token in the push URL. Raises ValidationError on
    any git failure (bad/expired token, network, GitHub outage) — the caller
    treats that as "mechanism unavailable" and degrades to the old
    direct-merge path, same contract as merge_topic().

    One rejection is recoverable and gets exactly one retry: GitHub refusing
    the push because the branch's `.github/workflows/` differs from the target
    repo's default branch and the credential has no `workflows` scope (see
    `_is_workflow_permission_rejection`). Nearly every card hit this — the
    branch forks off the platform's local base, which lags GitHub's main — so
    two-phase accept had degraded to direct-merge for essentially everything.
    The recovery is to merge GitHub's default branch in and push once more;
    anything else about the failure, and any second failure, still degrades as
    before. Syncing only after a rejection keeps the happy path (including the
    60s re-push poll) exactly as cheap as it was — no fetch, no extra commit."""
    repo_path = ensure_repo(project_id)
    try:
        snapshot_worktree(project_id, topic_id, "两阶段采纳前快照")
    except ValidationError:
        pass  # no workspace/jj state yet — nothing pending to fold
    branch = branch_for_topic(topic_id)
    if not _branch_exists(repo_path, branch):
        raise ValidationError("话题还没有可推送的分支")
    url = _github_push_url(owner, repo)
    env = _token_push_env(token)
    refspec = f"{branch}:refs/heads/{remote_branch}"
    try:
        _git(repo_path, "push", url, refspec, timeout=120, env=env)
    except ValidationError as exc:
        if not _is_workflow_permission_rejection(str(exc)):
            raise
        synced = _sync_remote_base_into_topic_branch(
            project_id, repo_path, branch, url=url, env=env
        )
        if not synced.get("synced"):
            raise ValidationError(
                "话题分支的 workflow 文件与 GitHub 默认分支不一致且无法同步"
                f"（{synced.get('reason', '')}）"
            ) from exc
        # Exactly one retry, never a loop: if this is still rejected the card
        # genuinely changes workflow files, and no amount of syncing helps.
        _git(repo_path, "push", url, refspec, timeout=120, env=env)
    head_sha = _git(repo_path, "rev-parse", branch).strip()
    return {"head_sha": head_sha, "remote_branch": remote_branch}


def topic_worktree(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Host path of a topic's git worktree (created on demand), world-writable so
    the sandbox container's non-root `node` user can write into the mount."""
    wt = _ensure_worktree(project_id, branch_for_topic(topic_id))
    import os

    os.chmod(wt, 0o777)
    return wt


_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"
# The `cheese` CLI as this backend build ships it — the ONLY source of truth.
_CLI_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "cheese"
# Where session_dir() stages it, relative to the session dir. Both container
# backends mount THIS over /usr/local/bin/cheese, so the CLI a turn runs is
# always the one its backend shipped, never whatever an image baked months ago.
CLI_IN_SESSION = "bin/cheese"


def cheese_cli_mount_source(session: Path) -> Path:
    """Host path of the CLI copy staged in a topic's session dir (see
    `session_dir`). Host-visible by construction — the session dir is already a
    bind-mount source — which the in-image `/app/sandbox/cheese` is not."""
    return session / CLI_IN_SESSION


def session_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Persistent per-topic ~/.claude (mounted into the ephemeral container) so
    the agent session / --resume survives across turns. Also seeds the `cheese`
    skill here (= ~/.claude/skills, the user source) — one mount holds both the
    session and the skill, with no host settings leaking in — and stages the
    `cheese` CLI at bin/cheese for the container to mount over its baked copy."""
    import os
    import shutil

    d = (
        Path(settings.workspace_root) / ".sessions" / str(project_id) / topic_id.hex[:8]
    ).resolve()
    d.mkdir(parents=True, exist_ok=True)
    # Create the hook WAL before the sandbox starts and make it writable by both
    # container users.  The backend runs as uid 1001 while the tmux image runs as
    # uid 1000; if the hook forwarder creates this directory first, its normal
    # 0755 mode lets the backend read events but not park or remove them.
    spool = d / "cheese-spool"
    spool.mkdir(parents=True, exist_ok=True)
    _loosen(str(spool), 0o777)
    skills_dst = d / "skills"
    if _SKILL_SRC.is_dir():
        shutil.copytree(_SKILL_SRC, skills_dst, dirs_exist_ok=True)
    # Loosen perms so the sandbox container (a different uid) can read/write the
    # mount. Best-effort per entry: the container's Claude Code runs as its own
    # uid and creates files here across turns, which the backend (another uid)
    # then cannot chmod — EPERM on ONE such file used to abort the whole turn
    # ('Operation not permitted' on session-env/…). A file the container made is
    # already accessible to the container, so skipping it is harmless.
    for root, _dirs, files in os.walk(d):
        _loosen(root, 0o777)
        for f in files:
            _loosen(os.path.join(root, f), 0o666)
    # AFTER the loosen walk, which would strip the CLI's exec bit (0o666). Copied
    # every time so a redeployed backend refreshes it on the next turn; a topic
    # whose container is reused for weeks still gets the current CLI.
    _stage_cheese_cli(d)
    return d


def _stage_cheese_cli(session: Path) -> None:
    """Refresh <session>/bin/cheese from this build's copy. Best-effort: a stale
    CLI is bad, but failing a turn over it is worse — the container still has its
    baked copy to fall back on."""
    import shutil

    if not _CLI_SRC.is_file():
        return
    dst = cheese_cli_mount_source(session)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _loosen(str(dst.parent), 0o777)
        shutil.copyfile(_CLI_SRC, dst)
        _loosen(str(dst), 0o777)
    except OSError:
        logger.warning("could not stage the cheese CLI at %s", dst, exc_info=True)


def _loosen(path: str, mode: int) -> None:
    import os

    try:
        os.chmod(path, mode)
    except OSError:
        pass


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
    # A topic's tree is a jj workspace whose .jj/repo pointer only resolves
    # with the main repo's store mounted too (see sandbox_vcs_mounts); the
    # project-level tree (topic_id=None) IS the main repo, no extra mount needed.
    vcs_mounts = (
        sandbox_vcs_mounts(project_id, branch_for_topic(topic_id))
        if topic_id is not None
        else []
    )
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
                *vcs_mounts,
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


def published_endpoint(container: str, port: int) -> str | None:
    """`127.0.0.1:<host-port>` a container publishes an in-container port to, or
    None (container down / mapping missing — an old container predating the
    publish). One parse shared by every "reach into the box" feature."""
    result = subprocess.run(
        ["docker", "port", container, str(port)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # e.g. "127.0.0.1:55007" (possibly one line per address family).
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    host_port = line.rsplit(":", 1)[-1]
    return f"127.0.0.1:{host_port}" if host_port.isdigit() else None


def app_endpoint(topic_id: uuid.UUID) -> str | None:
    """`127.0.0.1:<host-port>` of the topic's app port, or None when no container
    publishes it.

    Both backends' boxes are asked, tmux first. Asking only the SDK one (the old
    behavior) meant 运行环境预览 was dead for every tmux-backed topic — which is
    all of them under ``AGENT_BACKEND=tmux`` — because 3000 is published by
    ``cheesex-tmux-*`` while the lookup went to ``cheesex-sbx-*``.
    """
    if not sandbox_available():
        return None
    for name in (tmux_container_name(topic_id), container_name(topic_id)):
        endpoint = published_endpoint(name, APP_PORT)
        if endpoint is not None:
            return endpoint
    return None


# NOTE: there is deliberately no `app_preview_url` here any more. It returned
# `http://127.0.0.1:<host-port>` — the port is bound to the *server's* loopback,
# so the address only ever resolved for someone running the whole platform on
# their own laptop and every remote user got a white iframe. What a browser gets
# now is the backend's reverse-proxy path, built by the route that serves it
# (`app.api.routes.app_preview`), from this endpoint.


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
    """Run a machine gate in a disposable, resource-limited Docker container.

    The command is *data* in Docker's argv; no host shell ever parses it. The
    container receives only the topic worktree, no Docker socket, no backend
    environment/secrets, and no network. Failure to start Docker fails the gate
    closed -- there is intentionally no host-execution fallback.

    Full output goes to ``log_path`` while the returned tail remains bounded.
    """
    from datetime import UTC, datetime

    resolved_cwd = cwd.resolve(strict=True)
    container_name = f"cheesex-gate-{uuid.uuid4().hex[:12]}"
    temporary_log = log_path is None
    if temporary_log:
        fd, raw_path = tempfile.mkstemp(prefix="cheesex-gate-", suffix=".log")
        os.close(fd)
        output_path = Path(raw_path)
    else:
        output_path = log_path
        assert output_path is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)

    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        container_name,
        "--label",
        "cheesex-gate=1",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.quality_gate_pids_limit),
        "--memory",
        f"{settings.quality_gate_memory_mb}m",
        "--cpus",
        str(settings.quality_gate_cpus),
        "--user",
        "1000:1000",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=512m",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--mount",
        f"type=bind,source={resolved_cwd},target=/workspace",
        "--workdir",
        "/workspace",
        settings.quality_gate_image,
        "sh",
        "-lc",
        'exec sh -lc "$1"',
        "cheesex-gate",
        command,
    ]

    exit_code = -1
    try:
        with output_path.open("w", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now(UTC).isoformat()}] $ {command}\n")
            stream.write(f"(workspace: {resolved_cwd}, container: {container_name})\n")
            stream.flush()
            process = subprocess.Popen(  # noqa: S603 -- fixed Docker argv boundary
                docker_argv,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Killing the Docker client alone can leave the daemon-side
                # container running. Remove only the exact random name.
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=20,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                process.kill()
                process.wait(timeout=5)
                exit_code = 124
                stream.write(f"\n检查超时（>{timeout}s），容器已强制移除。\n")
            stream.write(f"\n(exit: {exit_code})\n")
    finally:
        try:
            output = output_path.read_text(encoding="utf-8", errors="replace")
        finally:
            if temporary_log:
                output_path.unlink(missing_ok=True)
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
