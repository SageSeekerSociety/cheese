"""A repository's `permissions.deny` holds in a room, as it does in Claude Code.

A repository writes the commands nobody may run into `.claude/settings.json` —
`git stash`, a force-push, a `git clean` — and Claude Code refuses a call any
of those rules matches. Where Claude Code is not the one making the call, the
rules are read with the project's hooks (`project_hooks.py`), so a pi session's
own tools are driven here through the real runner, over its socket, the way the
extension asks before each call.
"""

import asyncio
import json
from pathlib import Path

from tests.unit.test_pi_tool_hooks import _session

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


def _checkout(tmp_path: Path) -> Path:
    work = tmp_path / "room"
    (work / ".claude").mkdir(parents=True)
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
    # The personal file is read too, as Claude Code reads it.
    (work / ".claude/settings.local.json").write_text(
        json.dumps({"permissions": {"deny": ["Read(.env)"]}})
    )
    (work / "sub").mkdir()
    (work / "sub/.env").write_text("TOKEN=1\n")
    (work / "notes.md").write_text("notes\n")
    return work


def test_a_denied_command_is_refused_and_never_runs(tmp_path):
    work = _checkout(tmp_path)

    answers = asyncio.run(
        _session(
            tmp_path,
            work,
            [
                ("bash", {"command": "git stash"}),
                ("bash", {"command": "echo start && git stash pop; touch RAN"}),
                ("bash", {"command": "FOO=1 git push origin main --force"}),
                ("bash_start", {"command": "git push -f", "label": "push"}),
                ("bash", {"command": "echo $(git clean -fdx)"}),
                ("bash", {"command": "echo 'git stash' && echo fine"}),
                ("bash", {"command": "echo git stashed"}),
            ],
        )
    )

    for kind, said in answers[:5]:
        assert kind == "blocked"
        assert "permissions.deny" in said
    assert "Bash(git stash *)" in answers[0][1]
    assert "Bash(git push * --force*)" in answers[2][1]
    # An allow rule cannot carve an exception out of a deny.
    assert not (work / "RAN").exists()
    # Words that only mention a command, or a longer one, are not it.
    assert answers[5] == ("ran", "git stash\nfine\n")
    assert answers[6] == ("ran", "git stashed\n")


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
