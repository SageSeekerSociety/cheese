"""Agents do not open pull requests; the platform does (`agent/pr_creation.py`)."""

import asyncio
import io

import pytest

from app.domain.agent import pr_creation
from app.domain.agent.harness.claude_code.remote_execution import client
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.pi import hooks

OPENS = [
    "gh pr create --fill",
    'gh pr create --title "fix: x" --body "y" --base main',
    "cd /work/repo && git push -u origin HEAD && gh pr create --fill",
    "GH_TOKEN=x gh pr create --draft",
    "timeout 60 gh pr create --fill",
    "(cd repo; gh pr create --fill)",
    "git push\ngh pr create --fill",
    "bash -lc 'cd ~/cheese && gh pr create --fill'",
    "MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -- bash -lc 'gh pr create --fill'",
    'wsl -e bash -c "gh pr create --fill"',
    "eval 'gh pr create --fill'",
    "gh api repos/o/r/pulls -f title=x -f head=b -f base=main",
    "gh api -X POST repos/o/r/pulls --input body.json",
    "gh api --method=POST /repos/o/r/pulls",
    "gh api -XPOST repos/o/r/pulls",
    "gh api graphql -f query='mutation { createPullRequest(input: {}) { id } }'",
    "hub pull-request -m x",
    "/usr/bin/gh pr create --fill",
]

LEAVES = [
    "gh pr view 12",
    "gh pr list --head task/1",
    "gh pr checks 12",
    "gh pr edit 12 --title x",
    "gh pr merge 12 --squash",
    "gh api repos/o/r/pulls",
    "gh api repos/o/r/pulls/12",
    "gh api -X PATCH repos/o/r/pulls/12 -f title=x",
    "gh api repos/o/r/pulls/12/comments -f body=x",
    "echo 'gh pr create --fill'",
    "grep -rn 'gh pr create' docs",
    "git commit -m 'stop calling gh pr create'",
    ".claude/scripts/pr-watch.sh --enqueue --deploy 12",
    "gh api graphql -f query='{ viewer { login } }'",
]


@pytest.mark.parametrize("command", OPENS)
def test_opening_a_pull_request_is_recognised(command):
    assert pr_creation.opens_pull_request(command)


@pytest.mark.parametrize("command", LEAVES)
def test_other_commands_are_left_alone(command):
    assert not pr_creation.opens_pull_request(command)


def test_unbalanced_quotes_fall_back_to_the_plain_spelling():
    assert pr_creation.opens_pull_request("gh pr create --title 'oops")
    assert not pr_creation.opens_pull_request("echo 'unbalanced")


def test_claude_code_shell_prefix_refuses_before_reaching_the_machine(capsys):
    # The build's own wrapping of a Bash command (`client._SNAPSHOT` / `_CWD_FILE`).
    command = (
        "source /tmp/snap.sh 2>/dev/null || true && eval 'gh pr create --fill'"
        " < /dev/null && pwd -P >| /tmp/claude-1-cwd"
    )
    # No target is needed: the refusal comes before anything is sent.
    assert client.run_on_the_machine({}, command) == 1
    assert capsys.readouterr().err.strip() == pr_creation.REFUSAL


def test_codex_bash_call_is_refused():
    tools = RemoteTools.__new__(RemoteTools)
    notice = io.StringIO()
    receipt = asyncio.run(
        tools._executor_call(
            "native",
            "Bash",
            {"callId": "c1", "arguments": {"command": "gh pr create --fill"}},
            notice,
            True,
        )
    )
    assert receipt == {"error": pr_creation.REFUSAL}


@pytest.mark.parametrize("tool", ["bash", "bash_start"])
def test_pi_bash_is_refused_ahead_of_the_project_hooks(tool):
    with pytest.raises(hooks.Denied, match="PR 由平台开"):
        asyncio.run(
            hooks.run(
                "PreToolUse",
                tool,
                {"command": "gh pr create --fill"},
                call_id="c1",
                machine=None,
                cwd=None,
            )
        )
