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
    last_options: object = None

    def __init__(self, *, options) -> None:
        self.options = options
        FakeClaudeClient.last_options = options

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


@pytest.mark.anyio
async def test_sandbox_turn_denies_the_tool_no_user_can_answer():
    """`bypassPermissions` means the allowlist restricts nothing — a tool left
    out of `allowed_tools` is still callable. AskUserQuestion has no UI on this
    platform (its picker is drawn in the sandbox's own terminal), so a call to
    it strands the turn; only an explicit deny keeps it away. `cheese ask` is
    the platform's way to put a question in front of a user."""
    FakeClaudeClient.messages = [result("done")]

    events = [
        event
        async for event in AgentService(model="stub").stream_reply(
            prompt="test",
            system_prompt="system",
            cwd=".",
            resume_session_id=None,
            sandbox={"cli_path": "/x/claude-sbx", "allowed_tools": ["Bash"], "env": {}},
        )
    ]

    assert isinstance(events[-1], AgentResult)
    options = FakeClaudeClient.last_options
    assert "AskUserQuestion" in options.disallowed_tools
    assert "AskUserQuestion" not in options.allowed_tools


@pytest.mark.anyio
async def test_plain_turn_denies_it_too():
    """The no-sandbox turn already disallows the built-ins; the unanswerable
    question tool belongs in the same deny list, not just the sandbox one."""
    FakeClaudeClient.messages = [result("done")]

    await collect(AgentService(model="stub"))

    assert "AskUserQuestion" in FakeClaudeClient.last_options.disallowed_tools
