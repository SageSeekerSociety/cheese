"""The project's tool hooks around pi's own calls, under the names the hooks know.

A repository writes `PreToolUse` and `PostToolUse` hooks in `.claude/settings.json`
for plain Claude Code, which fires them around every tool call. pi fires none, so
the extension asks before and after each of its own calls (`platform.ts`), and
the room's machine runs them by the executor's own rules — the machine holds the
project, its settings and whatever the hooks run. The project's MCP calls have
theirs run around them by the same executor (`mcp.py`).

A hook's matcher and its `tool_input` are written against Claude Code's tools.
On Codex nothing is renamed, because the executor serves Codex its tools under
those names (`codex/tools.py`). pi's built-in tools have names and argument
keys of their own, so each one with a Claude Code equivalent is shown to the
hooks as that tool, and an `updatedInput` a hook returns is renamed back before
pi runs it. A tool with no equivalent keeps its own name.
"""

import asyncio


class Denied(PermissionError):
    """A hook blocked the call. The message is the hook's reason."""


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


#: pi's calls that run on the room's machine (`platform.ts`): the ones that
#: take it, hooks and all. A subagent or a job already started does not.
REACH_THE_MACHINE = frozenset(
    {"read", "write", "edit", "bash", "bash_start", "bash_write"}
)


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
    machine,
    cwd: str | None,
    result: object = None,
) -> dict:
    """Run the project's `event` hooks for one pi call to `tool` on the room's
    machine (`Machine.hooks`); return the arguments it proceeds with, in pi's
    shape. Raises `Denied` when a hook blocks it, or any part of it."""
    if not machine.taken and tool not in REACH_THE_MACHINE:
        # The project's hooks are the machine's; a session that has not
        # needed it yet has none to run, as a Claude Code room before its
        # machine fires none.
        return args
    # Taken first: the paths the hooks read are the machine's own, and the
    # first call that reaches it may be told to read the repository first.
    await asyncio.to_thread(machine.take)
    if not await asyncio.to_thread(machine.has_hooks, event):
        return args
    calls = [(name, machine.spelled(seen)) for name, seen in shown(tool, args)]
    updated = []
    for index, (name, seen) in enumerate(calls):
        answer = await asyncio.to_thread(
            machine.hooks,
            event,
            name,
            seen,
            # One id per call the hooks see, so a hook pairing its PreToolUse
            # with its PostToolUse pairs each edit's.
            call_id=call_id if len(calls) == 1 else f"{call_id}.{index}",
            cwd=cwd,
            result=result,
        )
        if "denied" in answer:
            raise Denied(answer["denied"])
        updated.append(answer.get("args", seen))
    if all(after == seen for after, (_, seen) in zip(updated, calls, strict=True)):
        return args
    return taken(tool, updated)
