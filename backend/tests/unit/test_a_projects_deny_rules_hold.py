"""A repository's `permissions.deny` holds in a room, as it does in Claude Code.

A repository writes the commands nobody may run into `.claude/settings.json` —
`git stash`, a force-push, a `git clean` — and Claude Code refuses a call any
of those rules matches. The same commands meet the same refusal whichever
harness the room runs: a pi session's own tools, driven here through the real
runner over its socket the way the extension asks before each call; a Codex
session's, through the tools the room's machine serves it; and a Claude Code
session's Bash, which the pinned build runs itself on the room's machine,
though it never loads the project's settings.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.pinned_claude import claude_binary
from tests.unit.test_pi_tool_hooks import _session
from tests.unit.test_remote_mcp_hooks import SCRIPTS, acceptance  # noqa: F401

#: This repository's own list, as its settings file writes it.
DENY = [
    "Bash(git stash *)",
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(git push * --force*)",
    "Bash(git push * -f*)",
    "Bash(git checkout -- .)",
    "Bash(git clean *)",
]

#: One scenario for every harness: each command, and the rule that refuses it,
#: or None where it runs. A refused command never runs, so `RAN` never appears.
COMMANDS = [
    ("git stash", "Bash(git stash *)"),
    ("echo start && git stash pop; touch RAN", "Bash(git stash *)"),
    ("FOO=1 git push origin main --force", "Bash(git push * --force*)"),
    ("echo $(git clean -fdx)", "Bash(git clean *)"),
    # Words that only mention a command, or a longer one, are not it.
    ("echo 'git stash' && echo fine", None),
    ("echo git stashed", None),
]


def _settings(work: Path) -> None:
    (work / ".claude").mkdir(parents=True, exist_ok=True)
    (work / ".claude/settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["Bash(git stash *)"],
                    "deny": DENY + ["Edit(/protected/**)"],
                }
            }
        )
    )


def _held(work: Path, answers: list[tuple[str, str]]) -> None:
    """The scenario's verdicts, from `(ran|blocked, text)` per command."""
    for (command, rule), (kind, said) in zip(COMMANDS, answers, strict=True):
        if rule is None:
            assert kind == "ran", (command, said)
        else:
            assert kind == "blocked", (command, said)
            assert "permissions.deny" in said and rule in said, (command, said)
    # An allow rule cannot carve an exception out of a deny.
    assert not (work / "RAN").exists()
    assert "fine" in answers[4][1]
    assert "git stashed" in answers[5][1]


def _checkout(tmp_path: Path) -> Path:
    work = tmp_path / "room"
    _settings(work)
    # The personal file is read too, as Claude Code reads it.
    (work / ".claude/settings.local.json").write_text(
        json.dumps({"permissions": {"deny": ["Read(.env)"]}})
    )
    (work / "sub").mkdir()
    (work / "sub/.env").write_text("TOKEN=1\n")
    (work / "notes.md").write_text("notes\n")
    return work


def test_a_pi_session_refuses_a_denied_command(tmp_path):
    work = _checkout(tmp_path)

    answers = asyncio.run(
        _session(
            tmp_path,
            work,
            [("bash", {"command": command}) for command, _ in COMMANDS]
            + [("bash_start", {"command": "git push -f", "label": "push"})],
        )
    )

    _held(work, answers[:-1])
    kind, said = answers[-1]
    assert kind == "blocked" and "Bash(git push -f*)" in said


def _machine(acceptance, folder, monkeypatch):  # noqa: F811
    """A room's machine over a project whose settings hold DENY."""
    home = folder / "executor-home"
    (home / ".claude").mkdir(parents=True)
    with monkeypatch.context() as scoped:
        scoped.setenv("HOME", str(home))
        scoped.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
        executor, target = acceptance.setup(
            folder,
            argparse.Namespace(ssh=None, claude=claude_binary()),
            "http://127.0.0.1:9",
        )
    project = Path(
        json.loads((folder / "executor-input.json").read_text())["workspace"]
    )
    _settings(project)
    return executor, target, project


def test_a_codex_session_refuses_a_denied_command(
    acceptance,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from app.domain.agent.harness.codex.tools import RemoteTools

    executor, target, project = _machine(acceptance, tmp_path / "codex", monkeypatch)
    monkeypatch.setenv("CHEESE_API", "http://127.0.0.1:9")
    monkeypatch.setenv("CHEESE_TOKEN", "room-token")
    try:
        tools = RemoteTools(target)
        asyncio.run(tools.discover([]))
        answers = []
        for index, (command, _) in enumerate(COMMANDS):
            answer = asyncio.run(
                tools(
                    "item/tool/call",
                    {
                        "tool": "Bash",
                        "callId": f"call-{index}",
                        "arguments": {"command": command},
                    },
                )
            )
            said = "".join(item.get("text", "") for item in answer["contentItems"])
            answers.append(("ran" if answer["success"] else "blocked", said))
        _held(project, answers)
    finally:
        subprocess.run(executor.command("stop"), capture_output=True, timeout=30)


@pytest.mark.skipif(
    sys.platform != "linux", reason="the session's namespace is Linux's"
)
def test_a_claude_code_session_refuses_a_denied_command(
    acceptance,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """The build runs Bash itself, and started with only the user's settings
    it never reads the project's rules; the room's machine refuses it first."""
    sys.path.insert(0, str(SCRIPTS))
    import headless_contract as contract

    from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS

    folder = tmp_path / "room"
    release = acceptance.execution_release
    monkeypatch.setattr(release, "mount_state", lambda _path: release.MOUNT_LIVE)
    executor, target, project = _machine(acceptance, folder, monkeypatch)
    home = folder / "home"
    (home / ".claude").mkdir(parents=True)
    launch = acceptance.client.prepare(
        home / "session",
        target,
        claude=claude_binary(),
        home_override=home,
        config_override=home / ".claude",
    )
    session = contract.Session(
        claude_binary(),
        tmp_path,
        "room",
        LAUNCH_ARGS,
        launch=launch,
        env={"CHEESE_API": "http://127.0.0.1:9", "CHEESE_TOKEN": "room-token"},
    )
    session.executor = executor
    session.remote_workspace = project
    try:
        answers = []
        for command, _ in COMMANDS:
            mark = session.user(contract.do("Bash", command=command))
            _, result = session.wait(contract.is_("result"), 90, mark)
            assert result is not None, (session.root / "stderr.log").read_text()[-2000:]
            (outcome,) = contract.results_since(session, mark)
            answers.append(
                (
                    "blocked" if outcome.get("is_error") else "ran",
                    contract.text_of(outcome),
                )
            )
        _held(project, answers)
    finally:
        contract.stop_remote(session)
        sys.path.remove(str(SCRIPTS))


def test_denied_paths_hold_for_reading_and_editing(tmp_path):
    work = _checkout(tmp_path)

    answers = asyncio.run(
        _session(
            tmp_path,
            work,
            [
                ("read", {"path": "sub/.env"}),
                ("write", {"path": "sub/.env", "content": "TOKEN=2\n"}),
                ("write", {"path": "protected/a.md", "content": "x"}),
                ("write", {"path": "src/protected/a.md", "content": "x"}),
                ("read", {"path": "notes.md"}),
            ],
        )
    )

    # `.env` anywhere under the checkout, for reading and so for editing too.
    assert [kind for kind, _ in answers[:3]] == ["blocked"] * 3
    assert (work / "sub/.env").read_text() == "TOKEN=1\n"
    assert not (work / "protected").exists()
    # `/protected` is the project's own top-level directory, not any of that name.
    assert answers[3] == ("ran", "written")
    assert answers[4] == ("ran", "notes\n")
