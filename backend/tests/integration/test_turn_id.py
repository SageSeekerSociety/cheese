"""R4: a turn's blocks share a turn_id (traceability / recovery foundation)."""


def _topic(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "T"}
    ).json()["data"]
    return t["id"]


def test_turn_blocks_share_one_turn_id(client):
    tid = _topic(client)
    with client.websocket_connect(f"/api/topics/{tid}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": "hi", "author": "user-1", "summon": True}
        )
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break

    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    turn_ids = {b["turn_id"] for b in blocks if b["turn_id"]}
    # The user message + the AI reply belong to the same turn.
    assert len(turn_ids) == 1
    kinds_with_turn = {b["kind"] for b in blocks if b["turn_id"]}
    assert "message" in kinds_with_turn
