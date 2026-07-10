"""R4: a turn's blocks share a turn_id (traceability / recovery foundation)."""

import uuid

from app.core.sandbox_auth import SANDBOX_TOKEN


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


def test_cheese_created_block_inherits_turn_from_header(client):
    # A cheese REST write (a decision) tags its block with the X-Cheese-Turn id
    # via the ambient contextvar — no per-handler plumbing (R4 cheese-side).
    tid = _topic(client)
    turn = str(uuid.uuid4())
    r = client.post(
        f"/api/topics/{tid}/decision",
        json={"decision": "Recall@10"},
        headers={"X-Cheese-Token": SANDBOX_TOKEN, "X-Cheese-Turn": turn},
    )
    assert r.status_code == 200
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    decision = next(b for b in blocks if b["kind"] == "decision")
    assert decision["turn_id"] == turn
