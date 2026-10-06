"""Every agent a room's Claude Code session starts is told how to write its step
titles, and how to share the repository and the machine with other tasks.

Each step of an agent the session starts — one from the Agent tool, one from a
workflow — is a line on the room's 施工现场, titled with the description that
agent wrote, and none of them reads the session's system prompt, where the
session itself reads the rule. So this drives the pinned build headless with
the runner's `LAUNCH_ARGS` and the session's settings, has it start one agent
of each kind, and reads what the model was sent on that agent's behalf.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.harness.claude_code.session_launch import session_settings
from app.domain.agent.harness.prompt import SHARED_CHECKOUT, STEP_TITLES
from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"


@pytest.fixture
def contract(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import headless_contract

    yield headless_contract
    sys.modules.pop("headless_contract", None)


def _opening(request) -> str:
    """The first message of the conversation a model request continues."""
    return json.dumps(request["messages"][0], ensure_ascii=False)


def test_agents_and_workflow_agents_are_given_the_rule(tmp_path, contract):
    binary = claude_binary()
    version = subprocess.check_output([binary, "--version"], text=True, timeout=10)
    assert version.startswith(CLAUDE_PINNED_VERSION + " "), version
    session = contract.Session(
        binary, tmp_path, "agents", LAUNCH_ARGS, settings=session_settings()
    )
    try:
        mark = session.user(
            contract.do(
                "Agent",
                description="child",
                subagent_type="general-purpose",
                prompt="AGENT_CHILD reply done",
            )
        )
        _, started = session.wait(
            contract.is_("system", "task_started", task_type="local_agent"), 60, mark
        )
        assert started, (session.root / "stderr.log").read_text()
        session.wait(
            contract.is_("system", "task_notification", task_id=started["task_id"]),
            60,
            mark,
        )
        script = (
            "export const meta = { name: 'titles', description: 'titles probe' }\n"
            "await agent('WORKFLOW_CHILD reply done', { label: 'one' })\n"
        )
        mark = session.user(contract.do("Workflow", script=script))
        _, workflow = session.wait(
            contract.is_("system", "task_started", task_type="local_workflow"),
            60,
            mark,
        )
        assert workflow, (session.root / "stderr.log").read_text()
        session.wait(
            contract.is_("system", "task_notification", task_id=workflow["task_id"]),
            60,
            mark,
        )
        requests = [r for r in session.requests() if r.get("messages")]
    finally:
        session.stop()

    for child in ("AGENT_CHILD", "WORKFLOW_CHILD"):
        # The parent's own messages quote the child's prompt inside a DO:
        # directive; the child's conversation opens on the prompt itself.
        own = [r for r in requests if child in _opening(r) and "DO:" not in _opening(r)]
        assert own, f"no model request was made for {child}"
        assert STEP_TITLES in _opening(own[0]), _opening(own[0])[:2000]
        # The rules about sharing the repository and the machine with other
        # tasks reach it the same way (escaped as the request carries them).
        shared = json.dumps(SHARED_CHECKOUT, ensure_ascii=False)[1:-1]
        assert shared in _opening(own[0]), _opening(own[0])[:2000]
