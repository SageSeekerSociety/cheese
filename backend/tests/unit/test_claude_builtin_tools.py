"""Exercise platform tool policy in the pinned headless Claude process.

The runner starts the build with `LAUNCH_ARGS` and the session's settings file;
what the model is offered under exactly those is what a room's session can do.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.harness.claude_code.session_launch import session_settings
from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"


@pytest.fixture
def contract(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import headless_contract

    yield headless_contract
    sys.modules.pop("headless_contract", None)


def _offered(contract, root, args, settings=None) -> set[str]:
    """The tool names the pinned build offers the model on its first request."""
    binary = claude_binary()
    version = subprocess.check_output([binary, "--version"], text=True, timeout=10)
    assert version.startswith(CLAUDE_PINNED_VERSION + " "), version
    session = contract.Session(binary, root, "tools", args, settings=settings)
    try:
        mark = session.user("hello")
        _, result = session.wait(contract.is_("result"), 60, mark)
        assert result is not None, (root / "tools/stderr.log").read_text()
        first = next(r for r in session.requests() if r.get("tools"))
        return {tool["name"] for tool in first["tools"]}
    finally:
        session.stop()


@pytest.fixture
def offered(tmp_path, contract):
    return _offered(contract, tmp_path, LAUNCH_ARGS, session_settings())


def test_the_pinned_build_has_the_tools_the_platform_takes_away(tmp_path, contract):
    """Otherwise the refusal below would pass against a build that never had them.

    (AskUserQuestion is not among them: headless, the build does not offer it
    without a way to ask. The runner test covers the model calling it anyway.)
    """
    stock = _offered(contract, tmp_path, contract.STREAM)
    assert {"CronCreate", "ScheduleWakeup", "TaskCreate", "TaskList"} <= stock


def test_the_platform_owns_tasks_reminders_and_questions(offered):
    assert not offered & {
        "AskUserQuestion",
        "TodoWrite",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "CronCreate",
        "CronDelete",
        "CronList",
        "ScheduleWakeup",
    }


def test_the_session_keeps_its_own_agents_and_tools(offered):
    assert {"Agent", "SendMessage", "TaskStop", "Bash", "Read", "WebFetch"} <= offered
