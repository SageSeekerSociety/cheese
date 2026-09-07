"""Project workspace — a git repo per project (spec §6.3: 所有产出都是 git).

每个 project = 一个 git 仓,每棵树 = 一个工作区目录 + 一条 git 分支
(`branch_for_tree`)。**提交这一步不在这里**:干活的分身在自己的机器上
`git commit`,再把分支推回来(`api/routes/git_http.py`)。这个模块读那条分支
——diff、采纳、推 PR 全是分支上的提交——并维护一份跟着它走的检出,供文件面板
和合并使用。

话题工作区是主仓的一个 git worktree,检出在那条分支上。所以分身在容器里那次
`git commit` **就是**分支往前走的那一步——不需要导出、不需要推、不需要平台代劳。
平台自己在这里只做检出级的操作(建工作区、把主干合进来物化冲突),不写任何人的
提交。
"""

import contextlib
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.domain.agent.platform_failures import WORKSPACE_VCS_PERMS_CODE
from app.domain.workspace import identity as identity_mod
from app.domain.workspace.textfile import (
    MAX_TEXT_BYTES,
    content_version,
    decode_text,
    looks_binary,
)

DEFAULT_BRANCH = "main"
# 沙箱镜像：芝士分身在容器里跑代码/测试，碰不到宿主机 (spec §9.1).
SANDBOX_IMAGE = "python:3.12-slim"

# The ONE uid/gid that may touch a project's git store.
#
# `sandbox_vcs_mounts` below bind-mounts the project's main-repo `.git` into
# every sandbox container read-write, so the backend process and the
# in-container agent commit into the same store. Both of them WRITE it — the
# agent's own `git commit` is what moves a topic branch — and git creates
# object directories 0755 and loose objects 0444, both owned by whoever wrote
# them. A second uid can read all of that and add nothing to it: the commit
# fails on `.git/objects/xx`, which it does not own. `core.sharedRepository`
# is git's supported way to widen those modes, so unlike the jj store this
# used to be, the constraint is now negotiable — but nothing negotiates it
# today, so both sides must still be the same uid.
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


def branch_for_tree(tree_id: uuid.UUID) -> str:
    """一棵树 = 一条 git 分支. Deterministic from the tree's id.

    A tree is a batch of work — many tasks share one, and it opens one PR. The
    branch belongs to the tree rather than to whoever is writing on it, which is
    the whole point: a room and every task batched with it write to one branch,
    and that branch is what the PR is open on.

    The `topic/` prefix stays even though a tree is not a topic. Each room's
    first tree carries the room's own id (migration `b8e2f4a90d33`), so the
    derived name is byte-for-byte what every existing branch, worktree and
    container path is already called; renaming the prefix would rename all of
    them and buy nothing.
    """
    return f"topic/{tree_id.hex[:8]}"


def _git(
    repo: Path, *args: str, timeout: int = 20, env: dict[str, str] | None = None
) -> str:
    result = _run_subprocess(["git", *args], repo, timeout, env)
    if result.returncode != 0:
        # git reports merge conflicts on stdout with an empty stderr — fall back
        # so the caller's error isn't blank.
        detail = result.stderr.strip() or result.stdout.strip()
        # The main repo's `.git` is bind-mounted into every sandbox
        # (sandbox_vcs_mounts), so a uid split breaks git here too.
        if _permission_denied(detail) or _names_a_store_it_cannot_enter(detail, repo):
            raise WorkspacePermissionError(_uid_split_hint(f"git {args[0]}: {detail}"))
        raise ValidationError(f"git {args[0]} failed: {detail}")
    return result.stdout


class WorkspacePermissionError(ValidationError):
    """A workspace operation failed because the on-disk repo is owned by another
    uid. Distinct from a generic ValidationError so the failure names its own
    cause: this is the shape a uid split takes, and it used to reach the file
    panel as a bare "failed" — indistinguishable from "the file is missing",
    which is why it went undiagnosed for a whole project.

    Carries its classification rather than leaving one to be recognised from
    the wording below: this is the platform's own copy, and copy that a
    classifier greps is copy nobody can edit.
    """

    failure_code = WORKSPACE_VCS_PERMS_CODE


# Match the OS error rather than the wording of any one command's message.
_PERMISSION_SIGNS = ("Permission denied", "os error 13", "Operation not permitted")


def _permission_denied(text: str) -> bool:
    return any(sign in text for sign in _PERMISSION_SIGNS)


def _store_of(tree: Path) -> Path:
    """The directory git uses as this tree's repository: `.git` itself for a
    main repo, or wherever a worktree's one-line `.git` pointer leads."""
    marker = tree / ".git"
    try:
        if marker.is_file():
            pointer = marker.read_text().split(":", 1)[1].strip()
            return Path(os.path.normpath(tree / pointer))
    except (OSError, IndexError):
        pass
    return marker


def _names_a_store_it_cannot_enter(detail: str, tree: Path) -> bool:
    """Whether a git failure is really a uid split wearing the wrong words.

    git does not report EACCES when it cannot get into a repository. It
    validates a gitdir by reading what is inside, and an unreadable one fails
    that check — so a store another uid owns comes back as `fatal: not a git
    repository`, which reads like the repository is gone. It is not gone: it is
    right there and this process cannot enter it. Look at the store the command
    ran against (walking up to whatever is still visible, since the wall itself
    hides everything behind it) and tell the two apart.
    """
    if "not a git repository" not in detail.lower():
        return False
    store = _store_of(tree)
    for candidate in (store, *store.parents):
        if candidate.exists():
            return not os.access(candidate, os.R_OK | os.X_OK)
    return False


def _uid_split_hint(detail: str) -> str:
    return (
        f"工作区仓库里有当前进程（uid={os.getuid()}）无权访问的文件。"
        f"后端与沙箱容器必须跑在同一个 uid（应为 {AGENT_UID}）——"
        f"git 的对象目录属于先写的一方，另一个 uid 加不进东西去。"
        f"原始报错：{detail}"
    )


def ensure_repo(project_id: uuid.UUID) -> Path:
    repo = _repo(project_id)
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q", "-b", DEFAULT_BRANCH)
        _git(repo, "config", "user.email", "cheese@zhishi.local")
        _git(repo, "config", "user.name", "芝士")
    _ensure_base_commit(repo)  # a worktree needs a real commit to fork from
    return repo


def _accept_a_push_to_a_checked_out_branch(repo: Path) -> None:
    """Let a machine push a branch that this repo has checked out somewhere.

    Every open topic is a worktree of this repo checked out ON its branch, and
    git's default answer to a push at a checked-out branch is to refuse it. The
    refusal reaches a `cheese-sync` that cannot report anything (a Stop hook
    that errors takes the turn down), so it would look exactly like success and
    the work would sit on the machine forever.

    ``updateInstead`` accepts the push AND moves that worktree onto it, so the
    files the panel reads are the ones the push carried — with no window in
    which the checkout is behind its own branch. It still refuses when the
    worktree holds uncommitted edits, which is the one case where landing the
    push would destroy someone's unsaved work.
    """
    _git(repo, "config", "receive.denyCurrentBranch", "updateInstead")


def _has_commit(repo: Path) -> bool:
    # HEAD specifically, not --all: a repo can hold refs pushed by a machine
    # while its own main is still unborn, and `rev-list --all` would then
    # falsely report a base commit.
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
            "chore: initialize project repository",
        )


def _branch_exists(repo: Path, branch: str) -> bool:
    return bool(_git(repo, "branch", "--list", branch).strip())


def _base_branch(repo: Path) -> str:
    if _branch_exists(repo, DEFAULT_BRANCH):
        return DEFAULT_BRANCH
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or DEFAULT_BRANCH


def _tree_dirname(topic_id: uuid.UUID) -> str:
    """On-disk (and in-container) name of a topic's workspace directory.

    Derived from the topic id ALONE — deliberately not from `branch_for_tree`,
    even though the two agree today (`topic/<hex8>` → `topic_<hex8>`). The
    directory name is baked into things that survive a rename and cannot be
    migrated cheaply: the worktree's admin directory under
    `.git/worktrees/<name>`, the relative `.git` pointer inside every worktree
    (whose depth `sandbox_vcs_mounts` reproduces), the container workdir, and —
    through that workdir — the session name the device backend hashes, so a
    changed path retires a live claude session and drops its context. Naming
    the branch is a git-side decision; it must not be able to move anyone's
    files.
    """
    return f"topic_{topic_id.hex[:8]}"


def _worktree_path(project_id: uuid.UUID, place_id: uuid.UUID) -> Path:
    """Where a place's files are: its TREE's directory.

    The argument is a place rather than a tree because every caller has a place
    and none of them should have to know that a room's tree changes when a batch
    is sealed. `tree_for_place` is the one place that answers it, and it answers
    "itself" for a room on its first tree — which is what keeps every existing
    directory exactly where it already is.
    """
    return tree_worktree_path(project_id, tree_for_place(place_id))


def tree_worktree_path(project_id: uuid.UUID, tree_id: uuid.UUID) -> Path:
    """Where a TREE's files are — for the callers that hold the tree itself
    (retiring a room's trees, one by one) rather than a place writing to it."""
    return (
        Path(settings.workspace_root)
        / ".worktrees"
        / str(project_id)
        / _tree_dirname(tree_id)
    ).resolve()


def _ensure_worktree(project_id: uuid.UUID, place_id: uuid.UUID) -> Path:
    """每棵树一个 git worktree：一批活共用一个工作目录，不同的树互不覆盖。工作区
    检出在这棵树自己的分支上（`branch_for_tree`）——分身在容器里那次 `git commit`
    直接就把分支往前挪了，没有导出、没有代推、也没有一步会失败的中转。分支名与
    工作区目录名各自独立派生，见 `_tree_dirname`。

    一棵**新**树从 base 分支长出来。它以前不是这样：一件活的树从它所在房间的树
    fork，因为那件活的提交最后要合回房间那一条分支。现在一批活共用一棵树、
    一起进同一个 PR，没有东西要合回去，所以也没有 fork 点要挑——下一批从 main
    开始，和任何一个并行的 PR 一样。

    但树的分支不一定是新的，因为这个目录不是它唯一的作者：文件不在这里的分身
    只能用 git 推到它（`api/routes/git_http.py`），而那次推送可能远早于有人第一
    次需要这个目录。分支已经存在时就检出**它**。以前不看这一眼，于是这里
    先给树铺一个空工作区、再把分支移到那个空提交上——一整批活被一个零文件的
    提交顶掉，而下一张验收卡正是拿它去开 PR 的。"""
    main = ensure_repo(project_id)
    _ensure_base_commit(main)  # a worktree needs a base commit to fork from
    tree_id = tree_for_place(place_id)
    branch = branch_for_tree(tree_id)
    wt = _worktree_path(project_id, place_id)
    if (wt / ".git").exists():
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    _accept_a_push_to_a_checked_out_branch(main)
    # A directory registered here but deleted from disk (a hand-cleaned disk,
    # a worktree this already replaced) makes `worktree add` refuse the name it
    # left behind.
    with contextlib.suppress(ValidationError):
        _git(main, "worktree", "prune")
    delivered = _branch_exists(main, branch)
    start = branch if delivered else _base_branch(main)
    create = [] if delivered else ["-b", branch]
    if wt.exists() and any(wt.iterdir()):
        _adopt_the_directory_that_is_already_there(main, wt, start, create)
    else:
        # A generous timeout: this writes out a whole project tree, and a
        # checkout killed halfway leaves a directory that the branch above
        # would then read as a tree full of uncommitted work.
        _git(main, "worktree", "add", "-q", *create, str(wt), start, timeout=300)
    _point_at_the_store_relatively(main, wt)
    _make_world_writable(wt)
    return wt


def _adopt_the_directory_that_is_already_there(
    main: Path, wt: Path, start: str, create: list[str]
) -> None:
    """Turn a populated directory into this branch's worktree, keeping the files.

    The directories on disk predate git worktrees — each is a jj workspace, and
    the whole point of `_tree_dirname` is that its name cannot move. So the
    directory has to stay exactly where it is and gain a `.git`, rather than be
    deleted and checked out again: whatever the agent had not committed lives
    only in these files, and re-checking-out would silently drop it.

    git has no "adopt this directory" (`worktree add` demands an empty one), so
    the worktree is created next door and only its administrative half is moved
    in. The index moves with it, describing `start` while the files describe
    what the agent actually left — which is precisely how uncommitted work is
    meant to read: `git status` shows it, the next commit carries it.

    The staging path is a throwaway PARENT holding the tree's own name, because
    git names the admin directory after the last path component: staged under
    its real name, the entry that ends up in `.git/worktrees/` is the one this
    tree would have had if it had been created here in the first place.
    """
    staging_parent = wt.with_name(f".adopting-{wt.name}")
    shutil.rmtree(staging_parent, ignore_errors=True)
    staging = staging_parent / wt.name
    _git(main, "worktree", "add", "-q", *create, str(staging), start, timeout=300)
    try:
        shutil.rmtree(wt / ".jj", ignore_errors=True)
        shutil.move(str(staging / ".git"), str(wt / ".git"))
        # Teach the admin dir where its worktree went; without this git prunes
        # the entry the moment it notices `staging` is gone.
        _git(main, "worktree", "repair", str(wt))
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def _point_at_the_store_relatively(main: Path, wt: Path) -> None:
    """Rewrite the worktree's `.git` pointer as a path relative to itself.

    git writes an absolute one, which is a host path and therefore meaningless
    inside a sandbox container — where the same worktree is mounted much
    shallower. A relative pointer survives the remap, and `sandbox_vcs_mounts`
    mounts the store where it lands. Host-native use is unaffected: git
    resolves a relative `gitdir:` against the directory holding the file.
    """
    admin = Path(_git(wt, "rev-parse", "--absolute-git-dir").strip())
    (wt / ".git").write_text(f"gitdir: {os.path.relpath(admin, wt)}\n")


# Container path a topic's worktree is bind-mounted to (exec_in_sandbox below)
# — the anchor `sandbox_vcs_mounts` resolves against.
SANDBOX_WORKDIR = "/work"


def sandbox_vcs_mounts(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    container_workdir: str = SANDBOX_WORKDIR,
) -> list[str]:
    """Extra `docker run -v` args so a topic's worktree is a working git repo
    inside its sandbox container.

    A linked worktree keeps nothing but its files: its HEAD, index and every
    object live in the project's shared repo, which the worktree finds through
    the `gitdir:` pointer in its own `.git`. `_point_at_the_store_relatively`
    writes that pointer relative to the worktree, so it says the same thing
    wherever the worktree is mounted — but it still has to LAND on the store.
    A container only ever gets the worktree, remapped to `container_workdir`
    (much shallower than the host tree), so the pointer resolves somewhere
    near the container's root instead of at the real repo, and every git
    command inside dies with "not a git repository".

    Follow that pointer from `container_workdir` to see where it lands, and
    mount the project's `.git` there. Host-native access (backend diff, merge,
    file panel — outside any container) is untouched: only the container's
    extra mounts change.
    """
    main = _repo(project_id)
    wt = _ensure_worktree(project_id, topic_id)
    pointer = (wt / ".git").read_text().split(":", 1)[1].strip()
    admin = Path(os.path.normpath(os.path.join(container_workdir, pointer)))
    store_in_container = admin.parents[1]  # strip "worktrees/<name>"
    return ["-v", f"{main / '.git'}:{store_in_container}"]


# Container mount point of a project's whole `.worktrees/<project>` tree in a
# sandbox. One mount covering every topic's worktree AND the
# shared dependency stores below, because hardlinks cannot cross bind mounts
# (link(2) → EXDEV even on the same filesystem): pnpm/uv only dedup against a
# store that lives on the SAME mount as the tree they install into. Verified
# live on the dev box — a cross-mount ln inside a sandbox fails with "Invalid
# cross-device link", and pnpm/uv then silently fall back to full copies, which
# is how one project's 220 worktrees came to hold 236GB.
SANDBOX_TOPICS_ROOT = "/topics"

# Where uv keeps the Python builds it downloads. The base sandbox image ships
# system python 3.11 and this project needs >=3.13, so uv fetches a managed
# interpreter at RUNTIME — and uv's default home for it is `~/.local/share/uv`,
# i.e. `/home/node`, which is the container's own overlay layer. The worktree
# (and its `.venv`) is a host bind mount and outlives the container; the
# interpreter it points at does not. `.venv/bin/python` is an absolute symlink
# into that layer and `pyvenv.cfg`'s `home =` names the same directory, so
# every container rebuild leaves the surviving venv pointing at nothing:
#
#     $ .venv/bin/python -V
#     No such file or directory
#     $ .venv/bin/pyright --version
#     cannot execute: required file not found      # shebang -> that same symlink
#
# `uv run` does repair this on its own (it recreates the venv from scratch),
# so this is a cost, not a breakage — measured in a sandbox on 2026-08-13:
# 15s and a 33MiB interpreter re-download per rebuild, versus 1s and no
# download once the interpreter lives here. Rebuilds are frequent (#316: 23 in
# one day), the re-download is NOT served by `.uv-cache` (uv caches wheels,
# not managed interpreters), and anything invoking `.venv/bin/<tool>` directly
# — or via `uv run --no-sync`, which recreates the venv WITHOUT reinstalling
# and so hands back an empty one — is broken until a plain `uv run` runs.
#
# `backend/Dockerfile` hit the identical symlink problem across image stages
# and fixed it the identical way (`ENV UV_PYTHON_INSTALL_DIR=/opt/python`);
# this is the same fix on the axis the sandbox varies along, which is time
# rather than stages.
UV_PYTHON_STORE = ".uv-python"

# Shared per-project stores that live on the HOST, as (host dirname, container
# env var). Dot-named so they can never collide with a topic worktree dir
# (`topic_<hex>`) or the merge-worktree root (`_merge`).
#
# The first two are dependency caches: pnpm reads npm_config_store_dir (its
# documented env form of store-dir), uv reads UV_CACHE_DIR, and both install by
# hardlinking out of their store when it is on the same filesystem/mount, so
# every topic's node_modules/.venv shares one physical copy per file. The third
# is not a cache — see UV_PYTHON_STORE: it is the interpreter the venv POINTS
# AT, and it is here because a venv that outlives its container has to be.
_SANDBOX_STORES = (
    (".pnpm-store", "npm_config_store_dir"),
    (".uv-cache", "UV_CACHE_DIR"),
    (UV_PYTHON_STORE, "UV_PYTHON_INSTALL_DIR"),
)


def sandbox_topic_workdir(topic_id: uuid.UUID) -> str:
    """A topic's worktree path inside a sandbox — its REAL path under the
    project-tree mount (not a per-topic remap), so hardlinks to the shared
    stores on the same mount work."""
    return f"{SANDBOX_TOPICS_ROOT}/{_tree_dirname(topic_id)}"


# Container mount point of a project's whole `.sessions/<project>` tree — the
# session-dir counterpart of SANDBOX_TOPICS_ROOT, and it exists for the same
# reason the topics tree is one mount: a box now hosts a whole ROOM, so it needs
# every one of that room's topics' `~/.claude` dirs, and a container's mounts are
# fixed at creation while a room keeps gaining tasks. One mount of the parent
# covers topics that do not exist yet.
#
# Each topic's `claude` is pointed at its own subdirectory with a per-session
# `CLAUDE_CONFIG_DIR` rather than by remapping the mount, because there is only
# one mount and many sessions.
SANDBOX_SESSIONS_ROOT = "/sessions"


def sessions_root(project_id: uuid.UUID) -> Path:
    """Host dir holding every topic's session dir for a project — the parent of
    `session_dir`, and the source of the SANDBOX_SESSIONS_ROOT mount.

    Also stages the `cheese` CLI here, at the same `bin/cheese` relative path a
    single topic's session dir uses. The CLI mount is one per BOX and a box now
    serves a room, so it cannot come out of any one topic's dir — but it must
    still come from a freshly-staged copy rather than the image's baked one (a
    box outlives many deploys; the stale baked copy once wrote every agent's
    memories into the wrong pool). The project's sessions root is the nearest
    thing all of a room's topics share."""
    root = (Path(settings.workspace_root) / ".sessions" / str(project_id)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _loosen(str(root), 0o777)
    _stage_cheese_cli(root)
    return root


def sandbox_session_dir(topic_id: uuid.UUID) -> str:
    """A topic's `~/.claude` INSIDE the sandbox — its real path under the sessions
    mount. Must agree with `session_dir`'s host layout (both name the directory
    `topic_id.hex[:8]`); the session exports this as CLAUDE_CONFIG_DIR."""
    return f"{SANDBOX_SESSIONS_ROOT}/{topic_id.hex[:8]}"


def _write_marker(path: Path, value: str, what: str) -> None:
    """Record one small DB-derived fact on disk, idempotently and atomically.

    Write-then-replace: a reader must never see a half-written value, and two
    concurrent turns of one room both write here. Best-effort by design — every
    caller's fact is a cache of something the DB owns, so losing the file costs
    a fallback, not correctness."""
    try:
        if path.read_text().strip() == value:
            return
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(value)
        tmp.replace(path)
    except OSError:
        logger.warning("could not record %s for %s", what, path.name, exc_info=True)


# --- which tree a place writes to -------------------------------------------
#
# A place (a room, or one thread in it) does not own a tree; it WRITES to one,
# and which one changes over a room's life: a batch is sealed when its PR opens
# and the next batch starts a fresh tree. That mapping is a DB fact, and this
# module is deliberately sync and DB-free — so the layer that knows writes the
# answer here, exactly the way `bind_room` already does for boxes.
#
# No binding means "this place writes to the tree named by its own id", which is
# true of every room on its first tree (that tree carries the room's id) and of
# everything that existed before trees did. A degraded answer, never a wrong
# one.
_PLACE_TREES_DIRNAME = ".place-trees"


def _place_trees_dir() -> Path:
    return Path(settings.workspace_root) / _PLACE_TREES_DIRNAME


def bind_tree(place_id: uuid.UUID, tree_id: uuid.UUID) -> None:
    """Record that *place_id*'s files live on *tree_id*."""
    _write_marker(_place_trees_dir() / place_id.hex, tree_id.hex, "place tree")


def tree_for_place(place_id: uuid.UUID) -> uuid.UUID:
    """The tree this place writes to — itself when nothing says otherwise."""
    try:
        return uuid.UUID(hex=(_place_trees_dir() / place_id.hex).read_text().strip())
    except (OSError, ValueError):
        return place_id


def forget_tree(place_id: uuid.UUID) -> None:
    with contextlib.suppress(OSError):
        (_place_trees_dir() / place_id.hex).unlink()


def gate_workdir_for(worktree: Path) -> str:
    """Where a gate container must mount ``worktree`` so its venv still works.

    Not a free choice: it has to be the SAME absolute path the agent's own
    sandbox used, because that is the path baked into every console script the
    agent's `uv sync` created. `_worktree_path` names the directory
    ``_tree_dirname(topic_id)`` and `sandbox_topic_workdir` builds the
    container path from exactly that, so the host directory's own name is
    enough — a gate needs no topic argument to agree with the sandbox.
    """
    return f"{SANDBOX_TOPICS_ROOT}/{worktree.name}"


def sandbox_store_env(root: Path) -> list[str]:
    """`docker run -e` args pointing each tool at its shared store, creating the
    host dirs (world-writable — the backend may run as a different uid than the
    container user that fills them).

    Split out of `sandbox_project_mounts` so the one place that decides where a
    sandbox puts its interpreter can be read by a test without standing up a
    repo — see tests/unit/test_sandbox_stores.py.
    """
    env_args: list[str] = []
    for dirname, env_var in _SANDBOX_STORES:
        store = root / dirname
        store.mkdir(parents=True, exist_ok=True)
        try:  # the sandbox's non-root `node` user fills the store
            os.chmod(store, 0o777)
        except OSError:
            pass
        env_args += ["-e", f"{env_var}={SANDBOX_TOPICS_ROOT}/{dirname}"]
    return env_args


def sandbox_project_mounts(project_id: uuid.UUID, topic_id: uuid.UUID) -> list[str]:
    """`docker run` args mounting the project's `.worktrees` tree (topics +
    shared stores, one mount — see SANDBOX_TOPICS_ROOT) plus the git store
    mount anchored to the topic's in-container workdir, plus the store env.

    Ensures the store dirs exist host-side, writable by the sandbox's non-root
    `node` user (the backend may run as a different uid; the stores are filled
    from inside containers).

    Isolation note: every topic sandbox of a project sees (and can write) its
    sibling topics' worktrees. That is not a new trust boundary — the same
    containers already share the project's writable `.git` store, so
    same-project topics were never isolated from each other; cross-project
    isolation is unchanged."""
    root = _worktree_path(project_id, topic_id).parent
    root.mkdir(parents=True, exist_ok=True)
    env_args = sandbox_store_env(root)
    workdir = sandbox_topic_workdir(topic_id)
    return [
        "-v",
        f"{root}:{SANDBOX_TOPICS_ROOT}",
        *env_args,
        *sandbox_vcs_mounts(project_id, topic_id, container_workdir=workdir),
    ]


def audit_workspace_ownership() -> list[str]:
    """Boot-time check that this process can actually use the workspace it was
    handed — one problem string per finding, empty when healthy.

    A uid split does not announce itself: the backend keeps booting and only the
    file panel dies, project-wide, with a 422 that reads like "file not found".
    So state it at startup instead. Deliberately cheap — the workspace root and
    each project's `.git`, not a walk of the store: that directory is what the
    backend and every sandbox both commit into, so being locked out of it fails
    everything downstream of it.

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
        store = entry / ".git"
        try:
            if not store.is_dir() or os.access(store, os.R_OK | os.W_OK | os.X_OK):
                continue
            owner = store.stat().st_uid
        except OSError:
            continue
        problems.append(
            f"{store} 属主 uid={owner}，当前进程 uid={me} 写不了"
            f"——该项目的所有 git 操作都会失败"
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
    return _ensure_worktree(project_id, topic_id)


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
        branch = branch_for_tree(tree_for_place(topic_id))
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


def _diff_base(repo: Path) -> str:
    """What a tree's changes are measured AGAINST: the project's base branch.

    It used to be the room's branch when the topic had forked one, because a
    task's branch grew out of its room and measuring against main would have
    reported the room's whole diff as the task's. A tree does not grow out of
    anything now — every batch forks the base branch and reaches main through
    its own PR — so the question "what did this batch change" has one answer
    again.
    """
    return _base_branch(repo)


def topic_diff(project_id: uuid.UUID, topic_id: uuid.UUID) -> str:
    """Full diff of a topic's branch vs what it grew out of (`_diff_base`)."""
    repo = ensure_repo(project_id)
    branch = branch_for_tree(tree_for_place(topic_id))
    if not _branch_exists(repo, branch):
        return ""
    return _git(repo, "diff", f"{_diff_base(repo)}...{branch}")


def topic_changed_files(project_id: uuid.UUID, topic_id: uuid.UUID) -> list[str]:
    """Paths a topic's branch changes relative to what it grew out of — the same
    range :func:`topic_diff` renders, named only.

    It exists so the panel can answer "is there anything to review, and how
    much" WITHOUT fetching the diff. The 改动 tab has to be right while it is
    closed (whether it is offered at all, and the count on it), and pulling a
    whole diff on every turn boundary to arrive at one integer is the shape that
    got the 资源 drawer's 20-second poll deleted.

    Empty when the branch doesn't exist yet, matching :func:`topic_diff`: a
    topic that has never written anything changes nothing.
    """
    repo = ensure_repo(project_id)
    branch = branch_for_tree(tree_for_place(topic_id))
    if not _branch_exists(repo, branch):
        return []
    out = _git(repo, "diff", "--name-only", f"{_diff_base(repo)}...{branch}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def topic_added_files(project_id: uuid.UUID, topic_id: uuid.UUID) -> list[str]:
    """Paths a topic's branch ADDS relative to the base — not modifies.

    Additions specifically, because the caller asking is looking for two
    branches that each introduce a NEW alembic revision (#314). Two branches
    editing the same existing file is ordinary; two branches each creating a
    migration is a fork of the chain waiting to happen, and it is the added-file
    list that tells them apart.

    Empty (never an exception) when the branch doesn't exist yet: a topic that
    has not written anything cannot collide with anything.
    """
    repo = ensure_repo(project_id)
    branch = branch_for_tree(tree_for_place(topic_id))
    if not _branch_exists(repo, branch):
        return []
    base = _base_branch(repo)
    out = _git(repo, "diff", "--name-only", "--diff-filter=A", f"{base}...{branch}")
    return [line.strip() for line in out.splitlines() if line.strip()]


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


def remove_worktree(project_id: uuid.UUID, wt: Path) -> bool:
    """Take a topic tree's worktree off this box for good. True when nothing
    of it is left on disk afterwards (including when there was nothing to begin
    with). `wt` is a `tree_worktree_path` or an entry `topic_worktrees_on_disk`
    returned — never the merge staging tree, which `_discard_worktree` owns.

    The branch is untouched: its commits are the record of the work, and the
    tree's directory is only ever a checkout of them plus whatever was never
    committed — which, for a place being archived, is abandoned by definition.

    Same teardown as `_discard_worktree`, with one difference it has to have:
    the project's main repo may itself be gone (a deleted project leaves its
    `.worktrees/<project>` behind), and then there is nothing to ask git to
    unregister from — the directory just goes.
    """
    if not wt.exists():
        return True
    repo = _repo(project_id)
    if (repo / ".git").exists():
        # A generous timeout: this deletes a whole checkout, and a run that is
        # cut short falls through to rmtree below rather than being lost.
        with contextlib.suppress(ValidationError):
            _git(repo, "worktree", "remove", "--force", str(wt), timeout=300)
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    if (repo / ".git").exists():
        with contextlib.suppress(ValidationError):
            _git(repo, "worktree", "prune")
    return not wt.exists()


# A topic tree's directory, and nothing else that lives beside one: the merge
# staging root (`_merge`), the adoption staging dirs (`.adopting-*`) and the
# shared stores (`.pnpm-store`, ...) all fail this on purpose.
_TOPIC_TREE_DIR = re.compile(r"^topic_([0-9a-f]{8})$")


def topic_worktrees_on_disk() -> list[tuple[uuid.UUID, str, Path]]:
    """Every topic tree directory under the workspace, as
    ``(project_id, tree id hex prefix, path)`` — the disk's own account of what
    is there, for the sweep that reconciles it against the database.

    Only the eight hex digits are on disk (`_tree_dirname`), never the whole
    id; resolving them is the caller's job. A `.worktrees/<project>` entry that
    is not a uuid is not one of ours and is passed over."""
    root = Path(settings.workspace_root) / ".worktrees"
    found: list[tuple[uuid.UUID, str, Path]] = []
    if not root.is_dir():
        return found
    for project_dir in sorted(root.iterdir()):
        try:
            project_id = uuid.UUID(project_dir.name)
        except ValueError:
            continue
        if not project_dir.is_dir():
            continue
        for entry in sorted(project_dir.iterdir()):
            match = _TOPIC_TREE_DIR.match(entry.name)
            if match is None or not entry.is_dir():
                continue
            found.append((project_id, match.group(1), entry))
    return found


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


def _warn_about_discarded_changes(repo: Path) -> None:
    """Name whatever the shared-tree sync is about to throw away.

    Discarding is the sync's whole point — the shared directory mirrors the
    base tip and is not where work is kept. But it is not read-only either:
    `write_file` and `exec_in_sandbox` both write straight into it when called
    with `topic_id=None`, and nothing ever commits those writes. So a discard
    here can silently destroy something a human typed in the 文件 panel.
    Failing loudly was at least visible; deleting silently would be a net loss,
    since it is the harder of the two to diagnose after the fact.

    Best-effort by construction: a status that fails must never become the
    thing that wedges the sync, so every error is swallowed. Tracked
    modifications only (`-uno`) — untracked files survive both the forced
    checkout and the `reset --hard`, so naming them would be a false alarm.
    `--no-optional-locks` keeps this from taking `index.lock` itself, which
    the retry loop right below exists to wait out.
    """
    try:
        dirty = _git(
            repo, "--no-optional-locks", "status", "--porcelain", "-uno", timeout=10
        ).strip()
    except (ValidationError, OSError):
        return
    if dirty:
        logger.warning(
            "shared checkout sync in %s is discarding uncommitted local "
            "changes to tracked files (the shared tree is a mirror of the "
            "base tip, not storage — but write_file/exec_in_sandbox with "
            "topic_id=None can write here):\n%s",
            repo,
            dirty,
        )


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
    _warn_about_discarded_changes(repo)
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
    sync_checkout: bool = True,
) -> dict:
    """Merge `merge_ref` into `base` without ever running the merge itself in
    the project's shared working directory. The merge happens in a throwaway
    detached worktree (own index, own lock, discarded whole regardless of
    outcome); only a fast, always-conflict-free ref move + working-tree sync
    touches the shared repo. That ref move is a compare-and-swap
    (`update-ref old new`) — if another accept landed on `base` in the
    meantime, this retries against the new tip rather than clobbering it or
    silently merging on top of a stale base.

    `sync_checkout=False` for a `base` that is NOT the project's base branch.
    The shared directory mirrors the base tip and nothing else; pointing it at a
    tree's branch would hand every project-level reader (list_files/read_file/
    exec_in_sandbox with topic_id=None) one batch's in-progress work as if it
    were the project. Those readers are already correct then — the base branch
    did not move — so the sync is not merely unnecessary, it is the bug.

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
        if not sync_checkout:
            return {"merged": True, "branch": merge_ref, "into": base}
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

    Whatever is on the branch is what gets merged, and nothing is added to it on
    the way in. Work that was never committed was never delivered — the person
    who wrote it decides when it becomes a commit, and until they do it is not
    in the diff anyone reviewed either.

    On conflict it aborts and reports, never half-merges."""
    repo = ensure_repo(project_id)
    branch = branch_for_tree(tree_for_place(topic_id))
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
        project_id, repo, base, branch, f"chore: merge {branch} into {base}"
    )


def _is_ancestor(repo: Path, ref: str, of: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ref, of],
            cwd=repo,
            capture_output=True,
            timeout=20,
        ).returncode
        == 0
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


def _base_adds_nothing(repo: Path, ref: str, base: str) -> bool:
    """Whether `base` contributes any CONTENT the upstream doesn't already have.

    Not `rev-list --count ref..base`: after a merge-based sync the base is ahead
    by a merge commit that changes not one byte. What decides whether the base
    may simply be pointed at the upstream is the tree, so that is what gets
    asked — is `base` identical in content to the last commit the two histories
    share? Unrelated histories (a fresh repo whose only commit is the platform's
    synthetic one) have no merge base at all, and answer no."""
    try:
        common = _git(repo, "merge-base", ref, base).strip()
    except ValidationError:
        return False  # unrelated histories — a real join is needed
    if not common:
        return False
    result = subprocess.run(
        ["git", "diff", "--quiet", common, base],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _fast_forward_base(project_id: uuid.UUID, repo: Path, base: str, ref: str) -> dict:
    """Point `base` straight at `ref` — no merge commit, nothing to conflict.

    Same two steps as the tail of `_merge_ref_into_base` (CAS the ref, then sync
    the shared checkout) and the same rule about them: once the CAS lands the
    sync is durable, and a stale shared directory afterwards is loud
    housekeeping, not a failed sync."""
    for _attempt in range(_MERGE_RETRY_LIMIT):
        old_sha = _git(repo, "rev-parse", base).strip()
        new_sha = _git(repo, "rev-parse", ref).strip()
        if old_sha == new_sha:
            return {"synced": True, "commits": 0, "reason": "已是最新"}
        try:
            _git(repo, "update-ref", f"refs/heads/{base}", new_sha, old_sha)
        except ValidationError:
            continue  # base moved under us — re-read and retry
        try:
            _sync_shared_checkout(repo, base, new_sha)
        except ValidationError as exc:
            logger.exception(
                "%s advanced to %s but the shared checkout could not be synced",
                base,
                new_sha,
            )
            return {"synced": True, "fast_forward": True, "sync_failed": str(exc)}
        return {"synced": True, "fast_forward": True}
    return {
        "synced": False,
        "reason": f"同步失败：{base} 分支并发更新冲突过多，请重试",
    }


def sync_upstream(project_id: uuid.UUID, *, token: str | None = None) -> dict:
    """同步上游: bring the project's base branch up to the upstream's default
    branch.

    `token` is the platform App's installation token when the project is bound
    to GitHub, and the fetch authenticates with that and nothing else. An
    unbound project fetches with no credential at all: a private upstream it
    is not bound to fails here, visibly, rather than being read on a key the
    platform cannot account for.

    **For a bound project the base branch is a MIRROR of the upstream's default
    branch, not a branch of its own.** That is the whole design, and getting it
    wrong is what produced the mess this replaces: the sync used to be an
    unconditional `merge --no-ff`, so every tick minted a merge commit that
    existed only locally. Nothing ever removed them, every topic branch was cut
    from a base carrying the whole pile, and each one showed up as a "new"
    commit in that topic's PR — 39 of PR #488's 40 commits were
    `同步上游 upstream/main → main`, and this repo's own base was 41 such commits
    ahead of upstream while its tree was byte-identical (verified 2026-08-16).

    So: fast-forward whenever the base has no content of its own, which after
    采纳即合并 (#296) is always — a bound project never commits to its base
    locally. A real merge is reserved for the case that genuinely needs one: a
    base that HAS local content the upstream lacks (a project seeded with work
    before it was bound), where a fast-forward would silently discard it.
    Conflicts there abort cleanly and report, same contract as merge_topic."""
    repo = ensure_repo(project_id)
    if get_upstream(project_id) is None:
        return {"synced": False, "reason": "未关联上游仓库"}
    try:
        _git(
            repo,
            "fetch",
            UPSTREAM_REMOTE,
            timeout=120,
            env=_token_git_env(token) if token else None,
        )
        ref = _upstream_ref(repo)
    except ValidationError as exc:
        return {"synced": False, "reason": str(exc)}
    base = _base_branch(repo)
    behind = int(_git(repo, "rev-list", "--count", f"{base}..{ref}").strip() or "0")
    if _base_adds_nothing(repo, ref, base):
        # Covers "simply behind" AND "ahead only by contentless merges left by
        # the old implementation" — the second is why this is not just
        # `merge --ff-only`, which would refuse and mint merge #42.
        result = _fast_forward_base(project_id, repo, base, ref)
        if result.get("synced"):
            result.setdefault("commits", behind)
        return result
    if behind == 0:
        return {"synced": True, "commits": 0, "reason": "已是最新"}
    result = _merge_ref_into_base(
        project_id,
        repo,
        base,
        ref,
        f"chore: merge upstream {ref} into {base}",
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
    """采纳冲突 → 派芝士解决的前置：把主干合进话题的工作区，冲突以标记形式
    materialize 在文件里；返回冲突文件列表。芝士就在这个工作区里干活，解完冲突
    `git add` + `git commit` 就是那次合并提交，落在话题分支上，重试采纳即可干净
    合并。"""
    repo = ensure_repo(project_id)
    base = _base_branch(repo)
    return _materialize_conflicts(project_id, topic_id, _git(repo, "rev-parse", base))


def prepare_upstream_conflict_resolution(
    project_id: uuid.UUID, topic_id: uuid.UUID, *, token: str | None = None
) -> list[str]:
    """同步上游冲突 → 派芝士解决的前置。Same contract as
    `prepare_conflict_resolution`, but the side being merged in is the UPSTREAM
    branch rather than the project's base.

    `token` is the same credential `sync_upstream` fetches with: the App's
    installation token for a bound project, nothing for an unbound one. The
    conflict this materializes was found by a fetch that used it, and the
    re-fetch here reads the same private upstream.

    Why this exists at all: `sync_upstream` aborts cleanly on conflict and
    reports — which is the right thing for the shared repo, but on its own it is
    a dead end. Accepting a topic had an exit (routes/accept.py dispatches 芝士
    at the materialized conflict); syncing did not, so a project whose upstream
    had diverged simply could not pull, and every later sync hit the same wall.

    Accepting the resulting topic finishes the sync: the merge commit 芝士 makes
    carries upstream as a parent, so `merge_topic` folding it into base brings
    the upstream history along with the resolution."""
    repo = ensure_repo(project_id)
    # A commit id rather than a ref name: `upstream/main` means nothing inside
    # the worktree until it fetches, and a raw sha needs no name at all.
    _git(
        repo,
        "fetch",
        UPSTREAM_REMOTE,
        timeout=120,
        env=_token_git_env(token) if token else None,
    )
    return _materialize_conflicts(
        project_id, topic_id, _git(repo, "rev-parse", _upstream_ref(repo))
    )


def _materialize_conflicts(
    project_id: uuid.UUID, topic_id: uuid.UUID, merge_sha: str
) -> list[str]:
    """Put a merge with `merge_sha` on the topic's branch, conflicts and all,
    and name the files that conflicted.

    The conflict has to be ON THE BRANCH, not merely in a working tree: whoever
    resolves it may be working from a clone on another machine, and it is the
    branch they pull and push back to. It also has to be a real merge — the
    commit's second parent is what makes 采纳 bring the other side's history
    along, which is the whole reason the upstream variant exists.

    git cannot commit a conflicted tree, so the conflict travels the only way
    git can carry it: the markers themselves, committed. That is what the
    resolver receives either way — in a clone, in the sandbox, in an editor —
    and finishing it is an ordinary commit.

    This is the platform committing, which it otherwise does not do. The
    distinction is whose work it is: the merge is the platform's own act,
    performed because someone pressed 采纳, and the agent's own commits stay
    the agent's to make.

    `--allow-unrelated-histories` because the upstream variant merges a repo
    that shares no history with this one — the ordinary case for a project
    connected to an existing GitHub repo.
    """
    wt = _ensure_worktree(project_id, topic_id)
    with contextlib.suppress(ValidationError):
        _git(wt, "merge", "--abort")  # a resolution attempt someone walked away from
    with contextlib.suppress(ValidationError):
        _git(
            wt,
            "merge",
            "--no-commit",
            "--no-ff",
            "--allow-unrelated-histories",
            merge_sha.strip(),
            timeout=120,
        )
    files = [
        line
        for line in _git(wt, "diff", "--name-only", "--diff-filter=U").splitlines()
        if line.strip()
    ]
    _git(wt, "add", "-A")
    _git(
        wt,
        "commit",
        "-q",
        "--no-verify",
        "-m",
        "chore: merge for 芝士 to resolve" if files else "chore: merge",
        timeout=60,
    )
    return files


def upstream_default_branch(repo: Path, *, token: str | None = None) -> str | None:
    """The upstream's own default branch (what its HEAD points at), so a push
    lands where that repo actually keeps its trunk instead of a guessed name.

    `token` is the App's installation token for a bound project: a private
    upstream answers `ls-remote` to nothing else, and without it the caller
    falls back to guessing `main`."""
    try:
        out = _git(
            repo,
            "ls-remote",
            "--symref",
            UPSTREAM_REMOTE,
            "HEAD",
            timeout=60,
            env=_token_git_env(token) if token else None,
        )
    except ValidationError:
        return None
    for line in out.splitlines():
        if line.startswith("ref:"):
            ref = line.split()[1]
            return ref.rsplit("/", 1)[-1]
    return None


def _token_git_env(token: str) -> dict[str, str]:
    """Subprocess env that authenticates one git push or fetch with a GitHub
    token.

    The token travels via env var into an inline credential helper — never argv
    (visible in ps), never disk. The helper list is reset first so that nothing
    configured elsewhere (a helper in the backend's persistent HOME, say) can
    answer before this one and act as some other identity.
    """
    helper = (
        '!f() { echo username=x-access-token; echo "password=$CHEESE_GIT_TOKEN"; }; f'
    )
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": helper,
        "CHEESE_GIT_TOKEN": token,
    }


def push_topic_branch(project_id: uuid.UUID, topic_id: uuid.UUID, token: str) -> str:
    """Push the topic's branch to the upstream (PR-based accept, #188 §5.1).

    The branch as it stands is what the PR carries. --force-with-lease: a
    re-push after a conflict fix must move the remote branch, but never trample
    one somebody else moved."""
    repo = ensure_repo(project_id)
    if get_upstream(project_id) is None:
        raise ValidationError("未关联上游仓库，无法推分支")
    branch = branch_for_tree(tree_for_place(topic_id))
    if not _branch_exists(repo, branch):
        raise ValidationError("话题没有分支，无法推送")
    _git(
        repo,
        "push",
        "--force-with-lease",
        UPSTREAM_REMOTE,
        f"{branch}:{branch}",
        timeout=120,
        env=_token_git_env(token),
    )
    return branch


def pr_base_branch(project_id: uuid.UUID) -> str:
    """两阶段采纳 (PR迭代式): the base branch a topic's PR should target — same
    branch merge_topic() would merge into locally."""
    return _base_branch(ensure_repo(project_id))


def has_undelivered_commits(project_id: uuid.UUID, topic_id: uuid.UUID) -> bool:
    """Does this topic's branch hold anything the base branch does not?

    This is the fact behind "can this room deliver again". A room outlives the
    work done in it (#536), so it files a card, that card merges, and then work
    continues — the next task's commits land on the same branch and are, right
    then, undelivered. Answering from history instead ("has a card ever been
    accepted?") freezes the room after its first delivery, which is the whole
    of 一个 task 完成了可以再新开 task.

    False also covers the branch that never existed: nothing to deliver is
    nothing to deliver, and the caller's refusal reads the same either way.
    """
    repo = ensure_repo(project_id)
    branch = branch_for_tree(tree_for_place(topic_id))
    base = _base_branch(repo)
    if not _branch_exists(repo, branch) or not _branch_exists(repo, base):
        return False
    # Ahead-ness, not equality: the base moves under a long-lived room branch
    # all the time, and a room that is merely behind main still has its own
    # commits to deliver.
    return not _is_ancestor(repo, branch, base)


def topic_branch_exists(project_id: uuid.UUID, topic_id: uuid.UUID) -> bool:
    """Does this topic have a branch a PR could carry? Discussion-only topics
    never grow one — for them the PR path is NOT APPLICABLE (accept merges
    nothing and archives), which callers must distinguish from a push/API
    FAILURE (where accept must stop rather than silently direct-merge)."""
    return _branch_exists(
        ensure_repo(project_id), branch_for_tree(tree_for_place(topic_id))
    )


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
    topic_id: uuid.UUID,
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
                f"chore: merge {default} into {branch} before pushing the PR branch",
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
    _move_the_checkout_onto(project_id, topic_id, was=old_sha, now=new_sha)
    return {"synced": True, "base": default, "head": new_sha}


def _move_the_checkout_onto(
    project_id: uuid.UUID, topic_id: uuid.UUID, *, was: str, now: str
) -> None:
    """Carry the topic's checkout from `was` to `now` after its branch was
    moved by a bare ref write.

    Every other way this branch moves takes its worktree along — the agent
    commits IN the worktree, and a machine's push arrives through
    receive-pack, which git resolves against the checkout (see
    `_accept_a_push_to_a_checked_out_branch`). Writing the ref directly is the
    one path that does not, and it leaves the checkout describing a commit its
    own branch no longer points at: `git status` then reports the synced files
    as deletions the agent is about to commit back.

    Only when the checkout still matches `was` exactly — anything else is
    someone's uncommitted work, and this sync is not worth losing it over.
    Best-effort: the ref is what gets pushed, so a stale checkout must not fail
    the sync.
    """
    wt = _worktree_path(project_id, topic_id)
    if not (wt / ".git").exists():
        return  # no checkout yet — whoever makes one will start from the ref
    try:
        if _git(wt, "diff", "--name-only", was).strip():
            return
        _git(wt, "reset", "-q", "--hard", now)
    except ValidationError as exc:
        logger.warning(
            "topic checkout %s stayed on %s after the PR-base sync: %s", wt, was, exc
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
    the point (see the PR trailer). This prepares a branch for review, using
    whichever repo #192 connected the project to (not necessarily the same
    remote `push_topic_branch` above pushes to).

    Auth reuses `_token_git_env` (credential helper via env var, never argv)
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
    branch = branch_for_tree(tree_for_place(topic_id))
    if not _branch_exists(repo_path, branch):
        raise ValidationError("话题还没有可推送的分支")
    url = _github_push_url(owner, repo)
    env = _token_git_env(token)
    refspec = f"{branch}:refs/heads/{remote_branch}"
    try:
        _git(repo_path, "push", url, refspec, timeout=120, env=env)
    except ValidationError as exc:
        if not _is_workflow_permission_rejection(str(exc)):
            raise
        synced = _sync_remote_base_into_topic_branch(
            project_id, repo_path, topic_id, branch, url=url, env=env
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
    wt = _ensure_worktree(project_id, topic_id)
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
    # container users.  The backend runs as uid 1001 while the sandbox image runs as
    # uid 1000; if the hook forwarder creates this directory first, its normal
    # 0755 mode lets the backend read events but not park or remove them.
    spool = d / "cheese-spool"
    spool.mkdir(parents=True, exist_ok=True)
    _loosen(str(spool), 0o777)
    # Same treatment for `cheese await`'s output logs: they live in the session
    # mount (not the container's own filesystem) so a multi-hour command's output
    # outlives the container that ran it, and not in the worktree so it never
    # reaches a commit.
    awaited = d / "cheese-await"
    awaited.mkdir(parents=True, exist_ok=True)
    _loosen(str(awaited), 0o777)
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
    """Host path of the topic's hook-event spool (WAL). The screen writes
    here via CHEESE_HOOK_SPOOL=/home/node/.claude/cheese-spool (the session dir
    mounts to /home/node/.claude), and the backend reconciles from it. Mirrors
    session_dir's base so both sides agree on ONE location."""
    return identity_mod.session_dir(project_id, topic_id) / "cheese-spool"


def await_log_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Host path of the topic's `cheese await` output logs. The container writes
    here via CHEESE_AWAIT_LOGS=/home/node/.claude/cheese-await (the session dir
    mounts to /home/node/.claude), so the output of a command that runs for hours
    survives the container being rebuilt under it. Mirrors spool_dir's base so
    both sides agree on ONE location."""
    return identity_mod.session_dir(project_id, topic_id) / "cheese-await"


# `docker info` costs ~50ms, and the answer changes only when someone starts or
# stops the daemon — so it is cached for this long rather than paid per call.
_SANDBOX_PROBE_TTL_S = 30.0
_sandbox_probe: tuple[float, bool] | None = None


def docker_installed() -> bool:
    """The binary is on PATH. Says nothing about whether it can be used."""
    return shutil.which("docker") is not None


def sandbox_available() -> bool:
    """The sandbox can actually run something — the binary exists AND its daemon
    answers.

    The binary alone used to be the whole check, which is wrong in the one case
    that happens most: docker installed, daemon not started. Callers then took
    the "yes" and failed inside `docker run`, and the message they printed said
    docker was not FOUND — naming the one problem the host did not have."""
    global _sandbox_probe
    if not docker_installed():
        return False
    now = time.monotonic()
    if _sandbox_probe is not None and now - _sandbox_probe[0] < _SANDBOX_PROBE_TTL_S:
        return _sandbox_probe[1]
    try:
        ok = (
            subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                timeout=5,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        ok = False
    _sandbox_probe = (now, ok)
    return ok


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
            "stderr": (
                "sandbox 不可用：未找到 docker"
                if not docker_installed()
                else "sandbox 不可用：docker 已安装但守护进程没有响应"
            ),
        }
    # A topic's tree is a linked worktree whose `.git` pointer only resolves
    # with the main repo's store mounted too (see sandbox_vcs_mounts); the
    # project-level tree (topic_id=None) IS the main repo, no extra mount needed.
    vcs_mounts = (
        sandbox_vcs_mounts(project_id, topic_id) if topic_id is not None else []
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
                # A crashing process must not dump its whole address space into
                # the bind-mounted worktree (frontend cores were 1-2GB each).
                "--ulimit",
                "core=0",
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


GATE_TAIL_CHARS = 4000
