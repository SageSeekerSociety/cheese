"""A project's tool hooks hold around pi's own tools, as on every other harness.

A repository guards Bash or its files with PreToolUse and PostToolUse hooks in
`.claude/settings.json`, written for Claude Code's tool names. pi fires no
hooks, so before each of its tool calls the extension asks the runner, and
again after it (`platform.ts`). What is driven here is the real runner, over
its socket, in a checkout that carries hooks: the calls a pi session makes are
asked about the way the extension asks, and run only when the answer allows
it, the way pi runs them.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi.runner import Runner
from tests.unit.test_pi_runner import call, shim

LOG = 'cat >> "$CLAUDE_PROJECT_DIR/hooks.log"; echo >> "$CLAUDE_PROJECT_DIR/hooks.log"'

# A project's own guard, as written for Claude Code: it reads the Bash command.
GUARD = """import json, sys
event = json.load(sys.stdin)
if "rm -rf" in event["tool_input"].get("command", ""):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "PROJECT_POLICY: no recursive deletes"}}))
"""

# And one that reads a file tool's `file_path`, refusing with exit 2.
NO_SECRETS = """import json, sys
event = json.load(sys.stdin)
if "secrets/" in event["tool_input"].get("file_path", ""):
    print("PROJECT_POLICY: secrets/ is read-only", file=sys.stderr)
    sys.exit(2)
"""

# And one that sends every read of the old notes to the new ones.
REDIRECT = """import json, sys
event = json.load(sys.stdin)
if event["tool_input"]["file_path"] == "old-notes.md":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {**event["tool_input"], "file_path": "notes.md"}}}))
"""


def _checkout(tmp_path: Path) -> Path:
    work = tmp_path / "room"
    (work / ".claude").mkdir(parents=True)
    (work / "doomed").mkdir()
    for name, source in (
        ("guard.py", GUARD),
        ("no_secrets.py", NO_SECRETS),
        ("redirect.py", REDIRECT),
    ):
        (work / ".claude" / name).write_text(source)

    def script(name: str) -> dict:
        path = f'"$CLAUDE_PROJECT_DIR/.claude/{name}"'
        return {"type": "command", "command": f"{sys.executable} {path}"}

    (work / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": LOG}],
                        },
                        {"matcher": "Bash", "hooks": [script("guard.py")]},
                        {"matcher": "Write|Edit", "hooks": [script("no_secrets.py")]},
                        {"matcher": "Read", "hooks": [script("redirect.py")]},
                    ],
                    "PostToolUse": [
                        {"matcher": "", "hooks": [{"type": "command", "command": LOG}]}
                    ],
                }
            }
        )
    )
    return work


def _pi_runs(work: Path, tool: str, args: dict) -> str:
    """What pi's own tool does with the arguments it was left with."""
    if tool in ("bash", "bash_start"):
        return subprocess.run(
            ["bash", "-c", args["command"]],
            cwd=work,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    if tool == "read":
        return (work / args["path"]).read_text()
    if tool == "write":
        (work / args["path"]).parent.mkdir(parents=True, exist_ok=True)
        (work / args["path"]).write_text(args["content"])
        return "written"
    raise AssertionError(tool)


async def _session(tmp_path: Path, work: Path, calls: list[tuple[str, dict]]):
    """A pi session in `work` making `calls`, each asked about before and after
    the way the extension asks; returns what each call answered."""
    runner = Runner(tmp_path / "state")
    await runner.start(
        Opening("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(work),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
    )
    answers = []
    try:
        for index, (tool, args) in enumerate(calls):
            asked = {"tool": tool, "id": f"call-{index}", "cwd": str(work)}
            before = await call(
                runner.state, "hooks", {**asked, "event": "PreToolUse", "input": args}
            )
            if "denied" in before:
                answers.append(("blocked", before["denied"]))
                continue
            output = _pi_runs(work, tool, before["input"])
            content = [{"type": "text", "text": output}]
            after = await call(
                runner.state,
                "hooks",
                {
                    **asked,
                    "event": "PostToolUse",
                    "input": before["input"],
                    "result": {"content": content},
                },
            )
            assert "denied" not in after
            answers.append(("ran", output))
    finally:
        await runner.close()
    return answers


def test_a_pi_session_runs_the_projects_hooks_around_its_own_tools(tmp_path):
    work = _checkout(tmp_path)
    (work / "notes.md").write_text("the new notes\n")

    answers = asyncio.run(
        _session(
            tmp_path,
            work,
            [
                ("bash", {"command": "echo hello"}),
                ("bash", {"command": "rm -rf doomed"}),
                ("bash_start", {"command": "rm -rf doomed", "label": "cleanup"}),
                ("write", {"path": "secrets/key", "content": "leaked"}),
                ("write", {"path": "draft.md", "content": "draft"}),
                ("read", {"path": "old-notes.md"}),
            ],
        )
    )

    assert answers[0] == ("ran", "hello\n")
    # The guard's reason is the answer, and the command never ran.
    for blocked in answers[1:3]:
        assert blocked == ("blocked", "PROJECT_POLICY: no recursive deletes")
    assert (work / "doomed").is_dir()
    kind, said = answers[3]
    assert kind == "blocked" and "PROJECT_POLICY: secrets/ is read-only" in said
    assert not (work / "secrets").exists()
    assert answers[4] == ("ran", "written")
    # The hook's `updatedInput`, in Claude Code's keys, reached pi in its own.
    assert answers[5] == ("ran", "the new notes\n")

    events = [
        json.loads(line)
        for line in (work / "hooks.log").read_text().splitlines()
        if line.strip()
    ]
    assert [
        (e["hook_event_name"], e["tool_name"], e["tool_use_id"]) for e in events
    ] == [
        ("PreToolUse", "Bash", "call-0"),
        ("PostToolUse", "Bash", "call-0"),
        ("PreToolUse", "Bash", "call-1"),
        ("PreToolUse", "Bash", "call-2"),
        ("PreToolUse", "Write", "call-3"),
        ("PreToolUse", "Write", "call-4"),
        ("PostToolUse", "Write", "call-4"),
        ("PreToolUse", "Read", "call-5"),
        ("PostToolUse", "Read", "call-5"),
    ]
    # The hooks read each call as Claude Code would have made it.
    assert events[0]["tool_input"] == {"command": "echo hello"}
    assert events[1]["tool_response"] == {
        "content": [{"type": "text", "text": "hello\n"}]
    }
    assert events[3]["tool_input"] == {
        "command": "rm -rf doomed",
        "description": "cleanup",
        "run_in_background": True,
    }
    assert events[5]["tool_input"] == {"file_path": "draft.md", "content": "draft"}
    assert events[8]["tool_input"] == {"file_path": "notes.md"}
    assert {e["cwd"] for e in events} == {str(work)}


@pytest.mark.parametrize("tool", ["ls", "bash_read"])
def test_a_tool_claude_code_has_no_equivalent_for_keeps_its_own_name(tmp_path, tool):
    work = _checkout(tmp_path)

    async def ask():
        runner = Runner(tmp_path / "state")
        await runner.start(
            Opening("system prompt", None, agent_handle="teammate"),
            binary=shim(tmp_path),
            cwd=str(work),
            env={"PATH": "/usr/bin:/bin"},
            args=[],
        )
        try:
            return await call(
                runner.state,
                "hooks",
                {"event": "PreToolUse", "tool": tool, "id": "c", "input": {"id": "x"}},
            )
        finally:
            await runner.close()

    assert asyncio.run(ask()) == {"input": {"id": "x"}}
    (event,) = [
        json.loads(line)
        for line in (work / "hooks.log").read_text().splitlines()
        if line.strip()
    ]
    assert event["tool_name"] == tool
