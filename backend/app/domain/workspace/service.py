"""Local attachments, session files and retired workspace cleanup."""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.platform_failures import WORKSPACE_VCS_PERMS_CODE
from app.domain.workspace import identity as identity_mod
from app.domain.workspace.textfile import (
    MAX_TEXT_BYTES,
    content_version,
    decode_text,
)

logger = logging.getLogger("cheesex.workspace")


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


def _safe_path(repo: Path, rel: str) -> Path:
    target = (repo / rel).resolve()
    if repo not in target.parents and target != repo:
        raise ValidationError("path escapes the project workspace")
    if ".git" in target.parts:
        raise ValidationError("cannot touch .git")
    return target


def _read_text_path(target: Path, path: str) -> dict:
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


def room_files_root(project_id: uuid.UUID, room_id: uuid.UUID) -> Path:
    """Room attachments and published previews have no code branch."""
    root = (
        Path(settings.workspace_root) / ".room-files" / str(project_id) / str(room_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def write_room_file(
    project_id: uuid.UUID, room_id: uuid.UUID, path: str, data: bytes
) -> None:
    if path.split("/")[0] == LIBRARY_PREFIX:
        # `library/…` 是资料库那一份的地址（见 `read_attachment`）。房间里再写一个
        # 同名的东西，读的人就会拿到房间那份、以为看的是资料库里的原件。
        raise ValidationError(f"{LIBRARY_PREFIX}/ 留给资料库，房间文件不能写在这里")
    target = _safe_path(room_files_root(project_id, room_id), path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_room_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bytes:
    target = _safe_path(room_files_root(project_id, room_id), path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target.read_bytes()


def read_room_text_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> dict:
    return _read_text_path(_safe_path(room_files_root(project_id, room_id), path), path)


LIBRARY_PREFIX = "library"


def library_ref(name: str) -> str:
    """资料库里那一份在消息和工作目录里的地址。"""
    return f"{LIBRARY_PREFIX}/{name}"


def library_name(path: str) -> str | None:
    """这个地址指的是资料库里哪一份,不是的话给 None。"""
    prefix = f"{LIBRARY_PREFIX}/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def read_attachment(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bytes:
    """一个附件的字节:资料库里那一份,或者只属于这个房间的那一份。"""
    name = library_name(path)
    if name is not None:
        return read_library_file(project_id, name)
    return read_room_file(project_id, room_id, path)


def read_attachment_text(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> dict:
    """同一个地址，读成文本(二进制的那一份照旧只回元数据和版本)。"""
    name = library_name(path)
    if name is not None:
        target = _safe_path(library_root(project_id), name)
        if not target.is_file():
            # 一条旧消息里的引用，而那份资料已经被扔掉了。说清是哪一种打不开：这个
            # 地址没错，是东西不在了。
            raise ValidationError("这份资料已经不在资料库里")
        return _read_text_path(target, path)
    return read_room_text_file(project_id, room_id, path)


def library_root(project_id: uuid.UUID) -> Path:
    root = Path(settings.workspace_root) / ".library" / str(project_id)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _next_name(name: str, attempt: int) -> str:
    if attempt == 1:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return f"{name}({attempt})"
    return f"{stem}({attempt}).{ext}"


def write_library_file(project_id: uuid.UUID, name: str, data: bytes) -> str:
    """Keep the name the user gave it; a taken name takes the next `(n)`.

    Allocating the name IS the write (`open(..., "xb")`): two uploads of the
    same name in flight is the case this exists for, and check-then-write loses
    one of them. Returns the name it ended up with."""
    root = library_root(project_id)
    for attempt in range(1, 1000):
        candidate = _next_name(name, attempt)
        target = _safe_path(root, candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as sink:
                sink.write(data)
        except FileExistsError:
            continue
        return candidate
    raise ValidationError(f"同名文件太多：{name}")


def read_library_file(project_id: uuid.UUID, path: str) -> bytes:
    target = _safe_path(library_root(project_id), path)
    if not target.is_file():
        raise NotFoundError("资料库里没有这份文件")
    return target.read_bytes()


def delete_library_file(project_id: uuid.UUID, name: str) -> None:
    """扔掉一份资料。

    旧消息里引用它的那枚 chip 随之打不开了，这是对的：那条引用指的就是这一份，而
    这一份没有了——在它的位置上摆一份别的东西，才是把读者读到的内容换掉。"""
    target = _safe_path(library_root(project_id), name)
    if not target.is_file():
        raise NotFoundError("资料库里没有这份文件")
    target.unlink()


def list_library_files(project_id: uuid.UUID) -> list[dict]:
    """Newest first: the file someone just gave the project is the one they are
    about to reference."""
    root = library_root(project_id)
    files = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        stat = entry.stat()
        files.append(
            {
                "path": str(entry.relative_to(root)),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files


def read_preview_file(
    project_id: uuid.UUID, topic_id: uuid.UUID, entry: str, relative: str
) -> bytes:
    """Read web assets only inside the explicitly selected artifact's directory."""
    parts = relative.split("/")
    if not relative or any(
        not part or part.startswith(".") or "\\" in part or "\x00" in part
        for part in parts
    ):
        raise ValidationError("preview path unavailable")
    tree = room_files_root(project_id, topic_id)
    directory = _safe_path(tree, str(PurePosixPath(entry).parent))
    target = _safe_path(directory, relative)
    return read_room_file(project_id, topic_id, str(target.relative_to(tree)))


def preview_file_version(
    project_id: uuid.UUID, topic_id: uuid.UUID, entry: str
) -> str | None:
    """Track HTML edits without loading a large artifact into the editor API."""
    target = _safe_path(room_files_root(project_id, topic_id), entry)
    try:
        with target.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()[:16]
    except OSError:
        # The metadata still names a missing/unreadable artifact; the file API
        # supplies its existing detailed error state to the preview panel.
        return None


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
