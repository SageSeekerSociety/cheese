"""Retry continues a failed reply even though its input was already read."""

import uuid

import pytest

from app.api.deps import get_chat_service
from tests.conftest import StubChannel, settle_turn, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
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


def failed_reply(client, *, forgotten_mention=False):
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
    assert bool(message["meta"]["consumed_turn"]) is not forgotten_mention
    assert ERROR in failure["meta"]["detail"]
    return thread, failure


def summon(client, thread, failure):
    response = client.post(
        f"/topics/{thread}/summon",
        json={"retry_block_id": failure["id"]},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_retry_continues_the_read_but_failed_request_once(client, stub_hooks):
    thread, failure = failed_reply(client)
    original_seat = stub_hooks.prompts[-1][0]
    stub_hooks.fail = False

    assert summon(client, thread, failure) == {"started": True}
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
    assert summon(client, thread, failure) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 2
    retired = next(b for b in blocks if b["id"] == failure["id"])
    assert retired["meta"]["retryable"] is False
    assert retired["meta"]["detail"] == failure["meta"]["detail"]


def test_a_second_failure_can_be_retried_without_reviving_the_old_card(
    client, stub_hooks
):
    thread, first = failed_reply(client)
    assert summon(client, thread, first) == {"started": True}
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
    assert summon(client, thread, second) == {"started": True}
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()
    assert len(stub_hooks.prompts) == 3


def test_retry_re_admits_the_failed_unread_batch(client, stub_hooks):
    thread, failure = failed_reply(client, forgotten_mention=True)
    stub_hooks.fail = False
    assert summon(client, thread, failure) == {"started": True}
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
    first_thread, first = failed_reply(client)
    second_thread, second = failed_reply(client)
    assert first_thread != second_thread
    assert summon(client, first_thread, second) == {
        "started": False,
        "reason": "nothing_pending",
    }
    assert len(stub_hooks.prompts) == 2
    stub_hooks.fail = False
    assert summon(client, first_thread, first) == {"started": True}
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(first_thread))
    wait_work_idle()
    assert len(stub_hooks.prompts) == 3
