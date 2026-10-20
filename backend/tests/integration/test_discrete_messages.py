"""What a session prints, Stop included, stays in its record, not the room."""

import uuid

import pytest

from tests.conftest import StubChannel
from tests.integration.test_chat_flow import _drain_until_done
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)


class MultiMessageScreen(StubChannel):
    """Two message boundaries with a tool call in between — the shape a real
    session produces. The Stop text repeats the LAST message, exactly like a
    real one does."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "我先查一下代码，稍等")
        self.uses(topic_id, "Grep", pattern="TODO")
        self.says(topic_id, "查完了：一共 3 处 TODO")
        self.stops(topic_id, "查完了：一共 3 处 TODO")


@pytest.fixture
def stub_hooks() -> MultiMessageScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return MultiMessageScreen()


def _run_turn(client) -> tuple[str, list[dict]]:
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
    ).json()["data"]
    # 芝士 answers in a 支线, so that is where its messages land.
    t = {"id": in_thread(client, t["id"], "alice")}
    with room_socket(client, t["id"], "alice") as ws:
        post_message(client, t["id"], "alice", {"content": "@芝士 帮我看看"})
        frames = _drain_until_done(ws)
    return t["id"], frames


def test_result_text_is_not_duplicated_as_extra_block(client):
    topic_id, _frames = _run_turn(client)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    ai_messages = [
        b for b in blocks if b["author"].startswith("cheese") and b["kind"] == "message"
    ]
    assert ai_messages == []


def test_stop_only_text_stays_in_activity(client, stub_hooks):
    """A missing MessageDisplay never promotes Stop text into chat."""

    def _plain(topic_id, prompt, reply, agent=None):
        del reply
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.stops(topic_id, "Hello world")

    stub_hooks.emit_turn = _plain  # type: ignore[method-assign]
    topic_id, frames = _run_turn(client)
    assert not any(f["type"] == "assistant_block" for f in frames)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert not [b for b in blocks if b["content"] == "Hello world"]
    # Not lost: it is in the session's raw record, where activity reads it.
    records = client.get(f"/topics/{topic_id}/session-records").json()["data"]
    assert any("Hello world" in str(item["record"]) for item in records["items"])
