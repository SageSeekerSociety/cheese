"""A spent model balance is told to whoever owns the account that ran dry.

A turn on the platform's model supply that meets a provider out of credit ran
into the platform's own account: the room is told the platform has it in hand.
A turn on the team's or member's own model connection met their account, which
nobody on the platform side was told about and only they can top up.
"""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.service import AgentResult, AgentToolUse
from app.domain.agent.turn.steps import hooks as hook_steps
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from tests.conftest import stub_compute
from tests.integration import test_own_claude_code_turns as own_turns
from tests.integration.test_session_turn_event_receiver import _room

# What Anthropic says when the API account behind a key has no credit left.
CREDIT_TOO_LOW = (
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
    '"message":"Your credit balance is too low to access the Anthropic API. '
    'Please go to Plans & Billing to upgrade or purchase credits."}}'
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route", "who", "retryable"),
    [(OWN_ROUTE, "human", False), ("gateway", "platform", True)],
)
async def test_a_spent_balance_is_told_to_whoever_owns_the_account(
    business_db_factory, tmp_path, monkeypatch, route, who, retryable
):
    factory = business_db_factory
    project, topic = await _room(factory, f"Spent balance on {route}")
    seat = await own_turns._seat(factory, project)
    broker = InProcessBroker()
    monkeypatch.setattr(hook_steps, "get_broker", lambda: broker)
    chat = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path),
        compute=stub_compute(),
        work_runner=AgentWorkRunner(broker),
    )
    chat.live.session_route[topic] = route
    work = uuid.uuid4()
    for event in (
        AgentToolUse(
            name="Bash", input={"command": "true"}, agent_handle=seat, session_id="s"
        ),
        AgentResult(
            text=CREDIT_TOO_LOW, session_id="s", is_error=True, agent_handle=seat
        ),
    ):
        await chat.hook_events.accept(project, topic, work, event, None, False, True)

    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic)
    notices = [
        (b.meta or {})
        for b in blocks
        if b.author_type == AuthorType.platform
        and (b.meta or {}).get("event_type") == "turn_failed"
    ]
    assert notices, [(b.author_type, b.content) for b in blocks]
    assert notices[-1]["who"] == who
    assert bool(notices[-1].get("retryable")) is retryable
