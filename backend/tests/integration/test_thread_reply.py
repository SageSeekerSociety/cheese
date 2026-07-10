"""B3: replying to a message threads the reply under it (reply_to)."""


def _topic(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "T"}
    ).json()["data"]
    return t["id"]


def _post(ws, content, reply_to=None):
    ws.send_json(
        {"type": "message", "content": content, "author": "u", "summon": False,
         "reply_to": reply_to}
    )
    # human-only post → user_block then done
    block = None
    while True:
        f = ws.receive_json()
        if f["type"] == "user_block":
            block = f["block"]
        if f["type"] in ("done", "error"):
            break
    return block


def test_reply_threads_under_parent(client):
    tid = _topic(client)
    with client.websocket_connect(f"/api/topics/{tid}/chat") as ws:
        parent = _post(ws, "根消息")
        child = _post(ws, "这是回复", reply_to=parent["id"])
    assert parent["reply_to"] is None
    assert child["reply_to"] == parent["id"]
