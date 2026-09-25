"""A hosted repository's own PreToolUse hooks hold for its room's tool calls.

A repository that guards its tools with a hook in `.claude/settings.json`
gets that guard in plain Claude Code, and must get it in a room without
changing anything. In a room the model's Bash and Write never run where the
model is. Bash the build runs itself through its shell prefix, which carries
the command to the executor that holds the project; the session registers the
project's hooks when it starts, as plain Claude Code does, so the build fires
them with its own input and their commands run there too. Write the plugin
hands to the executor, which runs the project's hooks around the call. So
these drive the pinned build headless, launched the way a room's session is
(the runner's `LAUNCH_ARGS`, `client.prepare`'s plugin, shell prefix and
guard), against the acceptance executor over a seeded project, and read what
happened on the executor and what the model was sent back.

The hook scripts below are written the way a repository writes them for plain
Claude Code: invoked through `$CLAUDE_PROJECT_DIR`, deciding by exit code or by
a `permissionDecision`, and each case asserts what plain Claude Code 2.1.277
does with the same script.
"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"

# The room's session runs in a namespace of its own (`client.py enter`), with
# the project at the executor's path, which is Linux's to give.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the session's namespace is Linux's"
)

GUARD = """#!/bin/sh
input=$(cat)
case "$input" in
  *FORBIDDEN*)
    printf '%s' '{"hookSpecificOutput": {"hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "PROJECT_POLICY: this repository forbids that"}}'
    ;;
esac
"""


@pytest.fixture
def contract():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import headless_contract

        yield headless_contract
    finally:
        sys.path.remove(str(SCRIPTS))
        for name in ("headless_contract", "model_fixture", "acceptance", "release"):
            sys.modules.pop(name, None)


@pytest.fixture
def room(contract, tmp_path, monkeypatch):
    """A room's session over a project whose settings hold `room.hooks`.

    The settings are the project's before the session starts, as a
    repository's are: the build registers hooks when it starts.
    """
    import acceptance

    binary = claude_binary()
    version = subprocess.check_output([binary, "--version"], text=True, timeout=10)
    assert version.startswith(CLAUDE_PINNED_VERSION + " "), version
    # The central workspace is a forwarded view this fixture does not mount.
    release = acceptance.execution_release
    monkeypatch.setattr(release, "mount_state", lambda _path: release.MOUNT_LIVE)
    sessions = []

    def start(hooks, *, directories=()):
        name = f"room{len(sessions)}"
        folder = tmp_path / name
        home, executor_home = folder / "home", folder / "executor-home"
        for path in (home / ".claude", executor_home / ".claude"):
            path.mkdir(parents=True)
        with monkeypatch.context() as scoped:
            # The executor's `claude mcp serve` inherits this environment.
            scoped.setenv("HOME", str(executor_home))
            scoped.setenv("CLAUDE_CONFIG_DIR", str(executor_home / ".claude"))
            executor, target = acceptance.setup(
                folder,
                argparse.Namespace(ssh=None, claude=binary),
                "http://127.0.0.1:9",
            )
        project = Path(
            json.loads((folder / "executor-input.json").read_text())["workspace"]
        )
        (project / ".claude/hooks").mkdir(parents=True, exist_ok=True)
        (project / ".claude/hooks/guard.sh").write_text(GUARD)
        (project / ".claude/hooks/guard.sh").chmod(0o755)
        (project / ".claude/settings.json").write_text(
            json.dumps({"hooks": {"PreToolUse": hooks}})
        )
        launch = acceptance.client.prepare(
            home / "session",
            target,
            claude=binary,
            home_override=home,
            config_override=home / ".claude",
        )
        for directory in directories:
            # On the executor, and in the view the unmounted fixture stands in
            # for, where the build checks its shell's directory exists.
            (project / directory).mkdir()
            (Path(launch["cwd"]) / directory).mkdir()
        session = contract.Session(binary, tmp_path, name, LAUNCH_ARGS, launch=launch)
        session.executor = executor
        session.remote_workspace = project
        sessions.append(session)
        return session

    yield start
    for session in sessions:
        contract.stop_remote(session)


def run(contract, session, tool, **arguments):
    """One tool call: its tool_result, and what the model was sent back."""
    mark = session.user(contract.do(tool, **arguments))
    _, result = session.wait(contract.is_("result"), 90, mark)
    assert result is not None, (session.root / "stderr.log").read_text()[-2000:]
    (outcome,) = contract.results_since(session, mark)
    sent = [
        contract.text_of(block)
        for request in session.requests()
        for block in contract.last_tool_results(request)
        if block.get("tool_use_id") == outcome["tool_use_id"]
    ]
    return outcome, sent


def bash_hook(command):
    return [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]


def test_a_project_hook_denies_a_forwarded_call_and_the_model_reads_why(contract, room):
    session = room(bash_hook('"$CLAUDE_PROJECT_DIR"/.claude/hooks/guard.sh'))
    project = session.remote_workspace

    denied, sent = run(
        contract,
        session,
        "Bash",
        command="echo FORBIDDEN > forbidden.txt",
        description="forbidden",
    )
    assert denied.get("is_error") is True, denied
    assert "PROJECT_POLICY: this repository forbids that" in contract.text_of(denied)
    assert sent and all("PROJECT_POLICY" in text for text in sent), sent
    assert not (project / "forbidden.txt").exists()
    assert not (session.workspace / "forbidden.txt").exists()

    allowed, _ = run(
        contract,
        session,
        "Bash",
        command="echo ALLOWED > allowed.txt",
        description="allowed",
    )
    assert allowed.get("is_error") is not True, allowed
    assert (project / "allowed.txt").read_text() == "ALLOWED\n"


def test_a_project_hook_exiting_2_blocks_a_write_with_its_message(contract, room):
    session = room(
        [
            {
                "matcher": "Write|Edit",
                "hooks": [
                    {"type": "command", "command": "echo NO_WRITES_HERE >&2; exit 2"}
                ],
            }
        ]
    )
    project = session.remote_workspace

    denied, sent = run(
        contract,
        session,
        "Write",
        file_path=str(session.workspace / "blocked.txt"),
        content="SHOULD_NOT_LAND",
    )
    assert denied.get("is_error") is True, denied
    assert sent and all("NO_WRITES_HERE" in text for text in sent), sent
    assert not (project / "blocked.txt").exists()

    ran, _ = run(
        contract, session, "Bash", command="echo STILL_RUNS", description="other tool"
    )
    assert "STILL_RUNS" in contract.text_of(ran), ran


def test_a_project_hook_that_fails_otherwise_does_not_block_the_call(contract, room):
    """Plain Claude Code treats any exit but 2 as a non-blocking hook error."""
    session = room(bash_hook("echo lint unavailable >&2; exit 1"))

    ran, _ = run(
        contract,
        session,
        "Bash",
        command="echo RAN > ran.txt",
        description="runs despite the hook error",
    )
    assert ran.get("is_error") is not True, ran
    assert (session.remote_workspace / "ran.txt").read_text() == "RAN\n"


def test_a_project_hook_runs_where_the_command_runs(contract, room, tmp_path):
    """`$CLAUDE_PROJECT_DIR` is the project root, the hook's cwd the shell's."""
    seen = tmp_path / "hook-saw.txt"
    session = room(
        bash_hook(
            'printf "%s|%s" "$PWD" "$CLAUDE_PROJECT_DIR" > ' + shlex.quote(str(seen))
        ),
        directories=["sub"],
    )
    project = session.remote_workspace
    run(contract, session, "Bash", command="cd sub", description="enter sub")
    run(contract, session, "Bash", command="true", description="second call")
    cwd, root = seen.read_text().split("|")
    assert Path(root).resolve() == project.resolve()
    assert Path(cwd).resolve() == (project / "sub").resolve()
