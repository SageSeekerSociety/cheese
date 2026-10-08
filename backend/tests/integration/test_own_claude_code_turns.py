"""What a turn of a member's own Claude Code costs, and what its usage limit does.

A member's own Claude Code runs on its owner's login (#2991): what it does is
recorded, and none of it is charged to the project. When its owner's account
refuses it for a usage window, it goes on by itself once the window resets, and
the room is told how long that is.

Claude Code reports its own usage at the end of every turn. That report must
not be what charges a turn the platform already meters elsewhere — the
metering proxy for a subscription turn — or the turn is charged twice.

These run the real close of a turn (`ChatService._close_hook_work`) over a
Claude Code result as the assembler reads it, against PostgreSQL.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.live_work import HookWorkState, LiveWork
from app.domain.agent.service import AgentResult
from app.domain.agent.supply import SUBSCRIPTION
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block
from app.domain.delivery.models import TimedDelivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.usage.models import ResourceUsage
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room

USAGE = {
    "input_tokens": 120,
    "cache_creation_input_tokens": 3000,
    "cache_read_input_tokens": 9000,
    "output_tokens": 400,
}


def _state(project, topic, seat, route):
    return HookWorkState(
        project_id=project,
        topic_id=topic,
        work_id=uuid.uuid4(),
        pending_ids=set(),
        reply_to=None,
        roster=[],
        topic_refs=[],
        continuation_id=None,
        route=route,
        acting_agent=seat,
        agent_pool=None,
        user_text="",
        started_at=datetime.now(UTC),
        agent_instance_handle=seat,
    )


def _result(seat, *records) -> AgentResult:
    """The turn's records through the assembler; its last answer is the result."""
    assembler = Assembler({}, session_id="native-session")
    events = []
    for record in records:
        events = assembler.accept(record)
    (result,) = events
    assert isinstance(result, AgentResult)
    return result


def _finished(seat, **changes):
    record = {
        "type": "result",
        "session_id": "native-session",
        "is_error": False,
        "result": "done",
        "usage": USAGE,
        "total_cost_usd": 0.42,
        "modelUsage": {"claude-opus-5-5": {"outputTokens": 400}},
        "cheese": {"agent_handle": seat},
    }
    record.update(changes)
    return record


def _chat(factory):
    chat = ChatService.__new__(ChatService)
    chat._sessions, chat._gateway = factory, None
    chat.live = LiveWork()
    return chat


async def _seat(factory, project) -> str:
    async with factory() as session:
        instance = await session.scalar(
            select(AgentInstance).where(AgentInstance.project_id == project)
        )
        assert instance is not None
        return agent_instance_handle(instance.id)


async def _usage(factory, turn_id):
    async with factory() as session:
        return list(
            await session.scalars(
                select(ResourceUsage).where(ResourceUsage.turn_id == turn_id)
            )
        )


def test_an_own_turn_is_recorded_and_not_charged(client):
    project = uuid.UUID(_project(client, "own usage"))
    topic = uuid.UUID(_room(client, str(project), "own turn"))

    async def run():
        factory = client.test_request_factory
        seat = await _seat(factory, project)
        state = _state(project, topic, seat, OWN_ROUTE)
        await _chat(factory)._close_hook_work(state, _result(seat, _finished(seat)))
        (row,) = await _usage(factory, state.work_id)
        assert row.credits == 0, "the owner's own login paid for it"
        assert row.output_tokens == 400
        assert row.input_tokens == 120 + 3000 + 9000, "every token it sent"
        assert row.model == "claude-opus-5-5"

    client.portal.call(run)


def test_a_subscription_turn_is_not_charged_from_what_the_session_reports(client):
    """The metering proxy lands a subscription turn's cost from the requests it
    carried; the session's own report would charge the same turn again."""
    project = uuid.UUID(_project(client, "subscription usage"))
    topic = uuid.UUID(_room(client, str(project), "subscription turn"))

    async def run():
        factory = client.test_request_factory
        seat = await _seat(factory, project)
        state = _state(project, topic, seat, SUBSCRIPTION)
        await _chat(factory)._close_hook_work(state, _result(seat, _finished(seat)))
        rows = await _usage(factory, state.work_id)
        assert sum(row.credits for row in rows) == 0
        assert sum(row.output_tokens for row in rows) == 0

    client.portal.call(run)


def test_an_own_turn_refused_by_its_usage_window_goes_on_when_it_resets(client):
    project = uuid.UUID(_project(client, "own limit"))
    topic = uuid.UUID(_room(client, str(project), "limited turn"))
    resets = datetime.now(UTC) + timedelta(hours=2)

    async def run():
        factory = client.test_request_factory
        seat = await _seat(factory, project)
        state = _state(project, topic, seat, OWN_ROUTE)
        refused = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": int(resets.timestamp()),
                "rateLimitType": "five_hour",
            },
        }
        result = _result(seat, refused, _finished(seat, result="limit reached"))
        await _chat(factory)._close_hook_work(state, result)
        async with factory() as session:
            (note,) = list(await session.scalars(select(TimedDelivery)))
            lines = list(
                await session.scalars(
                    select(Block).where(Block.conversation_id == topic)
                )
            )
        assert note.recipient_handle == seat
        assert note.conversation_id == topic
        assert note.due_at >= resets, "not before the window resets"
        assert note.due_at <= resets + timedelta(minutes=5)
        told = [line for line in lines if "分钟后恢复" in (line.content or "")]
        assert told, "the room is told how long it waits"

    client.portal.call(run)


def test_a_window_that_still_allows_the_turn_schedules_nothing(client):
    project = uuid.UUID(_project(client, "own allowed"))
    topic = uuid.UUID(_room(client, str(project), "allowed turn"))

    async def run():
        factory = client.test_request_factory
        seat = await _seat(factory, project)
        state = _state(project, topic, seat, OWN_ROUTE)
        allowed = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "resetsAt": 1},
        }
        await _chat(factory)._close_hook_work(
            state, _result(seat, allowed, _finished(seat))
        )
        async with factory() as session:
            assert list(await session.scalars(select(TimedDelivery))) == []

    client.portal.call(run)
