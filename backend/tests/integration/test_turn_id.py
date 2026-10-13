"""R4: a turn's blocks share a turn_id (traceability / recovery foundation)."""

import uuid

from app.core.sandbox_auth import SANDBOX_TOKEN
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
    ).json()["data"]
    return t["id"]


def test_turn_blocks_share_one_turn_id(client):
    # 芝士 answers in a 支线, so that is where the turn's blocks are.
    tid = in_thread(client, _topic(client), "user-1")
    with room_socket(client, tid, "user-1") as ws:
        post_message(client, tid, "user-1", {"content": "@芝士 hi"})
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    turn_ids = {b["turn_id"] for b in blocks if b["turn_id"]}
    # The user message + the AI reply belong to the same turn.
    assert len(turn_ids) == 1
    kinds_with_turn = {b["kind"] for b in blocks if b["turn_id"]}
    assert "message" in kinds_with_turn


def test_cheese_created_block_inherits_turn_from_header(client):
    # A cheese REST write (a weekly report) tags its block with the X-Cheese-Turn id
    # via the ambient contextvar — no per-handler plumbing (R4 cheese-side).
    tid = _topic(client)
    turn = str(uuid.uuid4())
    r = client.post(
        f"/topics/{tid}/weekly",
        json={"body": "Recall@10"},
        headers={"X-Cheese-Token": SANDBOX_TOKEN, "X-Cheese-Turn": turn},
    )
    assert r.status_code == 200
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    weekly = next(b for b in blocks if b["kind"] == "weekly")
    assert weekly["turn_id"] == turn
