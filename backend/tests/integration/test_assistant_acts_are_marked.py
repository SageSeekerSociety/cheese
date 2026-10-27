"""What a person's own 芝士 does for them through its gate is theirs, with
their authority, and marked as done by it on their behalf wherever the doing
is recorded: a message, a task, a review decision, an invitation, a decision
request answered (docs/agent-principles.md 一、四). What the person does
themselves carries no such mark."""

import asyncio
import uuid

from app.domain.assistant import gate
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from tests.conftest import seed_user
from tests.delivery import delivery_task_id
from tests.integration.conftest import open_task, session_auth_headers
from tests.integration.test_accept_pr import _ready_card, _task_settled
from tests.integration.test_accept_pr import app_world as app_world
from tests.integration.test_assistant_agent import (
    _acting,
    _as,
    _asked_in,
    _auth_headers,
    _Gatekeeper,
    _names,
)
from tests.integration.test_message_edit import _room


def _do(client, monkeypatch, token: str, act: dict) -> list[dict]:
    """Have the gate let ``act`` through, make its calls the way 芝士's tool
    does, and return what each answered."""

    async def looks(session):
        return _Gatekeeper("放", "放")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    r = client.post("/assistant/agent/act", json=act, headers=_as(token))
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["done"] is True, got
    answers: list[dict] = []
    made = ""
    for call in got["calls"]:
        sent = client.request(
            call["method"],
            call["path"].replace("{prev}", made),
            json=call.get("body"),
            headers=_as(got["pass"]),
        )
        assert sent.status_code == 200, sent.text
        answer = sent.json()["data"]
        made = str(answer.get("id") or "")
        answers.append(answer)
    return answers


def _blocks(client, topic: str, handle: str = "alice") -> list[dict]:
    r = client.get(f"/topics/{topic}/blocks", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_a_message_its_cheese_sends_is_marked_and_her_own_is_not(client, monkeypatch):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "跟大家说改到三点"))

    [sent] = _do(
        client,
        monkeypatch,
        token,
        {"kind": "message", "where": f"{slug}/channels/{number}", "text": "改到三点"},
    )
    own = client.post(
        f"/topics/{room}/messages",
        json={"content": "我自己说一句", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert own.status_code == 200, own.text

    assert sent["author"] == "alice"
    assert sent["meta"]["by_assistant"] is True
    assert "by_assistant" not in own.json()["data"]["meta"]
    # Read back as bob reads the room, not only as the answer to the send.
    stored = {b["content"]: b for b in _blocks(client, room, "bob")}
    assert stored["改到三点"]["meta"]["by_assistant"] is True
    assert "by_assistant" not in stored["我自己说一句"]["meta"]


def test_a_task_its_cheese_opens_says_so_where_it_is_announced(client, monkeypatch):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "开个任务整理表格"))

    task, brief = _do(
        client,
        monkeypatch,
        token,
        {
            "kind": "task",
            "where": f"{slug}/channels/{number}",
            "title": "整理表格",
            "text": "把相关工作整理成表",
        },
    )
    mine = client.post(
        f"/topics/{room}/tasks",
        json={"title": "我自己开的"},
        headers=session_auth_headers("alice"),
    )
    assert mine.status_code == 200, mine.text

    assert task["created_by"] == "alice"
    assert task["created_by_assistant"] is True
    assert mine.json()["data"]["created_by_assistant"] is False
    # Its brief, the first message in the task, is its 芝士's too.
    assert brief["meta"]["by_assistant"] is True
    announced = {
        b["meta"]["task_id"]: b["content"]
        for b in _blocks(client, room, "bob")
        if (b.get("meta") or {}).get("action") == "task_created"
    }
    assert announced[task["id"]].startswith("芝士代 <@alice> 创建了任务")
    assert announced[mine.json()["data"]["id"]].startswith("<@alice> 创建了任务")


def _link_card(client, room: str, task: str) -> uuid.UUID:
    """A card in ``task`` handing alice a link to review."""

    async def file_card():
        async with client.test_factory() as db:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task),
                reviewer_handle="alice",
                status=AcceptStatus.pending,
                change_subject="相关工作对比表",
                deliverable_kind=DeliverableKind.link,
                deliverable_url="https://example.edu/table",
            )
            db.add(card)
            await db.commit()
            return card.id

    return asyncio.run(file_card())


def test_a_review_its_cheese_decides_is_marked_on_the_card_and_the_line(
    client, monkeypatch, stub_project_forge
):
    room, _ = _room(client)
    slug, _ = _names(client, room)
    returned = open_task(client, room, "整理表格")
    accepted = open_task(client, room, "整理图表")
    _link_card(client, room, returned["id"])
    _link_card(client, room, accepted["id"])
    token = _acting(client, "alice", _asked_in(client, "alice", "两张卡你看着办"))

    [card] = _do(
        client,
        monkeypatch,
        token,
        {
            "kind": "review",
            "ref": f"{slug}/tasks/{returned['number']}",
            "choice": "reject",
            "text": "缺两篇",
        },
    )
    [taken] = _do(
        client,
        monkeypatch,
        token,
        {
            "kind": "review",
            "ref": f"{slug}/tasks/{accepted['number']}",
            "choice": "accept",
        },
    )

    assert (card["decided_by"], card["decided_by_assistant"]) == ("alice", True)
    assert (taken["decided_by"], taken["decided_by_assistant"]) == ("alice", True)
    said = [b["content"] for b in _blocks(client, returned["id"], "bob")]
    assert any(line.startswith("芝士代 <@alice> 退回了改动") for line in said), said


def test_a_review_she_decides_herself_is_not_marked(
    client, monkeypatch, stub_project_forge
):
    room, _ = _room(client)
    task = open_task(client, room, "整理表格")
    card = _link_card(client, room, task["id"])

    r = client.post(
        f"/accept-cards/{card}/reject",
        json={"decided_by": "alice", "note": "缺两篇"},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["decided_by_assistant"] is False
    said = [b["content"] for b in _blocks(client, task["id"], "bob")]
    assert any(line.startswith("<@alice> 退回了改动") for line in said), said


def test_an_invitation_its_cheese_sends_is_marked_for_whom_it_invites(
    client, monkeypatch
):
    room, project = _room(client)
    slug, _ = _names(client, room)
    seed_user(client, "carol")
    seed_user(client, "dave")
    token = _acting(client, "alice", _asked_in(client, "alice", "邀请 carol 进项目"))

    [invited] = _do(
        client,
        monkeypatch,
        token,
        {"kind": "member", "where": slug, "who": "carol"},
    )
    own = client.post(
        f"/projects/{project}/invitations",
        json={"user_handle": "dave"},
        headers=session_auth_headers("alice"),
    )
    assert own.status_code == 200, own.text

    assert (invited["inviter_handle"], invited["invited_by_assistant"]) == (
        "alice",
        True,
    )
    assert own.json()["data"]["invited_by_assistant"] is False
    # What carol and dave each see waiting on them.
    for handle, marked in (("carol", True), ("dave", False)):
        asks = client.get("/awaiting-me", headers=_auth_headers(client, handle))
        assert asks.status_code == 200, asks.text
        [ask] = [a for a in asks.json()["data"]["data"] if a["placeId"] == project]
        assert ask["byAssistant"] is marked


def test_a_decision_its_cheese_answers_is_marked_in_the_room(client, monkeypatch):
    room, project = _room(client)
    asked = client.post(
        f"/projects/{project}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "评测集怎么切分",
            "target_handle": "alice",
            "topic_id": room,
            "payload": {"options": ["按时间切分", "随机切分"]},
        },
    )
    assert asked.status_code == 200, asked.text
    [alert] = asked.json()["data"]["data"]
    token = _acting(client, "alice", _asked_in(client, "alice", "按时间切分吧"))

    _do(
        client,
        monkeypatch,
        token,
        {"kind": "respond", "ref": f"alerts/{alert['id']}", "choice": "按时间切分"},
    )

    [line] = [b for b in _blocks(client, room, "bob") if "【决策】" in b["content"]]
    assert line["author"] == "alice"
    assert line["meta"]["by_assistant"] is True


def test_a_pr_its_cheese_accepts_is_announced_as_accepted_for_her(
    client, monkeypatch, app_world
):
    """The line saying the change is in names whose hand merged it, whenever
    it is said: on the spot here, or once a merge queue lets it in."""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")
    fake.merge_sha_by_number[number] = "merge-sha-1"
    task = str(delivery_task_id(client, tid))
    slug, _ = _names(client, tid)
    task_number = client.get(f"/topics/{task}/task").json()["data"]["number"]
    token = _acting(client, "alice", _asked_in(client, "alice", "那个 PR 合了吧"))

    [card] = _do(
        client,
        monkeypatch,
        token,
        {"kind": "review", "ref": f"{slug}/tasks/{task_number}", "choice": "accept"},
    )

    assert card["status"] == "accepted"
    assert "芝士代 <@alice> 采纳了这次改动" in _task_settled(
        client, tid, "采纳了这次改动"
    )
