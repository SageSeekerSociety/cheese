"""The project's tool hooks around pi's calls, under the names the hooks know.

A repository writes `PreToolUse` and `PostToolUse` hooks in `.claude/settings.json`
for plain Claude Code, which fires them around every tool call. pi fires none
and has no executor to fire them, so its runner runs them, by the rules the
executor follows (`app/domain/agent/project_hooks.py`): around the MCP calls it
makes itself (`mcp.py`), and around pi's own tools when the extension asks
before and after each call (`platform.ts`).

A hook's matcher and its `tool_input` are written against Claude Code's tools.
On Codex nothing is renamed, because the executor serves Codex its tools under
those names (`codex/tools.py`). pi's built-in tools have names and argument
keys of their own, so each one with a Claude Code equivalent is shown to the
hooks as that tool, and an `updatedInput` a hook returns is renamed back before
pi runs it. A tool with no equivalent keeps its own name.
"""

import asyncio
from pathlib import Path

from app.domain.agent import project_hooks

Denied = project_hooks.Denied

#: pi's tool -> (Claude Code's tool, pi's argument keys that Claude Code names
#: differently, what Claude Code's input carries that pi's does not).
AS_CLAUDE_CODE: dict[str, tuple[str, dict[str, str], dict]] = {
    "bash": ("Bash", {}, {}),
    # The extension's own background shell (`platform.ts`): a command started
    # this way runs just as Bash would, so a guard on Bash has to see it.
    "bash_start": ("Bash", {"label": "description"}, {"run_in_background": True}),
    "read": ("Read", {"path": "file_path"}, {}),
    "write": ("Write", {"path": "file_path"}, {}),
    "edit": ("Edit", {"path": "file_path"}, {}),
    "grep": ("Grep", {}, {}),
    "find": ("Glob", {}, {}),
}


def shown(tool: str, args: dict) -> tuple[str, dict]:
    """The tool name and input the project's hooks see for a pi call."""
    if tool not in AS_CLAUDE_CODE:
        return tool, args
    name, renamed, added = AS_CLAUDE_CODE[tool]
    return name, {**{renamed.get(k, k): v for k, v in args.items()}, **added}


def taken(tool: str, updated: dict) -> dict:
    """A hook's `updatedInput` as pi's tool takes it."""
    if tool not in AS_CLAUDE_CODE:
        return updated
    _, renamed, added = AS_CLAUDE_CODE[tool]
    back = {theirs: ours for ours, theirs in renamed.items()}
    return {back.get(k, k): v for k, v in updated.items() if k not in added}


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
    """Run the project's `event` hooks for one pi call to `tool`; return the
    arguments it proceeds with, in pi's shape. Raises `Denied` when a hook
    blocks it."""
    name, seen = shown(tool, args)
    updated = await asyncio.to_thread(
        project_hooks.run,
        event,
        name,
        seen,
        call_id=call_id,
        root=root,
        cwd=cwd if cwd and Path(cwd).is_dir() else root,
        env=env,
        session_id=session_id,
        result=result,
    )
    return args if updated is seen else taken(tool, updated)
