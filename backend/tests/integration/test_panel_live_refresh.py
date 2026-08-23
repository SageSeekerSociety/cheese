"""面板必须跟着变 (§3.1.1): a `cheese` command that changes platform state has to
say so WHILE the turn runs, not only in the record it leaves behind.

The action block 芝士 files at the end of a turn is history — it says what was
done. It is not what makes the accept box appear; that is a separate frame the
room listens for. Without it, a card filed while somebody is reading the
conversation simply does not show up until they switch topics or reload, which
is exactly what people reported.
"""

import uuid

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.main import app
from tests.conftest import StubChannel
from tests.integration.conftest import chat_ws_url


class _RunsCheese(StubChannel):
    """A turn that runs one Bash command and stops."""

    command = 'cheese accept-request lisi "最懂这块"'

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del prompt
        self.starts(topic_id)
        self.uses(topic_id, "Bash", eid="e-bash", command=self.command)
        self.stops(topic_id, reply)


def _turn_frames(client, tmp_path, channel: StubChannel) -> list[dict]:
    """One summoned turn on `channel`, as the room sees it."""
    service = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([channel.runtime], channel.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: service

    pr = client.post("/projects", json={"name": "Demo"})
    project_id = pr.json()["data"]["id"]
    tr = client.post(
        "/topics",
        json={"project_id": project_id, "title": "讨论", "created_by": "user-1"},
    )
    topic_id = tr.json()["data"]["id"]

    frames: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "递一张卡", "summon": True})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    return frames


def _refreshed(frames: list[dict]) -> list[str]:
    return [f["resource"] for f in frames if f.get("type") == "state"]


def test_filing_a_card_tells_the_room_the_panel_is_stale(client, tmp_path):
    frames = _turn_frames(client, tmp_path, _RunsCheese())
    assert _refreshed(frames) == ["accept"]


def test_editing_the_doc_refreshes_the_doc_panel(client, tmp_path):
    class _WritesDoc(_RunsCheese):
        command = "cheese doc set docs/topics/x.md"

    frames = _turn_frames(client, tmp_path, _WritesDoc())
    assert _refreshed(frames) == ["doc"]


def test_an_ordinary_command_refreshes_nothing(client, tmp_path):
    """Only a platform-mutating `cheese` subcommand. A shell command that
    happens to mention the word must not make every panel re-fetch."""

    class _JustRuns(_RunsCheese):
        command = "grep -rn cheese backend/"

    frames = _turn_frames(client, tmp_path, _JustRuns())
    # The turn still ran — otherwise this asserts nothing at all.
    assert any(f["type"] == "event_block" for f in frames)
    assert _refreshed(frames) == []
