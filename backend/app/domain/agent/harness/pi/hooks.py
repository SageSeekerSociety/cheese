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
#: differently, what Claude Code's input carries that pi's does not). The
#: Claude Code side is the pinned build's own tool schemas; a key with no
#: equivalent there keeps pi's name.
AS_CLAUDE_CODE: dict[str, tuple[str, dict[str, str], dict]] = {
    "bash": ("Bash", {}, {}),
    # The extension's own background shell (`platform.ts`): a command started
    # this way runs just as Bash would, so a guard on Bash has to see it.
    "bash_start": ("Bash", {"label": "description"}, {"run_in_background": True}),
    "read": ("Read", {"path": "file_path"}, {}),
    "write": ("Write", {"path": "file_path"}, {}),
    # The extension's subagent tool (`platform.ts`) takes Claude Code's `Agent`
    # arguments under the name that build used to give it.
    "Task": ("Agent", {}, {}),
}


def shown(tool: str, args: dict) -> list[tuple[str, dict]]:
    """The calls the project's hooks see for one pi call: one, except for an
    `edit` of several replacements, which Claude Code would make as that many
    `Edit` calls (it has no tool that takes several)."""
    if tool == "edit":
        rest = {k: v for k, v in args.items() if k not in ("path", "edits")}
        return [
            (
                "Edit",
                {
                    **rest,
                    "file_path": args.get("path"),
                    "old_string": edit.get("oldText"),
                    "new_string": edit.get("newText"),
                },
            )
            for edit in args.get("edits") or []
        ]
    if tool not in AS_CLAUDE_CODE:
        return [(tool, args)]
    name, renamed, added = AS_CLAUDE_CODE[tool]
    return [(name, {**{renamed.get(k, k): v for k, v in args.items()}, **added})]


def taken(tool: str, updated: list[dict]) -> dict:
    """The hooks' `updatedInput`, one per call they saw, as pi's tool takes it."""
    if tool == "edit":
        paths = {edit.get("file_path") for edit in updated}
        if len(paths) != 1:
            raise Denied(
                "The project's hooks moved these edits to different files, "
                "which one edit call cannot make"
            )
        own = ("file_path", "old_string", "new_string")
        return {
            **{k: v for edit in updated for k, v in edit.items() if k not in own},
            "path": paths.pop(),
            "edits": [
                {"oldText": edit.get("old_string"), "newText": edit.get("new_string")}
                for edit in updated
            ],
        }
    if tool not in AS_CLAUDE_CODE:
        return updated[0]
    _, renamed, added = AS_CLAUDE_CODE[tool]
    back = {theirs: ours for ours, theirs in renamed.items()}
    return {back.get(k, k): v for k, v in updated[0].items() if k not in added}


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
    blocks it, or any part of it."""
    calls = shown(tool, args)
    updated = []
    for index, (name, seen) in enumerate(calls):
        updated.append(
            await asyncio.to_thread(
                project_hooks.run,
                event,
                name,
                seen,
                # One id per call the hooks see, so a hook pairing its
                # PreToolUse with its PostToolUse pairs each edit's.
                call_id=call_id if len(calls) == 1 else f"{call_id}.{index}",
                root=root,
                cwd=cwd if cwd and Path(cwd).is_dir() else root,
                env=env,
                session_id=session_id,
                result=result,
            )
        )
    if all(after is seen for after, (_, seen) in zip(updated, calls, strict=True)):
        return args
    return taken(tool, updated)
