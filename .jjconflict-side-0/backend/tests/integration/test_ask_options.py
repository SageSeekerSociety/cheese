"""cheese ask: option questions in the chat, one-click structured answers."""


def _topic(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post("/api/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"]


def _ask(client, tid: str) -> dict:
    r = client.post(
        f"/api/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_ask_creates_option_message(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    assert blk["author"] == "cheese"
    assert blk["kind"] == "message"
    assert blk["meta"]["options"] == ["cursor", "pageStart"]
    # It shows in the timeline like any message.
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    assert any(b["id"] == blk["id"] for b in blocks)


def test_ask_rejects_bad_option_counts(client):
    tid = _topic(client)
    r = client.post(
        f"/api/topics/{tid}/ask", json={"question": "q", "options": ["only-one"]}
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/topics/{tid}/ask",
        json={"question": "q", "options": ["a", "b", "c", "d", "e"]},
    )
    assert r.status_code == 422


def test_answer_records_choice_and_posts_reply(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/api/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["meta"]["answered"] == "cursor"
    assert data["meta"]["answered_by"] == "user-1"

    # The choice lands as the answerer's own message and summons 芝士. Drive
    # the submitted turn to completion the repo way: hold the WS open (replay
    # catches frames already published) until done/error.
    with client.websocket_connect(f"/api/topics/{tid}/chat") as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    debug = [(b["author"], b["kind"], b["content"][:30]) for b in blocks]
    assert any(b["author"] == "user-1" and b["content"] == "cursor" for b in blocks), (
        f"choice message never landed: {debug}"
    )


def test_answer_validates_option_and_single_shot(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/api/topics/blocks/{blk['id']}/answer",
        json={"option": "不存在的", "author": "user-1"},
    )
    assert r.status_code == 422

    client.post(
        f"/api/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    r = client.post(
        f"/api/topics/blocks/{blk['id']}/answer",
        json={"option": "pageStart", "author": "user-2"},
    )
    assert r.status_code == 422
