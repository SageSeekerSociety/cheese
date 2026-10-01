"""What the repository says about itself, read where the checkout is.

pi discovers AGENTS.md and CLAUDE.md by walking from its working directory up to
`/`, which in a room would go through the session host's own directories. So the
discovery is off (`--no-context-files`), and this reads the one directory a room
is entitled to: the checkout it was given, on the room's machine.

It has to be read, not skipped. A repository that Cheese hosts must never have
to change in order to be hosted, and its CLAUDE.md is how it says what it needs
— the other harness reads one, and a pi room that did not would be the same
repository being told different things by two teammates.

Run on the machine as a script (`python3 - <checkout>`, its source on stdin,
`machine.py`), so it is standard library only and imports nothing of ours. It
prints one JSON object: ``{"context": <text>}``, empty when the repository says
nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys

CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")

# Big enough for any of these written to be read by a person, small enough that
# a generated file checked in under one of these names cannot displace the room.
CONTEXT_LIMIT = 64 * 1024

# `@relative/path.md` inside one of these files is how a repository splits its
# rules across files; expanded the way the executor's own instruction reader
# does, so both harnesses read the same repository the same way. Relative
# markdown only — anything else stays written as it was, unguessed at.
IMPORT_LIMIT = 5

# Directories no convention lives in, and whose walk would cost more than the
# rest of the checkout together: a repository's dependencies are not its rules.
SKIPPED = (".git", "node_modules")

_IMPORT = re.compile(r"(?<![\w`])@([^\s`]+)")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return stream.read()
    except OSError:
        return None


def _expand(text: str, base: str, depth: int) -> str:
    def reference(match: re.Match) -> str:
        ref = match.group(1)
        if depth >= IMPORT_LIMIT or os.path.isabs(ref) or not ref.endswith(".md"):
            return match.group(0)
        target = os.path.normpath(os.path.join(base, ref))
        body = _read(target)
        if body is None:
            return match.group(0)
        return _expand(body, os.path.dirname(target), depth + 1)

    return _IMPORT.sub(reference, text)


def _conventions(root: str) -> list[str]:
    """`.claude/rules/**` and the nested CLAUDE.md files that scope themselves
    to a subdirectory, walked in sorted order so an unchanged tree reads the same.
    settings.json is deliberately not one of them: it is configuration, some of
    it executable, and reading it into a prompt would hand whoever can open a PR
    the room's hook runner."""
    found = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in SKIPPED)
        relative = os.path.relpath(directory, root).split(os.sep)
        for name in sorted(files):
            full = os.path.join(directory, name)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            is_rule = relative[:2] == [".claude", "rules"]
            nested = directory != root and name in ("CLAUDE.md", "CLAUDE.local.md")
            if is_rule or nested or (directory == root and name in CONTEXT_FILES):
                found.append(full)
    return found


def context(root: str) -> str:
    """Everything the repository at `root` says about itself, as one block."""
    root = os.path.abspath(root)
    parts: list[str] = []
    seen_paths: set[str] = set()
    seen_bodies: set[str] = set()
    # Root-level names lead in their documented order, then the walk's find.
    for file in [os.path.join(root, name) for name in CONTEXT_FILES] + _conventions(
        root
    ):
        resolved = os.path.realpath(file)
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        body = _read(resolved)
        if body is None or not body.strip():
            continue
        # The two names are often one file: a repository that keeps CLAUDE.md
        # and links AGENTS.md at it should not have it read into the turn twice.
        expanded = _expand(body, os.path.dirname(resolved), 0)
        if expanded in seen_bodies:
            continue
        seen_bodies.add(expanded)
        name = os.path.relpath(file, root)
        size = len(expanded.encode("utf-8"))
        if size > CONTEXT_LIMIT:
            kept = expanded.encode("utf-8")[:CONTEXT_LIMIT].decode("utf-8", "ignore")
            expanded = f"{kept}\n\n[{name} truncated at {CONTEXT_LIMIT} bytes]"
        parts.append(f"## {name}\n\n{expanded}")
    if not parts:
        return ""
    return f"# 这个仓库自己的说明（{root}）\n\n" + "\n\n".join(parts)


if __name__ == "__main__":
    print(json.dumps({"context": context(sys.argv[1])}, ensure_ascii=False))
