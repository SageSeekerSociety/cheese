"""A project's tool hooks hold around pi's own tools, as on every other harness.

A repository guards Bash or its files with PreToolUse and PostToolUse hooks in
`.claude/settings.json`, written for Claude Code's tool names. pi fires no
hooks, so before each of its tool calls the extension asks the runner, and
again after it (`platform.ts`), and the room's machine runs them. What is
driven here is the real runner, over
its socket, in a checkout that carries hooks: the calls a pi session makes are
asked about the way the extension asks, and run only when the answer allows
it, the way pi runs them.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner
from tests.support.room_machine import room_machine
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

# One that corrects what an Edit writes, through `updatedInput`.
FIX_TYPOS = """import json, sys
event = json.load(sys.stdin)
fixed = event["tool_input"]["new_string"].replace("teh", "the")
if fixed != event["tool_input"]["new_string"]:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {**event["tool_input"], "new_string": fixed}}}))
"""


def _checkout(tmp_path: Path) -> Path:
    work = tmp_path / "room"
    (work / ".claude").mkdir(parents=True)
    (work / "doomed").mkdir()
    for name, source in (
        ("guard.py", GUARD),
        ("no_secrets.py", NO_SECRETS),
        ("redirect.py", REDIRECT),
        ("fix_typos.py", FIX_TYPOS),
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
                        {"matcher": "Edit", "hooks": [script("fix_typos.py")]},
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
    if tool == "edit":
        target = work / args["path"]
        text = target.read_text()
        for edit in args["edits"]:
            text = text.replace(edit["oldText"], edit["newText"], 1)
        target.write_text(text)
        return "edited"
    raise AssertionError(tool)


async def _session(tmp_path: Path, work: Path, calls: list[tuple[str, dict]]):
    """A pi session in `work` making `calls`, each asked about before and after
    the way the extension asks; returns what each call answered."""
    runner = Runner(tmp_path / "state")
    machine = room_machine(tmp_path / "machine", checkout=work)
    target = machine.__enter__()
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=target,
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
        machine.__exit__(None, None, None)
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


def test_a_tool_claude_code_has_no_equivalent_for_keeps_its_own_name(tmp_path):
    tool = "bash_read"
    work = _checkout(tmp_path)

    async def ask():
        runner = Runner(tmp_path / "state")
        with room_machine(tmp_path / "machine", checkout=work) as target:
            await runner.start(
                SessionStart("system prompt", None, agent_handle="teammate"),
                binary=shim(tmp_path),
                cwd=str(tmp_path),
                env={"PATH": "/usr/bin:/bin"},
                args=[],
                target=target,
            )
            try:
                return await call(
                    runner.state,
                    "hooks",
                    {
                        "event": "PreToolUse",
                        "tool": tool,
                        "id": "c",
                        "input": {"id": "x"},
                    },
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


def _events(work: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (work / "hooks.log").read_text().splitlines()
        if line.strip()
    ]


def test_an_edit_reaches_the_hooks_as_claude_codes_edit_calls(tmp_path):
    """pi takes several replacements in one `edit`; Claude Code's Edit takes
    one, and the pinned build has no tool that takes several. So the hooks see
    one Edit per replacement, and a deny of any of them stops the whole call."""
    work = _checkout(tmp_path)
    (work / "notes.md").write_text("one\ntwo\n")
    (work / "secrets").mkdir()
    (work / "secrets/key").write_text("kept\n")

    answers = asyncio.run(
        _session(
            tmp_path,
            work,
            [
                (
                    "edit",
                    {
                        "path": "notes.md",
                        "edits": [
                            {"oldText": "one", "newText": "teh first"},
                            {"oldText": "two", "newText": "second"},
                        ],
                    },
                ),
                (
                    "edit",
                    {
                        "path": "secrets/key",
                        "edits": [{"oldText": "kept", "newText": "leaked"}],
                    },
                ),
            ],
        )
    )

    # The typo hook's `updatedInput` reached pi's edit in pi's own keys.
    assert answers[0] == ("ran", "edited")
    assert (work / "notes.md").read_text() == "the first\nsecond\n"
    kind, said = answers[1]
    assert kind == "blocked" and "PROJECT_POLICY: secrets/ is read-only" in said
    assert (work / "secrets/key").read_text() == "kept\n"

    events = _events(work)
    assert [
        (e["hook_event_name"], e["tool_name"], e["tool_use_id"]) for e in events
    ] == [
        ("PreToolUse", "Edit", "call-0.0"),
        ("PreToolUse", "Edit", "call-0.1"),
        ("PostToolUse", "Edit", "call-0.0"),
        ("PostToolUse", "Edit", "call-0.1"),
        ("PreToolUse", "Edit", "call-1"),
    ]
    assert events[0]["tool_input"] == {
        "file_path": "notes.md",
        "old_string": "one",
        "new_string": "teh first",
    }
    assert events[3]["tool_input"] == {
        "file_path": "notes.md",
        "old_string": "two",
        "new_string": "second",
    }
    # PostToolUse sees what ran, after the hook's correction.
    assert events[2]["tool_input"]["new_string"] == "the first"


def _asked(tmp_path: Path, work: Path, calls: list[tuple[str, dict]]):
    """The requests the machine is sent while a session asks about `calls`
    the way the extension asks, before and after each."""

    async def session():
        runner = Runner(tmp_path / "state")
        with room_machine(tmp_path / "machine", checkout=work) as target:
            await runner.start(
                SessionStart("system prompt", None, agent_handle="teammate"),
                binary=shim(tmp_path),
                cwd=str(tmp_path),
                env={"PATH": "/usr/bin:/bin"},
                args=[],
                target=target,
            )
            assert runner.machine is not None
            asked: list[str] = []
            control = runner.machine.client.control

            def counted(request, preparing=None):
                asked.append(request.get("subtype", ""))
                return control(request, preparing=preparing)

            runner.machine.client.control = counted  # type: ignore[method-assign]
            answers = []
            try:
                for index, (tool, args) in enumerate(calls):
                    for event in ("PreToolUse", "PostToolUse"):
                        answers.append(
                            await call(
                                runner.state,
                                "hooks",
                                {
                                    "event": event,
                                    "tool": tool,
                                    "id": f"c{index}",
                                    "input": args,
                                },
                            )
                        )
                return asked, answers
            finally:
                await runner.close()

    return asyncio.run(session())


def test_a_search_reaches_the_hooks_as_claude_codes_grep_and_glob(tmp_path):
    """pi's grep and find look through the project on the machine as Claude
    Code's Grep and Glob do, so a hook written for those sees them."""
    work = _checkout(tmp_path)
    _asked(
        tmp_path / "a",
        work,
        [
            ("grep", {"pattern": "TODO", "ignoreCase": True, "limit": 5}),
            ("find", {"pattern": "*.py", "path": "src"}),
            ("ls", {"path": "src"}),
        ],
    )
    before = [e for e in _events(work) if e["hook_event_name"] == "PreToolUse"]
    assert [(e["tool_name"], e["tool_input"]) for e in before] == [
        (
            "Grep",
            {
                "pattern": "TODO",
                "-i": True,
                "head_limit": 5,
                "output_mode": "content",
                "-n": True,
            },
        ),
        ("Glob", {"pattern": "*.py", "path": "src"}),
        ("ls", {"path": "src"}),
    ]


def test_a_call_no_hook_is_written_for_costs_the_machine_nothing(tmp_path):
    """Hooks run on the machine, so asking about one is a round trip there. A
    project with no hooks pays it for no call; one whose hook is for Bash pays
    it for Bash and for nothing else, and its hook still holds."""
    plain = tmp_path / "plain"
    plain.mkdir()
    asked, _ = _asked(
        tmp_path / "a",
        plain,
        [("bash", {"command": "echo hi"}), ("read", {"path": "a.md"})],
    )
    assert "tool_hooks" not in asked

    guarded = tmp_path / "guarded"
    (guarded / ".claude").mkdir(parents=True)
    (guarded / ".claude/guard.py").write_text(GUARD)
    (guarded / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{sys.executable} "
                                    '"$CLAUDE_PROJECT_DIR/.claude/guard.py"',
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    asked, answers = _asked(
        tmp_path / "b",
        guarded,
        [("read", {"path": "a.md"}), ("bash", {"command": "rm -rf doomed"})],
    )
    assert asked.count("tool_hooks") == 1
    assert answers[2] == {"denied": "PROJECT_POLICY: no recursive deletes"}


def test_a_deny_rule_is_checked_for_the_tools_it_covers(tmp_path):
    """A `Read(...)` deny covers editing the path too, as in Claude Code."""
    work = tmp_path / "room"
    (work / ".claude").mkdir(parents=True)
    (work / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Read(secrets/**)"]}})
    )
    asked, answers = _asked(
        tmp_path / "a",
        work,
        [
            ("bash", {"command": "echo hi"}),
            ("write", {"path": "secrets/key", "content": "x"}),
        ],
    )
    assert asked.count("tool_hooks") == 1
    assert "permissions.deny" in answers[2]["denied"]
