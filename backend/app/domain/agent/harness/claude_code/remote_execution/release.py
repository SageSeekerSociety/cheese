"""Install released helpers without replacing the native conversation process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import distribution
from pathlib import Path


def sources():
    from app.domain.agent import executor_transport
    from app.domain.agent.harness.claude_code import event_spool
    from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT

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
            "event_spool.py": Path(event_spool.__file__).read_text(),
            "platform-hook-source": CHEESE_HOOK_SCRIPT,
        }
    )
    return result


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
    helpers = config / "remote-execution"
    directory = config / "remote-session"
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
            raise RuntimeError("Native conversation must finish before helper release")
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
    for event in ("PreToolUse", "PostToolUse"):
        for group in settings.get("hooks", {}).get(event, []):
            matcher = group.get("matcher", "")
            for previous in (
                "^(?!mcp__native__invoke$)",
                "^(?!mcp__native__(?:invoke|chat_send)$)",
                "^(?!mcp__native__(?:invoke|chat_send|platform_request)$)",
            ):
                matcher = matcher.replace(
                    previous,
                    "^(?!mcp__native__(?:invoke|chat_send|platform_request|cheese_.*)$)",
                )
            group["matcher"] = matcher
    if target.get("kind") != "private":
        for event in ("SessionStart", "UserPromptSubmit"):
            settings.get("hooks", {})[event] = [
                group
                for group in settings.get("hooks", {}).get(event, [])
                if not any(
                    "context_service.py" in str(hook) for hook in group.get("hooks", [])
                )
            ]
    replace(settings_path, json.dumps(settings))
    offsets = {str(path): path.stat().st_size for path in transcripts}
    return {"changed": True, "version": version, "offsets": offsets}


def local_command_completed(offsets, prefix):
    for name, offset in offsets.items():
        with Path(name).open() as stream:
            stream.seek(offset)
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    event.get("type") == "system"
                    and event.get("subtype") == "local_command"
                    and event.get("content", "").startswith(prefix)
                ):
                    return True
    return False


def reloaded(offsets):
    return local_command_completed(offsets, "<local-command-stdout>Reloaded:")


def skills_reloaded(offsets):
    return local_command_completed(offsets, "<local-command-stdout>Reloaded skills:")


def acknowledge(home, version):
    replace(
        Path(os.path.expandvars(home)) / ".claude/remote-execution/release-ready",
        version,
    )


def wait_reloaded(offsets):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if reloaded(offsets):
            return True
        time.sleep(0.05)
    raise TimeoutError("Native plugin reload did not produce a completion receipt")


def wait_skills_reloaded(offsets, home=None, generation=None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if skills_reloaded(offsets):
            if home is not None and generation is not None:
                replace(
                    Path(os.path.expandvars(home))
                    / ".claude/remote-session/context-generation",
                    generation,
                )
            return True
        time.sleep(0.05)
    raise TimeoutError("Native skill reload did not produce a completion receipt")


def transcript_offsets(home):
    config = Path(os.path.expandvars(home)) / ".claude"
    return {
        str(path): path.stat().st_size
        for path in (config / "projects").glob("*/*.jsonl")
    }


def apply_forwarded_context(home, target):
    config = Path(os.path.expandvars(home)) / ".claude"
    directory = config / "remote-session"
    helpers = config / "remote-execution"
    target_path = directory / "execution.json"
    current = json.loads(target_path.read_text())
    tree = target.pop("context_tree")
    remote_target = {
        **current,
        **target,
        "central_workspace": current["central_workspace"],
        "central_config": current["central_config"],
        "helper": current["helper"],
        "target_file": current["target_file"],
    }
    replace(target_path, json.dumps(remote_target))
    generation_path = directory / "context-generation"
    previous = generation_path.read_text() if generation_path.exists() else None
    if tree["generation"] == previous:
        return {"changed": False}
    replace(directory / "context-tree.json", json.dumps(tree))
    hidden = directory / "forwarded-project"
    hidden.mkdir(exist_ok=True)
    if not os.path.ismount(hidden):
        log = (directory / "forwarded-project.log").open("a")
        subprocess.Popen(
            [
                sys.executable,
                str(helpers / "forwarded_fs.py"),
                str(target_path),
                str(hidden),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        deadline = time.monotonic() + 10
        while not os.path.ismount(hidden) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not os.path.ismount(hidden):
            raise RuntimeError("Forwarded project mount did not become ready")

    workspace = Path(current["central_workspace"])
    if workspace != hidden:
        manifest = directory / "context-manifest.json"
        mirrored = set(json.loads(manifest.read_text())) if manifest.exists() else set()
        backup = helpers / "release-backups/forwarded-context" / (previous or "legacy")
        if manifest.exists():
            backup.mkdir(parents=True, exist_ok=True)
            manifest_backup = backup / "context-manifest.json"
            if not manifest_backup.exists():
                shutil.copy2(manifest, manifest_backup)
        for name in sorted(mirrored, key=lambda value: value.count("/"), reverse=True):
            path = workspace / name
            if path.is_file() and not path.is_symlink():
                file_backup = backup / "files" / name
                if not file_backup.exists():
                    file_backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, file_backup)
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            parent = path.parent
            while parent != workspace:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        manifest.unlink(missing_ok=True)

        entries = tree["entries"]
        links = {
            name
            for name, entry in entries.items()
            if entry["kind"] != "directory"
            and not name.startswith((".claude/rules/", ".claude/skills/"))
        }
        for name in (".claude/rules", ".claude/skills"):
            if name in entries:
                links.add(name)
        links_path = directory / "forwarded-links.json"
        old_links = (
            set(json.loads(links_path.read_text())) if links_path.exists() else set()
        )
        for name in sorted(
            old_links - links, key=lambda value: value.count("/"), reverse=True
        ):
            path = workspace / name
            if path.is_symlink():
                path.unlink()
        for name in sorted(links, key=lambda value: value.count("/")):
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                if path.resolve() == (hidden / name).resolve():
                    continue
                path.unlink()
            elif path.exists():
                raise RuntimeError(
                    f"Forwarded context conflicts with central file: {name}"
                )
            path.symlink_to(
                hidden / name, target_is_directory=entries[name]["kind"] == "directory"
            )
        replace(links_path, json.dumps(sorted(links)))

    link_forwarded_user_context(
        directory, Path(current["central_config"]), hidden, tree, helpers
    )
    return {"changed": True, "offsets": transcript_offsets(home)}


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
