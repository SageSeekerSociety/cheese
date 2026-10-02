"""The skills pi finds in a project, found the way pi finds them.

A room starts pi with `--no-skills`, which keeps out what the machine's owner
keeps in their own home and also drops every skill the project itself carries.
The project's come back through `--skill`, named here in the order pi 1.0.0
resolves them when it opens the project, trusted, on its own
(`src/core/package-manager.ts` at tag v1.0.0; 0.85.1 到 1.0.0 之间这份文件只动
了内建扩展与 npm 命令的解析，技能发现一个字没变):

1. the plain paths in the project's `.pi/settings.json` `skills` array,
   relative to `.pi/`;
2. `<cwd>/.pi/skills`;
3. `.agents/skills` in the working directory and each directory above it, up
   to the git root, or to the filesystem root outside a repository, leaving
   out the user's own `~/.agents/skills`.

pi keeps the first skill of a name. Not carried over: the pattern entries of
that settings array (`!`, `+`, `-` and globs, which switch discovered skills on
and off), and the `.gitignore`, `.ignore` and `.fdignore` files pi honours
inside a skill directory.

The project is on the room's machine and pi on the session host, so this runs
on the machine as a script (`python3 - <checkout>`, its source on stdin,
`machine.py`) and prints the skills with their files; the runner keeps a copy of
them where pi can load them. Standard library only, importing nothing of ours.
"""

from __future__ import annotations

import base64
import json
import os
import pwd
import sys
from pathlib import Path

#: What one skill's directory may bring across, file by file and in all: a
#: skill is instructions and the small scripts they name, not a dataset.
FILE_LIMIT = 2 * 1024 * 1024
TOTAL_LIMIT = 16 * 1024 * 1024


def entries(directory: Path, loose_at_top: bool, root: Path | None = None) -> list[str]:
    """pi's `collectSkillEntries`: a directory holding SKILL.md is one skill;
    otherwise its subdirectories are searched, and loose `.md` files count at
    the top of a `.pi/skills` tree and below the top of an `.agents/skills`
    one (the two discovery modes, told apart here by `loose_at_top`).
    Directory order is the filesystem's, as `readdirSync` returns it."""
    root = root or directory
    if not directory.exists():
        return []
    try:
        found = list(os.scandir(directory))
    except OSError:
        return []
    for entry in found:
        if entry.name == "SKILL.md" and entry.is_file():
            return [entry.path]
    collected: list[str] = []
    for entry in found:
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue
        is_dir, is_file = entry.is_dir(), entry.is_file()
        if is_file and entry.name.endswith(".md"):
            at_top = directory == root
            if at_top == loose_at_top:
                collected.append(entry.path)
            continue
        if is_dir:
            collected += entries(Path(entry.path), loose_at_top, root)
    return collected


def _settings_paths(cwd: Path) -> list[str]:
    base = cwd / ".pi"
    try:
        listed = json.loads((base / "settings.json").read_text()).get("skills") or []
    except (OSError, ValueError, AttributeError):
        return []
    found: list[str] = []
    for item in listed:
        if not isinstance(item, str) or not item.strip():
            continue
        item = item.strip()
        if item[0] in "!+-" or "*" in item or "?" in item:
            continue
        path = Path(item).expanduser()
        path = path if path.is_absolute() else base / path
        path = Path(os.path.normpath(path))
        if path.is_file():
            found.append(str(path))
        elif path.is_dir():
            found += entries(path, True)
    return found


def _git_root(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def _homes() -> set[Path]:
    # The home pi itself would leave out, and the account's own: in a room HOME
    # is the session's, and the machine owner's `~/.agents/skills` is exactly
    # what `--no-skills` is there to keep out.
    homes = {Path(os.path.expanduser("~"))}
    try:
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except KeyError:
        pass
    return {Path(os.path.abspath(home)) / ".agents/skills" for home in homes}


def project_skills(cwd: str) -> list[str]:
    """Every skill file pi would load from the project at `cwd`, in its order."""
    start = Path(os.path.abspath(cwd))
    found = _settings_paths(start) + entries(start / ".pi/skills", True)
    top = _git_root(start)
    excluded = _homes()
    for directory in (start, *start.parents):
        skills = directory / ".agents/skills"
        if skills not in excluded:
            found += entries(skills, False)
        if directory == top:
            break
    seen: set[str] = set()
    unique = []
    for path in found:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            unique.append(path)
    return unique


def with_files(cwd: str) -> dict:
    """The skills at `cwd` and every file each one carries, by absolute path:
    a SKILL.md brings its whole directory (the references and scripts it names
    are in it), a loose skill file only itself. A file over `FILE_LIMIT`, or past
    `TOTAL_LIMIT` in all, is left on the machine, where a command still finds it."""
    skills = project_skills(cwd)
    files: dict[str, str] = {}
    total = 0
    for skill in skills:
        if os.path.basename(skill) != "SKILL.md":
            members = [skill]
        else:
            members = []
            for directory, names, found in os.walk(os.path.dirname(skill)):
                names[:] = sorted(n for n in names if n not in (".git", "node_modules"))
                members += [os.path.join(directory, name) for name in sorted(found)]
        for member in members:
            try:
                size = os.path.getsize(member)
                if member in files or size > FILE_LIMIT or total + size > TOTAL_LIMIT:
                    continue
                with open(member, "rb") as stream:
                    files[member] = base64.b64encode(stream.read()).decode()
                total += size
            except OSError:
                continue
    return {"skills": skills, "files": files}


if __name__ == "__main__":
    print(json.dumps(with_files(sys.argv[1])))
