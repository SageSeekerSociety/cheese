"""Install released helpers without replacing the native conversation process."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
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


def stage(home, sources):
    config = Path(os.path.expandvars(home)) / ".claude"
    platform_dir = Path(os.path.expandvars(home)) / ".cheese"
    helpers = platform_dir / "remote-execution"
    directory = platform_dir / "remote-session"
    target = json.loads((directory / "execution.json").read_text())
    settings_path = config / "settings.json"
    settings = json.loads(settings_path.read_text())
    version = digest(sources)
    ready = helpers / "release-ready"
    if ready.exists() and ready.read_text() == version:
        return {"changed": False, "version": version}
    transcripts = list((config / "projects").glob("*/*.jsonl"))
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
    if Path(target.get("central_workspace", "")) != forwarded:
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
    replace(
        directory / "plugin/hooks/proxy.js",
        sources["proxy.js"].replace("__EXECUTION_CONFIG__", json.dumps(target)),
    )
    allowed = settings.setdefault("permissions", {}).setdefault("allow", [])
    for name in ("invoke", "chat_send", "platform_request", "cheese_*"):
        tool = "mcp__native__" + name
        if tool not in allowed:
            allowed.append(tool)
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


def acknowledge(home, version):
    replace(
        Path(os.path.expandvars(home)) / ".cheese/remote-execution/release-ready",
        version,
    )


def link_forwarded_user_context(directory, config, forwarded, tree, helpers):
    """Expose forwarded project context through Claude's managed user source."""
    directory = Path(directory)
    config = Path(config)
    forwarded = Path(forwarded)
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
    wrapper = "".join(f"@{path}\n" for path in imports)
    if instructions.is_symlink():
        instructions.unlink()
    elif instructions.exists() and instructions.read_text() != wrapper:
        instructions_backup = backup / "CLAUDE.md"
        if not instructions_backup.exists():
            instructions_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(instructions, instructions_backup)
    replace(instructions, wrapper)

    state_path = directory / "forwarded-user-links.json"
    previous = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    wanted = {str(path.relative_to(config)) for path in links}
    for name in sorted(
        previous - wanted, key=lambda value: value.count("/"), reverse=True
    ):
        path = config / name
        if path.is_symlink():
            path.unlink()
    for destination, source in links.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            if destination.resolve() == source.resolve():
                continue
            destination.unlink()
        elif destination.exists():
            raise RuntimeError(
                f"Forwarded user context conflicts with central file: {destination}"
            )
        destination.symlink_to(
            source,
            target_is_directory=entries[str(source.relative_to(forwarded))]["kind"]
            == "directory",
        )
    replace(state_path, json.dumps(sorted(wanted)))
