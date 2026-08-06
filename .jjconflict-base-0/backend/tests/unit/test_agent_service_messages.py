"""Claude SDK transport messages become stable ChatService boundaries."""

from collections.abc import AsyncIterator

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

import app.domain.agent.service as service_module
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentService,
    AgentToolUse,
)


class FakeClaudeClient:
    messages: list[object] = []

    def __init__(self, *, options) -> None:
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def query(self, _request) -> None:
        return None

    async def receive_response(self) -> AsyncIterator[object]:
        for message in self.messages:
            yield message


def assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="stub")


def result(text: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="session-1",
        result=text,
    )


async def collect(service: AgentService) -> list[object]:
    return [
        event
        async for event in service.stream_reply(
            prompt="test",
            system_prompt="system",
            cwd=".",
            resume_session_id=None,
        )
    ]


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    monkeypatch.setattr(service_module, "ClaudeSDKClient", FakeClaudeClient)
    FakeClaudeClient.messages = []


@pytest.mark.anyio
async def test_partial_assistant_messages_keep_one_markdown_boundary():
    """Opening fence, body and closing fence must reach chat as one message."""
    fragments = [
        "- **改动**: 紧贴既有 dogfood 标记:\n",
        "  ```python\n",
        "  # dogfood loop: accepted on cheesex\n",
        "  # self-update on dev: written via the platform\n",
        "  ```\n",
    ]
    FakeClaudeClient.messages = [
        *(assistant(TextBlock(fragment)) for fragment in fragments),
        result(fragments[-1]),
    ]

    events = await collect(AgentService(model="stub"))

    messages = [event for event in events if isinstance(event, AgentMessage)]
    assert [event.text for event in messages] == ["".join(fragments)]
    assert isinstance(events[-1], AgentResult)
    assert events[-1].text == "".join(fragments)


@pytest.mark.anyio
async def test_tool_call_splits_two_logical_messages():
    FakeClaudeClient.messages = [
        assistant(TextBlock("我先查一下")),
        assistant(ToolUseBlock(id="tool-1", name="Grep", input={"pattern": "TODO"})),
        assistant(TextBlock("查完了")),
        result("查完了"),
    ]

    events = await collect(AgentService(model="stub"))

    assert [type(event) for event in events] == [
        AgentMessage,
        AgentToolUse,
        AgentMessage,
        AgentResult,
    ]
    assert [event.text for event in events if isinstance(event, AgentMessage)] == [
        "我先查一下",
        "查完了",
    ]
