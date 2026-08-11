"""施工现场 transcript + 资源用量 (spec §7.1/§9.1)."""

from tests.integration.conftest import chat_ws_url


def _topic(client) -> tuple[str, str]:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    return p["id"], t["id"]


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_transcript_and_usage_after_chat(client):
    pid, tid = _topic(client)
    _chat(client, tid)

    # 施工现场 = 工作细节 only: chat messages (human or AI) never mirror into
    # the transcript — they live in the conversation pane.
    tr = client.get(f"/api/topics/{tid}/transcript").json()["data"]["data"]
    assert all(b["kind"] == "event" for b in tr)
    assert not any(b["kind"] == "message" for b in tr)

    # 资源用量: token/cost recorded for the topic and rolled up to the project.
    tu = client.get(f"/api/topics/{tid}/usage").json()["data"]
    assert tu["total_tokens"] == 15  # 10 + 5 from the stub
    assert tu["turns"] == 1
    pu = client.get(f"/api/projects/{pid}/usage").json()["data"]
    assert pu["total_tokens"] == 15
