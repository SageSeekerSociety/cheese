"""进度层 (#187): 芝士's checklist outlives the turn, the machine and the session.

The agent writes it with the platform tool `todo_write`, which lands on
``PUT /topics/{id}/progress``. These tests pin what makes the layer real: the
room sees each write live, the write survives the turn, and the next turn is
actually told about it.
"""

import pytest

from app.api.deps import get_broker
from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    session_auth_headers,
)

PLAN = [
    {"content": "核实 issue 论断", "status": "completed"},
    {"content": "写实现", "status": "in_progress"},
    {"content": "补测试", "status": "pending"},
]


def _room(client) -> tuple[str, dict]:
    """A room, and the credentials its agent's session writes with."""
    p = post_project(client, json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    token = mint_scoped_token(project_id=p["id"], topic_id=t["id"])
    return t["id"], {"X-Cheese-Token": token}


def _write(client, topic_id: str, headers: dict, todos: list[dict], **extra):
    return client.put(
        f"/topics/{topic_id}/progress",
        json={"todos": todos, **extra},
        headers=headers,
    )


def _progress(client, topic_id: str, **params) -> list[tuple[str, str]]:
    data = client.get(f"/topics/{topic_id}/progress", params=params).json()["data"]
    return [(i["subject"], i["status"]) for i in data["items"]]


def _card(client, room_id: str) -> str:
    """One of the room's cards — a 分身 works it inside the room's session."""
    task = client.post(
        f"/topics/{room_id}/split", json={"title": "子活", "reviewer_handle": "alice"}
    ).json()["data"]
    wait_work_idle()
    return task["id"]


def _chat(client, topic_id: str) -> list[dict]:
    """Run one turn, returning every frame it produced."""
    frames: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 hi"})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                return frames


def test_topic_without_a_checklist_has_empty_progress(client):
    topic, _ = _room(client)
    body = client.get(f"/topics/{topic}/progress").json()
    assert body["code"] == 200
    assert body["data"] == {"items": [], "updated_at": None}


def test_a_write_reaches_the_room_live_and_is_stored(client):
    topic, headers = _room(client)
    with client.websocket_connect(chat_ws_url(topic, "user-1")) as ws:
        response = _write(client, topic, headers, PLAN)
        assert response.status_code == 200, response.text
        frame = ws.receive_json()
        while frame["type"] != "todo":
            frame = ws.receive_json()
    shown = [(i["subject"], i["status"]) for i in frame["items"]]
    assert shown == [(t["content"], t["status"]) for t in PLAN]
    assert not frame.get("restored"), "a write is this turn's list, not a leftover"
    assert _progress(client, topic) == shown
    data = client.get(f"/topics/{topic}/progress").json()["data"]
    # Stamped, so a reader can tell fresh progress from something ancient.
    assert data["updated_at"] is not None


def test_each_write_replaces_the_whole_list(client):
    topic, headers = _room(client)
    _write(client, topic, headers, PLAN)
    _write(client, topic, headers, [{"content": "只剩这一项", "status": "pending"}])
    assert _progress(client, topic) == [("只剩这一项", "pending")]


def test_next_turn_is_told_where_the_work_got_to(client, stub_hooks):
    topic, headers = _room(client)
    _chat(client, topic)
    assert "上次的任务清单" not in (stub_hooks.last_system_prompt or "")

    _write(client, topic, headers, PLAN)
    frames = _chat(client, topic)

    prompt = stub_hooks.last_system_prompt or ""
    # Status is carried as a mark, not just the text: "已完成" vs "在做" is the
    # whole reason to hand the list over rather than re-plan from scratch.
    assert "- [x] 核实 issue 论断" in prompt
    assert "- [~] 写实现" in prompt
    assert "- [ ] 补测试" in prompt

    # The room sees it too, before the turn produces anything of its own, and
    # marked as the last turn's rather than this one's.
    restored = [f for f in frames if f["type"] == "todo" and f.get("restored")]
    assert restored, "turn start must replay the stored checklist to the room"
    assert [i["subject"] for i in restored[0]["items"]] == [
        "核实 issue 论断",
        "写实现",
        "补测试",
    ]


def test_a_workers_checklist_stays_on_its_card(client, stub_hooks, monkeypatch):
    """A 分身 runs in the room's session, on the room's credentials. Its plan is
    its card's: the room's stored list, and what the room's next turn is handed
    back, stay the room's own."""
    topic, headers = _room(client)
    card = _card(client, topic)
    _write(client, topic, headers, PLAN)
    broker = get_broker()
    published: list[tuple[str, dict]] = []
    publish = broker.publish

    async def spy(channel, frame):
        published.append((channel, frame))
        await publish(channel, frame)

    monkeypatch.setattr(broker, "publish", spy)
    worker = [{"content": "改卡片上的接口", "status": "in_progress"}]
    response = _write(client, topic, headers, worker, task=card)
    assert response.status_code == 200, response.text

    assert _progress(client, topic, task=card) == [("改卡片上的接口", "in_progress")]
    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]
    todo_frames = [(c, f) for c, f in published if f["type"] == "todo"]
    assert [c for c, _ in todo_frames] == [card], "the card's list goes to the card"

    monkeypatch.setattr(broker, "publish", publish)
    frames = _chat(client, topic)
    prompt = stub_hooks.last_system_prompt or ""
    assert "- [~] 写实现" in prompt
    assert "改卡片上的接口" not in prompt
    restored = [f for f in frames if f["type"] == "todo" and f.get("restored")]
    assert [i["subject"] for i in restored[0]["items"]] == [t["content"] for t in PLAN]


def test_a_card_from_another_room_is_refused(client):
    topic, headers = _room(client)
    other, _ = _room(client)
    foreign = _card(client, other)
    assert _write(client, topic, headers, PLAN, task=foreign).status_code == 404
    assert _progress(client, topic) == []
    assert _progress(client, other, task=foreign) == []


@pytest.mark.parametrize(
    "todos",
    [
        [],
        [{"content": "x", "status": "done"}],
        [{"content": "   ", "status": "pending"}],
        [{"content": "长" * 201, "status": "pending"}],
        [{"content": f"第 {n} 步", "status": "pending"} for n in range(31)],
    ],
    ids=["empty", "unknown-status", "blank", "too-long", "too-many"],
)
def test_a_malformed_checklist_changes_nothing(client, todos):
    topic, headers = _room(client)
    _write(client, topic, headers, PLAN)
    assert _write(client, topic, headers, todos).status_code == 400
    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]


def test_only_the_rooms_agent_writes_the_checklist(client):
    topic, headers = _room(client)
    other, _ = _room(client)
    assert _write(client, topic, {}, PLAN).status_code in (401, 403)
    assert (
        _write(client, topic, session_auth_headers("user-1"), PLAN).status_code == 403
    )
    assert _write(client, other, headers, PLAN).status_code == 403
    assert _progress(client, topic) == []
    assert _progress(client, other) == []


def test_progress_is_per_topic(client):
    a, headers = _room(client)
    b, _ = _room(client)
    _write(client, a, headers, PLAN)
    assert _progress(client, b) == []


def test_progress_404s_for_an_unknown_topic(client):
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/topics/{unknown}/progress").status_code == 404
