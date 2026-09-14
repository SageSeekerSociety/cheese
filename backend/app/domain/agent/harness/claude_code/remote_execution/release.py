"""Install released helpers without replacing the native conversation process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
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
            "release.py",
        )
    }
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
    replace(settings_path, json.dumps(settings))
    offsets = {str(path): path.stat().st_size for path in transcripts}
    return {"changed": True, "version": version, "offsets": offsets}


def reloaded(offsets):
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
                    and event.get("content", "").startswith(
                        "<local-command-stdout>Reloaded:"
                    )
                ):
                    return True
    return False


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
