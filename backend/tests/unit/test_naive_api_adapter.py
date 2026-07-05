"""Unit tests for the naive_api agent loop, with a scripted fake model and a
fake tool executor (no real LLM, no DB)."""

from typing import Any

import pytest

from app.agent.adapters.naive_api.adapter import NaiveApiAgent
from app.agent.adapters.naive_api.types import ModelResponse, ToolCall
from app.agent.authorization.authorizer import ProjectActor
from app.agent.interfaces import Agent, AgentContext

pytestmark = pytest.mark.anyio

_ACTOR = ProjectActor(kind="agent", actor_id=1, project_id=10)


class _ScriptedModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def respond(self, messages, tools) -> ModelResponse:
        self.call_count += 1
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse(text="done")


class _Executor:
    def __init__(self, result: Any = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[ProjectActor, str, dict]] = []
        self._result = result
        self._raise = raise_exc

    async def __call__(self, actor, name, args) -> Any:
        self.calls.append((actor, name, args))
        if self._raise is not None:
            raise self._raise
        return self._result


def _ctx(role_prompt: str = "") -> AgentContext:
    return AgentContext(actor=_ACTOR, role_prompt=role_prompt)


async def test_agent_satisfies_protocol():
    agent = NaiveApiAgent(_ScriptedModel([]), [], _Executor())
    assert isinstance(agent, Agent)


async def test_tool_call_then_done():
    model = _ScriptedModel(
        [
            ModelResponse(
                tool_calls=[ToolCall("c1", "post_message", {"thread_id": 1, "content": "hi"})]
            ),
            ModelResponse(text="all done"),
        ]
    )
    executor = _Executor(result={"block_id": 7})
    agent = NaiveApiAgent(model, [], executor)
    session = await agent.start(_ctx())

    await session.send("do the thing")

    assert executor.calls == [(_ACTOR, "post_message", {"thread_id": 1, "content": "hi"})]
    # the tool result is fed back into the conversation
    tool_msgs = [m for m in session.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "{'block_id': 7}"
    assert session.messages[-1] == {"role": "assistant", "content": "all done"}


async def test_role_prompt_seeded_as_system():
    agent = NaiveApiAgent(_ScriptedModel([ModelResponse(text="ok")]), [], _Executor())
    session = await agent.start(_ctx(role_prompt="you coordinate"))
    assert session.messages[0] == {"role": "system", "content": "you coordinate"}


async def test_step_cap_stops_runaway_tool_loop():
    # model always calls a tool -> the loop must stop at max_steps.
    always_tool = ModelResponse(tool_calls=[ToolCall("c", "post_message", {"x": 1})])
    model = _ScriptedModel([always_tool] * 100)
    executor = _Executor(result="ok")
    agent = NaiveApiAgent(model, [], executor, max_steps=3)
    session = await agent.start(_ctx())

    await session.send("go")

    assert len(executor.calls) == 3
    assert session.messages[-1]["content"] == "[stopped after 3 tool steps]"


async def test_tool_error_is_fed_back_not_fatal():
    model = _ScriptedModel(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "boom", {})]),
            ModelResponse(text="recovered"),
        ]
    )
    executor = _Executor(raise_exc=ValueError("kaboom"))
    agent = NaiveApiAgent(model, [], executor)
    session = await agent.start(_ctx())

    await session.send("go")

    tool_msgs = [m for m in session.messages if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "error: kaboom"
    assert session.messages[-1] == {"role": "assistant", "content": "recovered"}
