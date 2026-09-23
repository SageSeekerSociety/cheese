"""cheese ask: option questions in the chat, one-click structured answers."""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import chat_ws_url, room_agent_seat


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "user-1"},
    ).json()["data"]
    return t["id"]


def _ask(client, tid: str) -> dict:
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_ask_creates_option_message(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    # Authored by THIS topic's 分身, not the shared platform ``cheese`` account.
    assert blk["author"] == room_agent_seat(client, tid)
    assert blk["kind"] == "message"
    assert blk["meta"]["options"] == ["cursor", "pageStart"]
    # It shows in the timeline like any message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert any(b["id"] == blk["id"] for b in blocks)


def test_the_question_it_asked_is_not_an_input_it_has_to_read(client):
    """芝士问出口的那道题落进房间，却不是一条交给它去读的输入。

    署名是房间里芝士那条 handle，轮次号这边填不出来：`cheese ask` 只在
    CHEESE_TURN 非空时才带 X-Cheese-Turn，而没有一处产品代码写那个环境变量（这
    份用例的 `_ask` 也一样不带）。按「署名是 agent 且落在某一轮里」去算，这道题
    就成了待读输入：「忘了 @」的补救按钮于是不再答「没有待读的东西」，白开一轮，
    而那一轮的 prompt 里躺着芝士刚问出口的这道题，它对着自己的问题再答一遍。
    """
    tid = _topic(client)
    _ask(client, tid)

    summoned = client.post(f"/topics/{tid}/summon", json={"author": "user-1"})
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己问出口的那道题不该把它自己叫起来"


def test_ask_rejects_bad_option_counts(client):
    tid = _topic(client)
    r = client.post(
        f"/topics/{tid}/ask", json={"question": "q", "options": ["only-one"]}
    )
    assert r.status_code == 422
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "q", "options": ["a", "b", "c", "d", "e"]},
    )
    assert r.status_code == 422


def test_ask_requires_a_valid_topic_scoped_credential(client):
    tid = _topic(client)
    body = {"question": "选哪个？", "options": ["a", "b"]}
    without_token = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={"X-Cheese-Token": ""},
    )
    assert without_token.status_code == 401

    other = _topic(client)
    wrong_topic = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(
                    client.get(f"/topics/{other}").json()["data"]["project_id"]
                ),
                topic_id=other,
            )
        },
    )
    assert wrong_topic.status_code == 403


def test_answer_records_choice_and_posts_reply(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["meta"]["answered"] == "cursor"
    assert data["meta"]["answered_by"] == "user-1"

    # The choice lands as the answerer's own message and summons 芝士. Drive
    # the submitted turn to completion the repo way: hold the WS open (replay
    # catches frames already published) until done/error.
    with client.websocket_connect(chat_ws_url(tid, "user-1")) as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    debug = [(b["author"], b["kind"], b["content"][:30]) for b in blocks]
    # 选项落成回答者自己的一条消息，并且在正文里点了问问题的那个席位的名 —— 召唤
    # 写在正文里，时间线上这条消息因此自己说明了它叫的是谁。
    seat = room_agent_seat(client, tid)
    assert any(
        b["author"] == "user-1" and b["content"] == f"<@{seat}> cursor" for b in blocks
    ), f"choice message never landed: {debug}"


def test_answer_validates_option_and_single_shot(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "不存在的", "author": "user-1"},
    )
    assert r.status_code == 422

    client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "pageStart", "author": "user-2"},
    )
    assert r.status_code == 422
