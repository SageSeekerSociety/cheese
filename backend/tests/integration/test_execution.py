"""施工现场 transcript + 资源用量 (spec §7.1/§9.1)."""


def _topic(client) -> tuple[str, str]:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "话题"}
    ).json()["data"]
    return p["id"], t["id"]


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": "hi", "author": "user-1", "summon": True}
        )
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_transcript_and_usage_after_chat(client):
    pid, tid = _topic(client)
    _chat(client, tid)

    # 施工现场: AI messages show up in the transcript.
    tr = client.get(f"/api/topics/{tid}/transcript").json()["data"]["data"]
    assert any(b["author_type"] == "ai" for b in tr)

    # 资源用量: token/cost recorded for the topic and rolled up to the project.
    tu = client.get(f"/api/topics/{tid}/usage").json()["data"]
    assert tu["total_tokens"] == 15  # 10 + 5 from the stub
    assert tu["turns"] == 1
    pu = client.get(f"/api/projects/{pid}/usage").json()["data"]
    assert pu["total_tokens"] == 15
