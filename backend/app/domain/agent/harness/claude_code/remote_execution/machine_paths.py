"""Files a command names on this host, spelled where the executor holds them.

A Bash command runs on the executor, but the build names some files by where
it keeps them here: a skill in its config directory, and the file it sends a
command's output to. Shipped beside `client.py` (`release.sources`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

if __package__:
    from app.domain.agent.executor_transport import session_path
else:
    from executor_transport import session_path

# The build's file for one command's output, under its temporary directory:
# `claude-<uid>/<project>/<session>/tasks/<id>.output`.
_TASK_OUTPUT = r"(?:[^/\s'\"`]+/)+tasks/[A-Za-z0-9_-]+\.output"
# A path that reads the same unquoted, so it can stand where one was written.
_PLAIN = re.compile(r"[A-Za-z0-9_./:@+-]+")


def skill_places(target):
    """Where the executor holds each skill the session names by an entry that
    is not the project's root skill of that name: a subdirectory's, and one
    written without its `paths` (`release.SKILL_PLACES`)."""
    path = Path(target.get("target_file", "")).parent / "skill-places.json"
    try:
        places = json.loads(path.read_text())["places"]
    except (OSError, ValueError, KeyError):
        return {}
    return {name: place["place"] for name, place in places.items()}


def skill_paths(target, text):
    """`text` with each skill file named in the session's config dir named
    where the executor holds it: a project skill in the project's
    `.claude/skills`, one the platform shipped in the executor's config dir.
    `proxy.js` `skillPath` is the same mapping, for the file tools."""
    skills = target["central_config"] + "/skills/"
    project = session_path(target["workspace"]) + "/.claude/skills/"
    shipped = set(target.get("shipped_skills") or [])
    executor = target.get("executor_config")
    places = skill_places(target)

    def place(match):
        name = match.group(1)
        if name in places:
            return places[name]
        if name in shipped:
            return f"{executor}/skills/{name}" if executor else match.group(0)
        return project + name

    return re.sub(re.escape(skills) + r"([^/\s'\"`]+)", place, text)


# The build sends every Bash command's output to a file of its own here, and
# keeps the file of one it ran in the background: its TaskOutput, its
# notification and Monitor read it. A command that reads it later — `tail`,
# `cat`, `grep` — runs on the executor, where the file is not. The executor's
# record of the command holds the same bytes (`runtime` `keep`), so the file
# is named by the record's path there. Which record holds which file is kept
# in `task-outputs/` beside the target, one entry per file.


def _places(target):
    return Path(target["target_file"]).parent / "task-outputs"


def _entry(target, name):
    return _places(target) / hashlib.sha256(name.encode()).hexdigest()[:32]


def task_output(target):
    """This command's output file, relative to the build's temporary
    directory, when the build sent its output to one; otherwise None."""
    try:
        if sys.platform == "linux":
            path = os.readlink("/proc/self/fd/1")
        elif sys.platform == "darwin":
            # deferred-import: fcntl is absent on Windows, where this never runs
            import fcntl

            path = fcntl.fcntl(1, fcntl.F_GETPATH, bytes(1024)).split(b"\0")[0]
            path = path.decode()
        else:
            return None
    except (OSError, UnicodeDecodeError):
        return None
    root = os.path.realpath(target["central_tmp"]) + "/"
    path = os.path.realpath(path)
    if not path.startswith(root) or not re.fullmatch(_TASK_OUTPUT, path[len(root) :]):
        return None
    return path[len(root) :]


def keep_output(target, name, command_id, answer):
    """Record where the executor holds the output file `name`; whether it
    does. An executor that does not keep it, or holds it at a path a command
    could not name unquoted, leaves the file named as it was."""
    held = answer.get("held")
    if not held or not _PLAIN.fullmatch(session_path(held)):
        return False
    entry = _entry(target, name)
    entry.parent.mkdir(exist_ok=True)
    temporary = entry.with_name(entry.name + "." + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps({"name": name, "command_id": command_id, "held": session_path(held)})
    )
    temporary.replace(entry)
    return True


def dropped_outputs(target):
    """The commands whose output file the build has since deleted — one it
    waited for, whose output it took — which the executor forgets. Each is
    told once."""
    found = []
    places = _places(target)
    for entry in places.iterdir() if places.is_dir() else ():
        try:
            kept = json.loads(entry.read_text())
        except (OSError, ValueError):
            continue
        if not (Path(target["central_tmp"]) / kept["name"]).exists():
            entry.unlink(missing_ok=True)
            found.append(kept["command_id"])
    return found


def task_outputs(target, text):
    """`text` with each output file the executor holds named where it does."""
    root = target.get("central_tmp")
    if not root or root not in text:
        return text

    def place(match):
        try:
            kept = json.loads(_entry(target, match.group(1)).read_text())
        except (OSError, ValueError):
            return match.group(0)
        return kept["held"]

    return re.sub(re.escape(root) + "/(" + _TASK_OUTPUT + ")", place, text)
