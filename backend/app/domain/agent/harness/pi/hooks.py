"""The project's tool hooks, run by pi's runner around the calls it makes.

A repository writes `PreToolUse` and `PostToolUse` hooks in `.claude/settings.json`
for plain Claude Code, which fires them around every tool call, MCP tools
included. On the other harnesses the executor runs them (`Executor.hooks` in
`claude_code/remote_execution/runtime.py`) around the calls it makes, and runs
them for a remote server's call through its `tool_hooks` control. pi has no
executor: its runner is on the room's machine and makes the MCP calls itself,
so it runs the hooks itself, by the executor's rules:

- the checkout's `.claude/settings.json`, then `.claude/settings.local.json`;
- a group whose `matcher` fully matches the tool name, `""` and `*` matching all;
- command hooks only, run with `bash -c` in the call's working directory, with
  the project root in `CLAUDE_PROJECT_DIR` and the event as JSON on stdin;
- exit 2 blocks with the hook's stderr; any other failing exit lets the call go
  ahead; on success a `deny` decision or `decision: block` blocks, and a
  `PreToolUse` hook's `updatedInput` replaces the call's arguments.
"""

import asyncio
import json
import re
from pathlib import Path


class HookDenied(Exception):
    """A hook blocked the call. The message is the hook's reason."""


async def run(
    event: str,
    tool: str,
    args: dict,
    *,
    call_id: str,
    root: str,
    cwd: str | None,
    env: dict[str, str],
    session_id: str,
    result: object = None,
) -> dict:
    """Run the project's `event` hooks for one call; return the arguments the
    call proceeds with. Raises `HookDenied` when a hook blocks it."""
    project = Path(root)
    where = cwd if cwd and Path(cwd).is_dir() else root
    settings = [
        json.loads(path.read_text())
        for path in (
            project / ".claude/settings.json",
            project / ".claude/settings.local.json",
        )
        if path.exists()
    ]
    for source in settings:
        for group in source.get("hooks", {}).get(event, []):
            matcher = group.get("matcher", "*")
            if matcher not in ("", "*") and not re.fullmatch(matcher, tool):
                continue
            for hook in group.get("hooks", []):
                if hook["type"] != "command":
                    raise ValueError("pi rooms run command hooks only")
                payload = {
                    "hook_event_name": event,
                    "tool_name": tool,
                    "tool_input": args,
                    "tool_use_id": call_id,
                    "cwd": str(where),
                    "session_id": session_id,
                }
                if event == "PostToolUse":
                    payload["tool_response"] = result
                process = await asyncio.create_subprocess_exec(
                    "bash",
                    "-c",
                    hook["command"],
                    cwd=where,
                    env={**env, "CLAUDE_PROJECT_DIR": str(project)},
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    out, err = await asyncio.wait_for(
                        process.communicate(json.dumps(payload).encode()),
                        hook.get("timeout", 60),
                    )
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise
                if process.returncode == 2:
                    raise HookDenied(f"{event} hook: {err.decode(errors='replace')}")
                if process.returncode:
                    continue
                output = json.loads(out) if out.strip() else {}
                specific = output.get("hookSpecificOutput", {})
                if (
                    specific.get("permissionDecision") == "deny"
                    or output.get("decision") == "block"
                ):
                    raise HookDenied(
                        specific.get("permissionDecisionReason")
                        or output.get("reason", f"{event} hook denied the call")
                    )
                if event == "PreToolUse" and "updatedInput" in specific:
                    args = specific["updatedInput"]
    return args
