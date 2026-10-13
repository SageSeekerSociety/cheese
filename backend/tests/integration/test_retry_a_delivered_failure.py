"""Retry continues a failed reply even though its input was already read."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block
from app.domain.delivery.models import NativeInput
from tests.conftest import StubChannel, settle_turn, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)

ERROR = "API Error: Stream idle timeout - no chunks received (Unknown)"


class FailsAfterReading(StubChannel):
    def __init__(self, **policy):
        super().__init__(**policy)
        self.fail = True
        self.prompts = []

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        self.prompts.append((agent, prompt))
        if not self.fail:
            return super().emit_turn(topic_id, prompt, reply, agent=agent)
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.record(
            topic_id,
            agent=agent,
            type="result",
            subtype="error_during_execution",
            is_error=True,
            result=ERROR,
        )


@pytest.fixture
def stub_hooks():
    return FailsAfterReading()


def work_state(client, conversation):
    async def read():
        async with client.test_factory() as session:
            turns = list(
                await session.scalars(
                    select(AgentTurn)
                    .where(AgentTurn.conversation_id == uuid.UUID(conversation))
                    .order_by(AgentTurn.started_at)
                )
            )
            inputs = list(
                await session.scalars(
                    select(NativeInput).where(
                        NativeInput.conversation_id == uuid.UUID(conversation)
                    )
                )
            )
            return turns, inputs

    return asyncio.run(read())


def failed_reply(client, stub_hooks, *, forgotten_mention=False):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    thread = in_thread(client, room, "alice")
    with room_socket(client, thread, "alice") as ws:
        content = "核对分页方案" if forgotten_mention else "@芝士 核对分页方案"
        post_message(client, thread, "alice", {"content": content})
        if forgotten_mention:
            response = client.post(
                f"/topics/{thread}/summon",
                json={},
                headers=session_auth_headers("alice"),
            )
            assert response.json()["data"] == {"started": True}
        while ws.receive_json()["type"] != "done":
            pass
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()
    blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
    message = next(
        b for b in blocks if b["author"] == "alice" and "核对分页方案" in b["content"]
    )
    failure = next(b for b in blocks if (b.get("meta") or {}).get("retryable"))
    # A failed reply is not successful completion. Receipt is its own fact.
    assert message["meta"]["consumed_turn"] is None
    assert "核对分页方案" in stub_hooks.prompts[-1][1]
    turns, inputs = work_state(client, thread)
    assert inputs and any(row.echoed_at is not None for row in inputs)
    assert all(row.completed_at is None for row in inputs)
    assert stub_hooks.prompts[-1][0] == room_agent_seat(client, room)
    assert any(str(turn.id) == failure["turn_id"] for turn in turns)
    assert ERROR in failure["meta"]["detail"]
    return thread, failure


def set_failure_event(client, failure, event_type, *, without_turn=False):
    async def update():
        async with client.test_factory() as session:
            block = await session.get(Block, uuid.UUID(failure["id"]))
            assert block is not None
            block.meta = {**(block.meta or {}), "event_type": event_type}
            if without_turn:
                block.turn_id = None
            await session.commit()

    asyncio.run(update())


def summon(client, thread, failure, *, require_started=False):
    response = client.post(
        f"/topics/{thread}/summon",
        json={"retry_block_id": failure["id"]},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    if require_started and result != {"started": True}:
        turns, inputs = work_state(client, thread)
        state = {
            "turns": [
                {
                    "id": str(t.id),
                    "seat": t.agent_handle,
                    "content": t.content,
                    "continuation": str(t.continuation_id),
                    "delivered": t.delivered_at,
                    "stopped": t.stopped_at,
                    "resendable": t.resendable,
                }
                for t in turns
            ],
            "inputs": [
                {
                    "work": str(i.work_id),
                    "execution": str(i.execution_work_id),
                    "seat": i.recipient_handle,
                    "echoed": i.echoed_at,
                    "completed": i.completed_at,
                    "terminated": i.terminated_at,
                    "termination": i.termination,
                }
                for i in inputs
            ],
        }
        pytest.fail(f"Retry did not start: {result}; failure={failure}; state={state}")
    return result


@pytest.mark.parametrize(
    "event_type",
    ["turn_failed", "turn_timeout", "deploy_interrupted", "dispatch_unknown"],
)
def test_retry_continues_the_read_but_failed_request_once(
    client, stub_hooks, event_type
):
    thread, failure = failed_reply(client, stub_hooks)
    set_failure_event(client, failure, event_type)
    original_seat = stub_hooks.prompts[-1][0]
    stub_hooks.fail = False

    assert summon(client, thread, failure, require_started=True) == {"started": True}
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()

    assert len(stub_hooks.prompts) == 2
    assert stub_hooks.prompts[-1][0] == original_seat
    assert "核对分页方案" in stub_hooks.prompts[-1][1]
    blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
    assert (
        sum(b["author"] == "alice" and "核对分页方案" in b["content"] for b in blocks)
        == 1
    )
    original = next(
        b for b in blocks if b["author"] == "alice" and "核对分页方案" in b["content"]
    )
    turns, _ = work_state(client, thread)
    retry_turn = max(turns, key=lambda turn: turn.started_at)
    assert retry_turn.reply_to == uuid.UUID(original["id"])
    replies = [
        b
        for b in blocks
        if b["turn_id"] == str(retry_turn.id) and (b.get("meta") or {}).get("progress")
    ]
    assert replies and all(b["reply_to"] == original["id"] for b in replies)
    assert summon(client, thread, failure) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 2
    retired = next(b for b in blocks if b["id"] == failure["id"])
    assert retired["meta"]["retryable"] is False
    assert retired["meta"]["detail"] == failure["meta"]["detail"]


@pytest.mark.parametrize(
    "event_type", ["turn_timeout", "deploy_interrupted", "dispatch_unknown"]
)
def test_a_notice_without_identifiable_work_retires_without_starting_unrelated_work(
    client, stub_hooks, event_type
):
    thread, failure = failed_reply(client, stub_hooks)
    set_failure_event(client, failure, event_type, without_turn=True)
    stub_hooks.fail = False
    assert summon(client, thread, failure) == {
        "started": False,
        "reason": "nothing_pending",
    }
    wait_work_idle()
    assert len(stub_hooks.prompts) == 1
    blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
    retired = next(b for b in blocks if b["id"] == failure["id"])
    assert retired["meta"]["retryable"] is False
    assert retired["meta"]["detail"] == failure["meta"]["detail"]
    assert summon(client, thread, retired) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 1


def test_a_second_failure_can_be_retried_without_reviving_the_old_card(
    client, stub_hooks
):
    thread, first = failed_reply(client, stub_hooks)
    assert summon(client, thread, first, require_started=True) == {"started": True}
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()
    blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
    second = next(b for b in reversed(blocks) if (b.get("meta") or {}).get("retryable"))
    assert second["id"] != first["id"]
    assert summon(client, thread, first) == {
        "started": False,
        "reason": "nothing_pending",
    }
    stub_hooks.fail = False
    assert summon(client, thread, second, require_started=True) == {"started": True}
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()
    assert len(stub_hooks.prompts) == 3


def test_retry_re_admits_the_failed_unread_batch(client, stub_hooks):
    thread, failure = failed_reply(client, stub_hooks, forgotten_mention=True)
    stub_hooks.fail = False
    assert summon(client, thread, failure, require_started=True) == {"started": True}
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()
    assert "核对分页方案" in stub_hooks.prompts[-1][1]
    blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
    message = next(
        b for b in blocks if b["author"] == "alice" and "核对分页方案" in b["content"]
    )
    assert message["meta"]["consumed_turn"]
    assert summon(client, thread, failure) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 2


def test_retry_cannot_target_a_failure_in_another_conversation(client, stub_hooks):
    first_thread, first = failed_reply(client, stub_hooks)
    second_thread, second = failed_reply(client, stub_hooks)
    assert first_thread != second_thread
    assert summon(client, first_thread, second) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 2
    stub_hooks.fail = False
    assert summon(client, first_thread, first, require_started=True) == {
        "started": True
    }
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(first_thread))
    wait_work_idle()
    assert len(stub_hooks.prompts) == 3
