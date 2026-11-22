"""The channel's main line is offered only the tools its kind holds, and a call
to any other is refused before it reaches the platform.

The main line talks with members and coordinates the channel's tasks; the
work itself is a task's. Its launch says what it holds (`session_env`), and
the platform's tool table is read through that on every harness
(`sandbox/cheese` `offered_tools`, `run_platform_tool`).
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent.room.system_prompt import session_env
from app.domain.prompts import Kind

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_main_line_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Host:
    def __init__(self, environ):
        self.environ = dict(environ)
        self.requests: list[dict] = []

    def request(self, plan):
        self.requests.append(plan)
        return {"data": {}}

    def sync_task(self, task_id):
        raise AssertionError("no machine is reached")


def _offered(environ) -> dict[str, list[str] | None]:
    out = {}
    for row in cheese.offered_tools(environ):
        action = row["inputSchema"]["properties"].get("action")
        out[row["name"]] = sorted(action["enum"]) if action else None
    return out


def test_the_main_line_is_offered_what_it_holds_and_nothing_else():
    offered = _offered(session_env(Kind.main_line))
    assert "chat_send" in offered
    assert "cheese_card" not in offered
    assert "todo_write" not in offered
    assert "state" not in (offered["cheese_task"] or [])
    assert "open" in (offered["cheese_task"] or [])


def test_a_call_outside_what_the_main_line_holds_is_refused():
    host = Host(session_env(Kind.main_line))
    with pytest.raises(cheese.PlatformToolError):
        cheese.run_platform_tool(
            "cheese_task", {"action": "state", "state": "working"}, host
        )
    assert host.requests == []


def test_a_task_session_is_offered_the_whole_table():
    assert _offered(session_env(Kind.task)) == _offered({})
