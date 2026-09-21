"""项目那唯一一个 git 源，以及跟着它走的那些平台侧目录。

这个包里只有仓库这一侧：git 子进程、检出目录、会话 home 与 spool、退役检出的清
理。用户给项目的资料、贴进房间的文件、发布出来的预览产物都不在这里——它们不在任
何 git 树上，住在 :mod:`app.domain.library.service`。

平台自己在这里只做检出级的操作，不写任何人的提交：干活的分身在自己的机器上
`git commit`，再把分支推回来。
"""

import contextlib
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.platform_failures import WORKSPACE_VCS_PERMS_CODE
from app.domain.repository import identity as identity_mod

logger = logging.getLogger("cheesex.repository")


class GitTimeoutError(ValidationError):
    """A git subprocess ran past its timeout. Only ever raised AFTER the
    process is confirmed dead (SIGTERM, escalating to SIGKILL, then waited
    on) — never while it might still be running, so callers can safely clean
    up whatever it was holding (worktree, lock file)."""


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


def _git(
    repo: Path, *args: str, timeout: int = 20, env: dict[str, str] | None = None
) -> str:
    result = _run_subprocess(["git", *args], repo, timeout, env)
    if result.returncode != 0:
        # git reports merge conflicts on stdout with an empty stderr — fall back
        # so the caller's error isn't blank.
        detail = result.stderr.strip() or result.stdout.strip()
        if _permission_denied(detail) or _names_a_store_it_cannot_enter(detail, repo):
            raise WorkspacePermissionError(_permission_hint(f"git {args[0]}: {detail}"))
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


def _permission_hint(detail: str) -> str:
    return (
        f"工作区仓库里有当前进程（uid={os.getuid()}）无权访问的文件。"
        "请检查历史工作区的目录权限。"
        f"原始报错：{detail}"
    )


SANDBOX_TOPICS_ROOT = "/topics"


def sandbox_topic_workdir(topic_id: uuid.UUID) -> str:
    """Stable room session directory, independent of task checkout names."""
    return f"{SANDBOX_TOPICS_ROOT}/topic_{topic_id.hex[:8]}"


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


def remove_worktree(project_id: uuid.UUID, wt: Path) -> bool:
    """Remove a retired checkout after the caller verifies publication and no writers.

    Keep its branch intact. The repository may already be gone, in which case
    there is no worktree registration to remove. Return whether the checkout
    is absent after cleanup.
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


_TOPIC_TREE_DIR = re.compile(r"^(?:topic|task)_([0-9a-f]{8})$")


def topic_worktrees_on_disk() -> list[tuple[uuid.UUID, str, Path]]:
    """Every topic tree directory under the workspace, as
    ``(project_id, tree id hex prefix, path)`` — the disk's own account of what
    is there, for the sweep that reconciles it against the database.

    Only the eight hex digits are on disk, never the whole
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


_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"


_CLI_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "cheese"


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
    # Create the hook WAL before the sandbox starts. Both the hook forwarder
    # and the backend need directory write access to park and remove events.
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
    """Host path of the topic's hook-event spool (WAL). The screen writes
    here via CHEESE_HOOK_SPOOL=/home/node/.claude/cheese-spool (the session dir
    mounts to /home/node/.claude), and the backend reconciles from it. Mirrors
    session_dir's base so both sides agree on ONE location."""
    return identity_mod.session_dir(project_id, topic_id) / "cheese-spool"
