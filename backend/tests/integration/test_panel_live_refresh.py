"""面板必须跟着变 (§3.1.1): a change to a platform resource tells the room its
panel is stale WHILE the turn runs, not only in the record it leaves behind.

The frame is sent by the API handler that made the change. That handler is the
one place that knows the change happened: an agent may reach it through a
`cheese` subcommand, `cheese api`, or any other client, and a subcommand can
fail. What the agent's shell command says is not evidence of either, so a Bash
line that merely mentions `cheese doc set` refreshes nothing.
"""

import uuid

import pytest

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.runtime import get_broker
from app.main import app
from tests.conftest import StubChannel
from tests.delivery import delivery_task_id
from tests.integration.conftest import chat_ws_url
from tests.integration.test_accept_pr import app_world as app_world


@pytest.fixture
def frames(monkeypatch) -> list[tuple[str, dict]]:
    """Every frame published to any channel, in order."""
    seen: list[tuple[str, dict]] = []
    broker = get_broker()
    original = broker.publish

    async def publish(channel, frame):
        seen.append((channel, frame))
        await original(channel, frame)

    monkeypatch.setattr(broker, "publish", publish)
    return seen


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics", json={"project_id": pid, "title": "房间", "created_by": "alice"}
    ).json()["data"]["id"]
    return pid, rid


def _agent(pid: str, rid: str) -> dict[str, str]:
    """The credential a turn is minted: what `cheese` and `cheese api` both send."""
    return {"X-Cheese-Token": mint_scoped_token(project_id=pid, topic_id=rid)}


def _stale(frames: list[tuple[str, dict]], room: str) -> list[str]:
    return [
        frame["resource"]
        for channel, frame in frames
        if channel == room and frame.get("type") == "state"
    ]


def test_writing_the_doc_refreshes_the_doc_panel(client, frames):
    pid, rid = _room(client)
    r = client.put(
        f"/topics/{rid}/doc",
        json={"content": "调查安排", "expected_version": 0},
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["doc"]


def test_recording_a_decision_refreshes_the_room(client, frames):
    pid, rid = _room(client)
    r = client.post(
        f"/topics/{rid}/decision",
        json={"decision": "用 A 方案"},
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["decision"]


def test_opening_a_piece_of_work_refreshes_the_rooms_work_list(client, frames):
    pid, rid = _room(client)
    r = client.post(
        f"/topics/{rid}/split",
        json={"title": "一件活", "reviewer_handle": "alice"},
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["topics"]


def test_a_notification_about_a_room_refreshes_that_room(client, frames):
    pid, rid = _room(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "title": "看下",
            "kind": "change_alert",
            "level": "light",
            "target_handle": "alice",
            "topic_id": rid,
        },
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["notify"]


def test_a_milestone_from_a_room_refreshes_that_room(client, frames):
    # A person's call: a room-scoped agent token is refused on this
    # project-level route before anything is written.
    pid, rid = _room(client)
    r = client.post(
        f"/projects/{pid}/milestones", json={"title": "交初稿", "source_topic_id": rid}
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["milestone"]


@pytest.mark.usefixtures("app_world")
def test_filing_and_correcting_a_card_refreshes_the_accept_panel(client, frames):
    pid, rid = _room(client)
    task = delivery_task_id(client, rid)
    filed = client.post(
        f"/topics/{rid}/tasks/{task}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=_agent(pid, rid),
    )
    assert filed.status_code == 200, filed.text
    assert _stale(frames, rid) == ["accept"]
    frames.clear()
    corrected = client.post(
        f"/topics/{rid}/tasks/{task}/accept-card/describe",
        json={"change_subject": "chore(test): say what the card is for"},
        headers=_agent(pid, rid),
    )
    assert corrected.status_code == 200, corrected.text
    assert _stale(frames, rid) == ["accept"]


class _RunsCheese(StubChannel):
    """A turn that runs one Bash command and stops. The command never reaches
    the platform: this stub runs nothing, which is the point."""

    command = "cheese doc set docs/topics/x.md"

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del prompt
        self.starts(topic_id)
        self.uses(topic_id, "Bash", eid="e-bash", command=self.command)
        self.stops(topic_id, reply)


def _turn_frames(client, tmp_path, channel: StubChannel) -> list[dict]:
    """One summoned turn on `channel`, as the room sees it."""
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([channel.runtime], channel.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    _, topic_id = _room(client)
    seen: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 改一下文档"})
        while True:
            frame = ws.receive_json()
            seen.append(frame)
            if frame["type"] in ("done", "error"):
                break
    return seen


@pytest.mark.parametrize(
    "command",
    [
        "cheese doc set docs/topics/x.md",
        'cheese accept-request lisi "最懂这块"',
        'cheese decision "用 A 方案"',
    ],
)
def test_a_command_that_only_names_a_change_refreshes_nothing(
    client, tmp_path, command
):
    class _Runs(_RunsCheese):
        pass

    _Runs.command = command
    seen = _turn_frames(client, tmp_path, _Runs())
    # The turn ran and its Bash step reached the room — otherwise this asserts
    # nothing at all.
    assert any(
        f["type"] == "event_block" and "cheese" in (f["block"].get("content") or "")
        for f in seen
    )
    assert [f for f in seen if f.get("type") == "state"] == []


def test_the_turn_still_files_its_action_card(client, tmp_path):
    """The action card at the end of a turn is a separate record and stays."""

    class _Decides(_RunsCheese):
        command = 'cheese decision "用 A 方案"'

    seen = _turn_frames(client, tmp_path, _Decides())
    assert any(
        f["type"] == "event_block" and f["block"].get("content") == "芝士 记录了决策"
        for f in seen
    )
