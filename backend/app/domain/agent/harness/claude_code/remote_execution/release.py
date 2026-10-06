"""Install released helpers without replacing the native conversation process."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import types
from importlib.metadata import distribution
from pathlib import Path

# What a mountpoint is doing — told apart in the one way `os.path.ismount` cannot.
#
# When a forwarded_fs server process dies, its mount does NOT go away: the
# directory stays occupied and answers every stat with ENOTCONN. `os.path.ismount`
# swallows that OSError and returns False, which every caller reads as "nothing is
# mounted here" — so a mount is attempted onto it and fails, and the cleanup that
# would have removed it skips it. Nothing ever clears it again.
#
# It is not only the room's problem. Anything that walks the directory blocks
# there, `actions/checkout` included: one left in a CI runner's workspace on
# 2026-09-15 wedged every job that machine picked up afterwards, each in a
# 15-minute timeout with an empty workspace and no git process, until the mount
# was released by hand.
MOUNT_LIVE = "live"
MOUNT_DEAD = "dead"
MOUNT_NONE = "none"


def mount_state(path):
    """``live``, ``dead`` or ``none`` for one mountpoint."""
    try:
        os.lstat(path)
    except OSError as error:
        if error.errno in (errno.ENOTCONN, errno.ETIMEDOUT, errno.EHOSTDOWN):
            return MOUNT_DEAD
        return MOUNT_NONE
    return MOUNT_LIVE if os.path.ismount(path) else MOUNT_NONE


def release_mount(path):
    """Unmount ``path`` whatever state it is in. True when nothing is left there.

    Takes a dead mount too, which is the case the callers actually need: a live
    one they could have found themselves.
    """
    if mount_state(path) == MOUNT_NONE:
        return True
    unmount = shutil.which("fusermount3") or shutil.which("fusermount")
    if unmount is None:
        return False
    # Plain unmount first; lazy only if the directory is still occupied, since a
    # lazy unmount detaches a mount that may still have a live reader.
    for flag in ("-u", "-uz"):
        subprocess.run([unmount, flag, str(path)], capture_output=True, timeout=10)
        if mount_state(path) == MOUNT_NONE:
            return True
    return False


def sources():
    from app.domain.agent import executor_transport

    directory = Path(__file__).parent
    result = {
        name: (directory / name).read_text()
        for name in (
            "client.py",
            "proxy.js",
            "private.py",
            "runtime.py",
            "context_service.py",
            "forwarded_fs.py",
            "release.py",
        )
    }
    fuse_distribution = distribution("fusepy")
    fuse_source = fuse_distribution.locate_file("fuse.py").read_text()
    if "Permission to use, copy, modify, and distribute" not in fuse_source:
        raise RuntimeError("fusepy source does not carry its ISC license")
    result["fuse.py"] = fuse_source
    result.update(
        {
            "cheese.py": (
                Path(__file__).resolve().parents[6] / "sandbox/cheese"
            ).read_text(),
            "executor_transport.py": Path(executor_transport.__file__).read_text(),
        }
    )
    return result


# The helpers a release really does bring up to date in a running session. The
# plugin module is loaded again by `/reload-plugins`; the other two are read
# only by processes that start after the release — a prompt hook, and the native
# MCP transport the release reconnects.
#
# Every other helper stays as the launch left it, so a change to one is a
# change to the launch (`launch_only`). `client.py` is replaced on disk here,
# but `prepare` wrote the shell prefix, the launch environment, the argv and
# the MCP config out of it once, the forwarded view's server and the other MCP
# bridges keep the modules they started with, and nothing writes those again.
# A helper added later is launch-only until it is shown to be one of these.
RESIDENT = frozenset({"proxy.js", "context_service.py", "cheese.py"})


def platform_tool_names(cheese_source: str) -> list[str]:
    """The platform tools the native MCP server serves: the rows of
    `PLATFORM_TOOLS` in this release's `cheese.py`, the one table every harness
    reads. A module of its own, so the CLI's `__main__` block does not run."""
    module = types.ModuleType("cheese_platform_tools")
    exec(compile(cheese_source, "cheese", "exec"), module.__dict__)  # noqa: S102
    return list(module.PLATFORM_TOOLS.names())


def hook_module(proxy_source: str, target: dict, platform_tools: list[str]) -> str:
    """`proxy.js` as the plugin loads it, at launch and at every release."""
    return proxy_source.replace("__EXECUTION_CONFIG__", json.dumps(target)).replace(
        "__PLATFORM_TOOLS__", json.dumps(platform_tools)
    )


def allow_native_tools(settings: dict, platform_tools: list[str]) -> None:
    """Allow the native server's tools by name, the table's rows included."""
    allowed = settings.setdefault("permissions", {}).setdefault("allow", [])
    for name in ("invoke", "project_tools", *platform_tools):
        tool = "mcp__native__" + name
        if tool not in allowed:
            allowed.append(tool)


def launch_only(sources):
    """The helper sources a running session keeps as they were at launch."""
    return {name: text for name, text in sources.items() if name not in RESIDENT}


def script(function, *args):
    return (
        Path(__file__).read_text()
        + "\nprint(json.dumps("
        + function
        + "(*json.loads("
        + repr(json.dumps(args))
        + "))))\n"
    )


def digest(sources):
    return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()


def replace(path, content):
    path = Path(path)
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(content)
    temporary.chmod(path.stat().st_mode if path.exists() else 0o600)
    temporary.replace(path)


def stage(home, sources, seat="", session_id="", background=False):
    """Install a release into the session that asked for it.

    ``seat`` is that session's own directory inside the room's home
    (``place.seat_dir``), where its execution target and everything the client
    prepared beside it live; empty is a caller with no seat to name, which
    resolves to the room-level directory files sat in before seats existed.
    The transcript config dir is the room's. A named seat owns its helpers and
    hook settings, so releasing it cannot replace another seat's tool code or
    redirect its hooks. The busy check still reads room transcripts.
    ``background``: the session has a command running in the background. The
    release leaves the process and its commands running, so that only matters
    where the release would unmount the forwarded view the command may be
    reading.
    """
    config = Path(os.path.expandvars(home)) / ".claude"
    platform_dir = Path(os.path.expandvars(home)) / ".cheese"
    helpers = (
        Path(os.path.expandvars(seat)) / "remote-execution"
        if seat
        else platform_dir / "remote-execution"
    )
    directory = (
        Path(os.path.expandvars(seat)) / "remote-session"
        if seat
        else platform_dir / "remote-session"
    )
    target = json.loads((directory / "execution.json").read_text())
    settings_path = directory / "settings.json"
    settings = json.loads(settings_path.read_text())
    version = digest(sources)
    ready = directory / "release-ready"
    if ready.exists() and ready.read_text() == version:
        return {"changed": False, "version": version}
    transcripts = list(
        (config / "projects").glob(
            f"*/{session_id}.jsonl" if session_id else "*/*.jsonl"
        )
    )
    if transcripts:
        busy = False
        latest = max(transcripts, key=lambda path: path.stat().st_mtime_ns)
        for line in latest.read_text().splitlines():
            event = json.loads(line)
            if event.get("isMeta") or event.get("isSidechain"):
                continue
            message = event.get("message", {})
            if event.get("type") == "user":
                busy = True
            elif event.get("type") == "assistant" and message.get("stop_reason"):
                busy = message["stop_reason"] == "tool_use"
            elif (
                event.get("type") == "system"
                and event.get("subtype") == "local_command"
            ):
                busy = False
        if busy:
            # Replacing helpers under a running turn swaps the code its tool
            # calls are in. Said rather than raised: it is a reason to wait,
            # which the caller decides, not a failed release.
            return {"changed": False, "busy": True, "version": version}
    forwarded = directory / "forwarded-project"
    unmounts = Path(target.get("central_workspace", "")) != forwarded
    if background and unmounts and mount_state(forwarded) != MOUNT_NONE:
        return {"changed": False, "busy": True, "version": version}
    if unmounts:
        # `release_mount`, not a bare fusermount: it also takes down a mount whose
        # server has died, which is the one that would otherwise stay here forever.
        # Still loud on failure — replacing the helpers under a view that is still
        # mounted is what this unmount exists to prevent.
        if not release_mount(forwarded):
            raise RuntimeError(f"Could not release the forwarded view at {forwarded}")
    backup = helpers / "release-backups" / version
    paths = {name: helpers / name for name in sources}
    paths.update(settings=settings_path, proxy=directory / "plugin/hooks/proxy.js")
    for name, path in paths.items():
        destination = backup / name
        if path.exists() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            destination.chmod(0o600)
    # Install companions before client.py, which imports them on reconnect.
    for name in sorted(sources, key=lambda name: name == "client.py"):
        if Path(name).name != name:
            raise ValueError("A helper source must be a filename")
        replace(helpers / name, sources[name])
    platform_tools = platform_tool_names(sources["cheese.py"])
    replace(
        directory / "plugin/hooks/proxy.js",
        hook_module(sources["proxy.js"], target, platform_tools),
    )
    allow_native_tools(settings, platform_tools)
    if target.get("kind") != "private" and target.get("helper"):
        managed_context_hook = {
            "type": "command",
            "command": target["helper"][0],
            "args": [
                str(helpers / "context_service.py"),
                str(directory / "execution.json"),
            ],
        }
        for event in ("SessionStart", "UserPromptSubmit"):
            groups = []
            for group in settings.get("hooks", {}).get(event, []):
                hooks = [
                    hook
                    for hook in group.get("hooks", [])
                    if not all(
                        hook.get(key) == value
                        for key, value in managed_context_hook.items()
                    )
                ]
                if hooks:
                    groups.append({**group, "hooks": hooks})
            settings.get("hooks", {})[event] = groups
    replace(settings_path, json.dumps(settings))
    return {"changed": True, "version": version}


def acknowledge(home, version, seat=""):
    directory = (
        Path(os.path.expandvars(seat)) / "remote-session"
        if seat
        else Path(os.path.expandvars(home)) / ".cheese/remote-session"
    )
    replace(
        directory / "release-ready",
        version,
    )


# A skill's name is its entry in the config dir, where "/" cannot appear. A
# name Claude Code qualifies with a directory (`apps/web:deploy`) is spelled
# with this in its place; `proxy.js` takes the plain spelling in a Skill call.
NAME_SLASH = "∕"
# The state of the skills a session is offered only once a file tool reaches
# their files: the subdirectories whose `.claude/skills` it has reached, and
# the path-scoped skills a file it reached matched (`touch_skills`).
LAZY_STATE = "lazy-skills.json"
# For every entry in the config dir that is not the project's root skill of
# the same name: where the executor holds its files, and what Claude Code adds
# to its description (`proxy.js` reads both).
SKILL_PLACES = "skill-places.json"
# Left by `touch_skills` for the runner's next catch-up, which reloads skills.
SKILLS_CHANGED = "skills-changed"


class _Locked:
    """One writer at a time to a session's config dir: the runner's catch-up
    and the session's own file tools both relink it."""

    def __init__(self, directory):
        self.path = Path(directory) / "skills.lock"

    def __enter__(self):
        import fcntl

        self.stream = self.path.open("a")
        fcntl.flock(self.stream, fcntl.LOCK_EX)

    def __exit__(self, *_):
        self.stream.close()


def _lazy_state(directory):
    path = Path(directory) / LAZY_STATE
    state = json.loads(path.read_text()) if path.exists() else {}
    return {
        "directories": state.get("directories", []),
        "skills": state.get("skills", []),
        "generated": state.get("generated", []),
    }


def _split_outside_braces(value):
    pieces, current, depth = [], "", 0
    for character in value:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == "," and depth == 0:
            pieces.append(current)
            current = ""
            continue
        current += character
    return [*pieces, current]


def _expand_braces(value):
    import re

    done, pending = [], [value]
    while pending:
        item = pending.pop()
        match = re.match(r"^([^{]*)\{([^}]+)\}(.*)$", item)
        if not match:
            done.append(item)
            continue
        head, options, tail = match.groups()
        pending.extend(head + option.strip() + tail for option in options.split(","))
    return done


def path_patterns(values):
    """The patterns a skill's `paths` scopes it to, read as Claude Code reads
    them: split at top-level commas, braces expanded, a trailing `/**` dropped.
    None when they scope it to nothing narrower than everything."""
    patterns = []
    for value in values or []:
        for piece in _split_outside_braces(value):
            piece = piece.strip()
            if len(piece) >= 2 and piece[0] == piece[-1] and piece[0] in "'\"":
                piece = piece[1:-1]
            for pattern in _expand_braces(piece) if piece else []:
                if pattern.endswith("/**"):
                    pattern = pattern[:-3]
                if pattern:
                    patterns.append(pattern)
    if not patterns or all(pattern == "**" for pattern in patterns):
        return None
    return patterns


def _pattern(pattern):
    import re

    negate = pattern.startswith("!")
    if negate:
        pattern = pattern[1:]
    directory_only = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    anchored = "/" in pattern
    pattern = pattern.lstrip("/")
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        elif pattern[index] == "[" and "]" in pattern[index + 2 :]:
            end = pattern.index("]", index + 2)
            members = pattern[index + 1 : end]
            if members.startswith("!"):
                members = "^" + members[1:]
            out, index = out + "[" + members + "]", end + 1
        elif pattern[index] == "\\" and index + 1 < len(pattern):
            out, index = out + re.escape(pattern[index + 1]), index + 2
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return negate, directory_only, re.compile(("" if anchored else "(?:.*/)?") + out)


def path_matches(patterns, relative):
    """Whether a file at `relative` (to the project) is one `patterns` name,
    by gitignore rules, as Claude Code decides whether to offer a path-scoped
    skill: the file or any directory above it matching is enough."""
    if not relative or relative.startswith("../") or relative.startswith("/"):
        return False
    compiled = [_pattern(pattern) for pattern in patterns]
    parts = relative.split("/")
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth])
        is_directory = depth < len(parts)
        matched = False
        for negate, directory_only, expression in compiled:
            if directory_only and not is_directory:
                continue
            if expression.fullmatch(candidate):
                matched = not negate
        if matched:
            return True
    return False


def _skill_scope(entries, skill):
    """The `paths` patterns of the skill at `skill` (a directory in the
    project, or a link to one), or None when it applies everywhere."""
    entry = entries.get(skill, {})
    if entry.get("kind") == "symlink":
        skill = os.path.normpath(
            os.path.join(os.path.dirname(skill), entry.get("target", ""))
        ).replace(os.sep, "/")
    return path_patterns(entries.get(skill + "/SKILL.md", {}).get("paths"))


def _skills_in(entries, parent):
    prefix = parent + "/"
    return sorted(
        name[len(prefix) :]
        for name, entry in entries.items()
        if name.startswith(prefix)
        and "/" not in name[len(prefix) :]
        and entry["kind"] in ("directory", "symlink")
    )


def _without_paths(text):
    """SKILL.md's text with its frontmatter's `paths` taken out."""
    import re

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    out, dropping = [lines[0]], False
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            out.extend(lines[index:])
            break
        if dropping and (line[:1].isspace() or line.startswith("-")):
            continue
        dropping = bool(re.match(r"paths\s*:", line))
        if not dropping:
            out.append(line)
    return "\n".join(out)


def link_forwarded_user_context(directory, config, forwarded, tree, helpers, view=None):
    """Expose forwarded project context through Claude's managed user source.

    `forwarded` is where the session sees the project, and what the links
    name; `view` is where this process can read it, when that is elsewhere
    (the runner reads it outside the session's namespace)."""
    with _Locked(directory):
        _link(
            Path(directory),
            Path(config),
            Path(forwarded),
            tree,
            helpers,
            Path(view) if view else Path(forwarded),
        )


def _link(directory, config, forwarded, tree, helpers, view):
    entries = tree["entries"]
    backup = (
        Path(helpers)
        / "release-backups/forwarded-context/user-source"
        / hashlib.sha256(str(config).encode()).hexdigest()[:16]
    )
    for category in ("skills", "commands", "agents", "rules"):
        parent = config / category
        if parent.is_symlink():
            backup.mkdir(parents=True, exist_ok=True)
            link_backup = backup / f"{category}.symlink"
            if not link_backup.exists():
                link_backup.write_text(os.readlink(parent))
            parent.unlink()
            parent.mkdir()
    links = {}
    for name in entries:
        relative = Path(name)
        if relative.parts[:2] in {
            (".claude", "skills"),
            (".claude", "commands"),
            (".claude", "agents"),
            (".claude", "rules"),
        }:
            if len(relative.parts) == 3:
                links[config / relative.parts[1] / relative.parts[2]] = (
                    forwarded / relative
                )
            continue
    instructions = config / "CLAUDE.md"
    imports = [
        forwarded / name for name in ("CLAUDE.md", "CLAUDE.local.md") if name in entries
    ]
    # The project is at the executor's own path, which can hold a space; an
    # import ends at the first unescaped one, and Claude Code reads `\ ` as a
    # space inside it.
    wrapper = "".join("@" + str(path).replace(" ", "\\ ") + "\n" for path in imports)
    if instructions.is_symlink():
        instructions.unlink()
    elif instructions.exists() and instructions.read_text() != wrapper:
        instructions_backup = backup / "CLAUDE.md"
        if not instructions_backup.exists():
            instructions_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(instructions, instructions_backup)
    replace(instructions, wrapper)

    # Skills Claude Code offers only once a file tool reaches their files
    # (`touch_skills` records which it has reached):
    #
    #   a subdirectory's `.claude/skills`, from the first time a file under
    #   that subdirectory is read or written, under its own name, or under
    #   `dir:name` when a root skill or another subdirectory's has the name;
    #
    #   a skill whose `paths` scopes it to some files, from the first time one
    #   of them is. Linked as it is, the session holds it back itself and a
    #   native Read (`proxy.js`) offers it; one a Write or Edit reached is
    #   written here without its `paths`, so the session offers it outright.
    lazy = _lazy_state(directory)
    touched = set(lazy["skills"])
    ours = set(lazy["generated"])
    generated = {}
    places = {}
    for destination, source in list(links.items()):
        skill = str(source.relative_to(forwarded))
        if destination.parent.name == "skills" and skill in touched:
            if _skill_scope(entries, skill):
                generated[destination] = skill
                places[destination.name] = {"place": str(source)}
                del links[destination]
    regular = {
        path.name if path.parent.name == "skills" else path.stem
        for path in links
        if path.parent.name in ("skills", "commands")
    } | {path.name for path in generated}
    if (config / "skills").is_dir():
        regular |= {
            entry.name
            for entry in (config / "skills").iterdir()
            if entry.is_dir() and not entry.is_symlink() and entry.name not in ours
        }
    nested = [
        (parent, name)
        for parent in sorted(set(lazy["directories"]))
        for name in _skills_in(entries, parent + "/.claude/skills")
    ]
    counts = {}
    for _, name in nested:
        counts[name] = counts.get(name, 0) + 1
    for parent, name in nested:
        skill = f"{parent}/.claude/skills/{name}"
        if name in regular or counts[name] > 1:
            # As Claude Code names and describes it (2.1.282, `RGo`).
            entry = parent.replace("/", NAME_SLASH) + ":" + name
            note = (
                f' (scoped to {parent}/ — use this instead of the unscoped "{name}" '
                f"skill when the files being changed are under {parent}/)"
                if name in regular
                else f" (from {parent}/.claude/skills — applies when working on "
                f"files under {parent}/)"
            )
        else:
            entry = name
            note = (
                f" (from {parent}/.claude/skills — applies when working on "
                f"files under {parent}/)"
            )
        destination = config / "skills" / entry
        places[entry] = {"place": str(forwarded / skill), "note": note}
        if skill in touched and _skill_scope(entries, skill):
            generated[destination] = skill
        else:
            links[destination] = forwarded / skill

    state_path = directory / "forwarded-user-links.json"
    previous = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    wanted = {str(path.relative_to(config)) for path in (*links, *generated)}
    for name in sorted(
        previous - wanted, key=lambda value: value.count("/"), reverse=True
    ):
        path = config / name
        if path.is_symlink():
            path.unlink()
        elif path.is_dir() and path.name in ours:
            shutil.rmtree(path)
    for destination, source in links.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            if destination.resolve() == source.resolve():
                continue
            destination.unlink()
        elif destination.is_dir() and destination.name in ours:
            shutil.rmtree(destination)
        elif destination.exists():
            # The platform ships something by this name (its own skill, or a
            # way of working the project saved). Plain Claude Code gives a
            # personal skill precedence over a project skill of the same name
            # and carries on, and so does the room: the repository's stays in
            # the project, unlinked, and the session starts.
            wanted.discard(str(destination.relative_to(config)))
            continue
        destination.symlink_to(
            source,
            target_is_directory=entries[str(source.relative_to(forwarded))]["kind"]
            == "directory",
        )
    written = []
    for destination, skill in generated.items():
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists() and destination.name not in ours:
            wanted.discard(str(destination.relative_to(config)))
            continue
        written.append(destination.name)
        destination.mkdir(parents=True, exist_ok=True)
        # Only SKILL.md is read here; a file beside it is read where the
        # executor holds it (`skill-places.json`).
        replace(
            destination / "SKILL.md",
            _without_paths((view / skill / "SKILL.md").read_text()),
        )
    lazy["generated"] = sorted(written)
    replace(directory / LAZY_STATE, json.dumps(lazy))
    # Whether any skill the session holds is scoped by `paths`: only then does
    # a Read need the session's own look at the file (`proxy.js`).
    scoped = any(
        _skill_scope(entries, str(source.relative_to(forwarded)))
        for destination, source in links.items()
        if destination.parent.name == "skills"
    )
    replace(directory / SKILL_PLACES, json.dumps({"places": places, "scoped": scoped}))
    replace(state_path, json.dumps(sorted(wanted)))


def touch_skills(directory, config, forwarded, helpers, path, view=None):
    """A file tool reached `path`: offer what Claude Code offers once one has.

    Records the subdirectories between the file and the project whose
    `.claude/skills` the session now offers, and the path-scoped skills the
    file matches, relinks, and leaves word for the runner's catch-up. True
    when anything is offered that was not."""
    directory = Path(directory)
    try:
        relative = Path(path).relative_to(Path(forwarded)).as_posix()
    except ValueError:
        return False
    tree_path = directory / "context-tree.json"
    if relative == "." or not tree_path.exists():
        return False
    tree = json.loads(tree_path.read_text())
    entries = tree["entries"]
    with _Locked(directory):
        lazy = _lazy_state(directory)
        directories, skills = set(lazy["directories"]), set(lazy["skills"])
        parts = relative.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent + "/.claude/skills" in entries:
                directories.add(parent)
        for parent in ("", *sorted(directories)):
            skills_dir = (parent + "/" if parent else "") + ".claude/skills"
            for name in _skills_in(entries, skills_dir):
                skill = f"{skills_dir}/{name}"
                patterns = _skill_scope(entries, skill)
                if patterns and path_matches(patterns, relative):
                    skills.add(skill)
        if directories == set(lazy["directories"]) and skills == set(lazy["skills"]):
            return False
        lazy.update(directories=sorted(directories), skills=sorted(skills))
        replace(directory / LAZY_STATE, json.dumps(lazy))
    link_forwarded_user_context(directory, config, forwarded, tree, helpers, view)
    (directory / SKILLS_CHANGED).touch()
    return True


def forget_touched_skills(directory, config):
    """A new session process starts with none of what the last one reached."""
    directory, config = Path(directory), Path(config)
    with _Locked(directory):
        lazy = _lazy_state(directory)
        for name in lazy["generated"]:
            path = config / "skills" / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        (directory / LAZY_STATE).unlink(missing_ok=True)
