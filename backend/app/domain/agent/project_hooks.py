"""The project's PreToolUse and PostToolUse hooks, run by whoever makes the call.

A repository writes these in `.claude/settings.json` for plain Claude Code,
which fires them around every tool call. Where Claude Code is not the one
making a call, whatever makes it runs them by these rules: the Codex executor
(`Executor.hooks`), and pi's runner (`harness/pi/hooks.py`). One copy, so what
a hook can block cannot differ between two harnesses:

- the checkout's `.claude/settings.json`, then `.claude/settings.local.json`,
  then whatever the caller adds;
- a group whose `matcher` fully matches the tool name, `""` and `*` matching all;
- command hooks only, run with `bash -c` in the call's working directory, with
  the project root in `CLAUDE_PROJECT_DIR` and the event as JSON on stdin;
- exit 2 blocks with the hook's stderr; any other failing exit lets the call go
  ahead; on success a `deny` decision or `decision: block` blocks, and a
  `PreToolUse` hook's `updatedInput` replaces the call's arguments.

Standard library only, and Python 3.9: shipped as a loose file beside the
executor's runtime (`remote-execution/project_hooks.py`), and in pi's runner
archive.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


class Denied(PermissionError):
    """A hook blocked the call. The message is the hook's reason."""


def run(
    event,
    tool,
    args,
    *,
    call_id,
    root,
    cwd,
    env,
    session_id,
    result=None,
    extra=(),
    program=lambda argv: argv,
):
    """Run the project's `event` hooks for one call to `tool`, as the hooks name
    it; return the arguments the call proceeds with. Raises `Denied` when a hook
    blocks it. `program` turns the hook's argv into one this OS can start."""
    root = Path(root)
    paths = [root / ".claude/settings.json", root / ".claude/settings.local.json"]
    settings = [json.loads(path.read_text()) for path in paths if path.exists()]
    settings.extend(extra)
    for source in settings:
        for group in source.get("hooks", {}).get(event, []):
            matcher = group.get("matcher", "*")
            if matcher not in ("", "*") and not re.fullmatch(matcher, tool):
                continue
            for hook in group.get("hooks", []):
                if hook["type"] != "command":
                    raise ValueError(
                        "Remote execution currently requires command hooks"
                    )
                payload = {
                    "hook_event_name": event,
                    "tool_name": tool,
                    "tool_input": args,
                    "tool_use_id": call_id,
                    "cwd": str(cwd),
                    "session_id": session_id,
                }
                if event == "PostToolUse":
                    payload["tool_response"] = result
                response = subprocess.run(
                    program(["bash", "-c", hook["command"]]),
                    cwd=cwd,
                    env=dict(env, CLAUDE_PROJECT_DIR=str(root)),
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    timeout=hook.get("timeout", 60),
                )
                if response.returncode == 2:
                    raise Denied(f"Remote {event} hook failed: {response.stderr}")
                if response.returncode:
                    continue
                output = json.loads(response.stdout) if response.stdout.strip() else {}
                specific = output.get("hookSpecificOutput", {})
                if (
                    specific.get("permissionDecision") == "deny"
                    or output.get("decision") == "block"
                ):
                    raise Denied(
                        specific.get("permissionDecisionReason")
                        or output.get("reason", "Remote hook denied operation")
                    )
                if event == "PreToolUse" and "updatedInput" in specific:
                    args = specific["updatedInput"]
    return args
