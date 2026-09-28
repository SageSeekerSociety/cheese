"""The skills pi finds in a project, found the way pi finds them.

A room starts pi with `--no-skills`, which keeps out what the machine's owner
keeps in their own home and also drops every skill the project itself carries.
The project's come back through `--skill`, named here in the order pi 0.85.1
resolves them when it opens the project, trusted, on its own
(`src/core/package-manager.ts` at tag v0.85.1):

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

Standard library only: this runs in the runner archive on the machine.
"""

import json
import os
import pwd
from pathlib import Path


def entries(directory: Path, mode: str, root: Path | None = None) -> list[str]:
    """pi's `collectSkillEntries`: a directory holding SKILL.md is one skill;
    otherwise its subdirectories are searched, and loose `.md` files count at
    the top of a `.pi/skills` tree and below the top of an `.agents/skills`
    one. Directory order is the filesystem's, as `readdirSync` returns it."""
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
            if (mode == "pi" and at_top) or (mode == "agents" and not at_top):
                collected.append(entry.path)
            continue
        if is_dir:
            collected += entries(Path(entry.path), mode, root)
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
            found += entries(path, "pi")
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
    found = _settings_paths(start) + entries(start / ".pi/skills", "pi")
    top = _git_root(start)
    excluded = _homes()
    for directory in (start, *start.parents):
        skills = directory / ".agents/skills"
        if skills not in excluded:
            found += entries(skills, "agents")
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
