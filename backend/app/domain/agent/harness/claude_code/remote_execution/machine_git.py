"""The checkout's history, for a session that only reads the machine: a
document's 芝士 asking what changed (`pi/platform.ts`, the `git` tool).

A fixed set of git's reading commands, answered by this executor in its own
process, never a command line the caller writes: the caller names which one
and fills in revisions and a path, and nothing it sends becomes an option.

Each runs beside whatever the room's agent is doing in the same checkout, so
none of them may write there. Log, show and blame never do. Status refreshes
the index when it can, and ``GIT_OPTIONAL_LOCKS=0`` is git's own switch for a
reader that must not (it is what editors and prompts set); a diff against the
working tree refreshes it whatever that says, so status and diff read a copy
of the index of their own (``GIT_INDEX_FILE``), refreshed and thrown away. The
programs a repository can configure git to run while it reads (an external
diff, a text conversion, a file-system monitor) are switched off, so a read
runs git and nothing else.

Shipped beside `runtime.py` (`RELEASE_FILES`), standard library only, and run
by Pythons as old as 3.9.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

#: The most of one answer brought back.
OUTPUT_LIMIT = 100 * 1024
#: How long one command may take.
TIMEOUT_S = 30
#: The most commits a log lists.
LOG_LIMIT = 200

#: A revision as the caller may name one: a branch, a tag, a commit, with
#: git's suffixes (`main~2`, `HEAD^`, `v1.0^{}`), never something that reads
#: as an option.
REVISION = re.compile(r"[A-Za-z0-9._/@~^{}+-]+")

#: Before every command: what keeps it from running anything but git.
QUIET = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.pager=cat",
    "-c",
    "color.ui=false",
    "--no-pager",
)


class NotAGitCheckout(RuntimeError):
    """The workspace is not a git checkout; the message is git's."""


class Refused(ValueError):
    """What was asked cannot be asked; the message says why."""


def _revision(value: object, name: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.startswith("-") or not REVISION.fullmatch(text):
        raise Refused(f"{name} 不是一个分支名或提交号：{text}")
    return text


def _path(value: object) -> list[str]:
    if value in (None, ""):
        return []
    return ["--", str(value)]


def argv(request: dict) -> list[str]:
    """The git command line for one request, after `git -C <root>`."""
    command = request.get("command")
    path = _path(request.get("path"))
    stat = ["--stat"] if request.get("stat") else []
    if command == "status":
        return ["status", "--short", "--branch", *path]
    if command == "log":
        limit = min(max(int(request.get("limit") or 20), 1), LOG_LIMIT)
        revision = _revision(request.get("revision"), "revision")
        return [
            "log",
            f"--max-count={limit}",
            "--date=iso",
            "--format=%h %ad %an%n    %s",
            *stat,
            *([revision] if revision else []),
            *path,
        ]
    if command == "diff":
        # Against `base` (HEAD when not named): what the working tree, with
        # what is not committed yet, has that `base` has not. With
        # `since_branched`, against where the checkout left `base` instead,
        # which is "what this work changed" when `base` has moved on.
        base = _revision(request.get("base"), "base") or "HEAD"
        since = ["--merge-base"] if request.get("since_branched") else []
        return ["diff", "--no-ext-diff", "--no-textconv", *stat, *since, base, *path]
    if command == "show":
        revision = _revision(request.get("revision"), "revision") or "HEAD"
        return ["show", "--no-ext-diff", "--no-textconv", *stat, revision, *path]
    if command == "blame":
        if not path:
            raise Refused("blame 要给一个文件路径")
        revision = _revision(request.get("revision"), "revision")
        return ["blame", "--date=short", *([revision] if revision else []), *path]
    raise Refused(f"不支持的 git 命令：{command}")


def _index_copy(root: str, scratch: str, env: dict) -> str:
    """A copy of the checkout's index in ``scratch``, for a command that would
    refresh the real one. The index is replaced whole when git writes it, so
    the copy is one version of it, never half of two."""
    found = subprocess.run(
        ["git", "-C", root, "rev-parse", "--git-path", "index"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=TIMEOUT_S,
        env=env,
    )
    if found.returncode != 0:
        raise NotAGitCheckout(found.stderr.decode("utf-8", "replace").strip())
    # Relative to the checkout when git names it so, as `-C` read it.
    index = os.path.join(root, found.stdout.decode().strip())
    copy = os.path.join(scratch, "index")
    if os.path.exists(index):
        shutil.copyfile(index, copy)
    return copy


def answer(request: dict, root: str = ".") -> dict:
    """One reading command in the checkout at ``root``: what it printed, or
    `{"error": ...}` saying why it could not."""
    try:
        args = argv(request)
    except (Refused, ValueError) as refused:
        return {"error": str(refused)}
    env = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_EXTERNAL_DIFF": "",
    }
    env.pop("GIT_INDEX_FILE", None)
    with tempfile.TemporaryDirectory() as scratch:
        try:
            if args[0] in ("status", "diff"):
                env["GIT_INDEX_FILE"] = _index_copy(root, scratch, env)
            done = subprocess.run(
                ["git", "-C", root, *QUIET, *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=TIMEOUT_S,
                env=env,
            )
        except FileNotFoundError:
            return {"error": "这台电脑上没有 git"}
        except NotAGitCheckout as missing:
            return {"error": str(missing) or "工作目录不是一个 git 仓库"}
        except subprocess.TimeoutExpired:
            return {"error": f"git 超过 {TIMEOUT_S} 秒没有答完"}
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip()
        return {"error": said or f"git 以状态 {done.returncode} 结束"}
    output = done.stdout[: OUTPUT_LIMIT + 1]
    text = output[:OUTPUT_LIMIT].decode("utf-8", "replace")
    return {"output": text, "truncated": len(output) > OUTPUT_LIMIT}
