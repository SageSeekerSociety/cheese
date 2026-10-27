"""What a person's 芝士 reads with (`routes/assistant_agent.py`): things by the
words of their address, as the person, in text."""

import asyncio
import re
import uuid

from sqlalchemy import select

from app.api.routes import assistant_agent
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.assistant import gate, proposals
from app.domain.assistant.models import (
    AssistantChange,
    AssistantConversation,
    AssistantGateCheck,
    AssistantMessage,
)
from app.domain.gateway_chat import Completion, Usage
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from app.domain.topic.models import Topic
from app.domain.user.repositories import UserProfileRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    add_external_member,
    new_project,
    open_task,
    session_auth_headers,
)
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_message_edit import _room, _say


def _auth_headers(client, handle: str) -> dict:
    """The person themselves, as their own pages call."""
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _as(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _personal(client, handle: str, project: str | None = None) -> str:
    seed_user(client, handle)
    return mint_delegated_credential(
        user_id=_ledger_user(client, handle)[0],
        handle=handle,
        work=str(uuid.uuid4()),
        project_id=project,
        ttl_s=300,
    )


def _names(client, room: str) -> tuple[str, int]:
    """The room's project's slug and the room's number."""

    async def read():
        async with client.test_factory() as db:
            topic = await db.get(Topic, uuid.UUID(room))
            slug = await db.scalar(
                select(Project.slug).where(Project.id == topic.project_id)
            )
            return slug, topic.number

    return asyncio.run(read())


def _read(client, token: str, ref: str, page: int = 1):
    return client.post(
        "/assistant/agent/read", json={"ref": ref, "page": page}, headers=_as(token)
    )


def test_a_channel_reads_by_its_address_with_its_latest_messages(client):
    room, _ = _room(client)
    _say(client, room, "alice", "周五的组会改到下午三点")
    slug, number = _names(client, room)
    token = _personal(client, "alice")

    r = _read(client, token, f"{slug}/channels/{number}")

    assert r.status_code == 200, r.text
    text = r.json()["data"]["text"]
    assert f"频道「话题」（{slug}/channels/{number}）" in text
    assert "alice：周五的组会改到下午三点" in text
    # A link copied from the address bar reads the same.
    copied = _read(client, token, f"https://x.test/projects/{slug}/channels/{number}")
    assert copied.json()["data"]["text"] == text


def test_a_project_lists_its_channels_by_the_refs_that_read_them(client):
    room, _ = _room(client)
    slug, number = _names(client, room)

    r = _read(client, _personal(client, "alice"), slug)

    assert r.status_code == 200, r.text
    assert f"「话题」（{slug}/channels/{number}）" in r.json()["data"]["text"]


def test_what_the_person_cannot_open_cannot_be_read(client):
    room, _ = _room(client)
    slug, number = _names(client, room)
    outsider = _personal(client, "carol")

    assert _read(client, outsider, slug).status_code in (403, 404)
    assert _read(client, outsider, f"{slug}/channels/{number}").status_code in (
        403,
        404,
    )


def test_a_ref_it_cannot_read_says_how_refs_are_written(client):
    r = _read(client, _personal(client, "alice"), "频道 3")
    assert r.status_code == 400
    assert "ml-course/channels/3" in r.text


def test_the_inbox_says_when_nothing_waits(client):
    r = client.post(
        "/assistant/agent/inbox", json={}, headers=_as(_personal(client, "dora"))
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == "现在没有等你处理的事。"


def test_a_search_finds_across_the_persons_projects_with_refs(client):
    room, _ = _room(client)
    _say(client, room, "alice", "部署要改成 8443 端口")
    slug, number = _names(client, room)

    r = client.post(
        "/assistant/agent/search",
        json={"query": "8443"},
        headers=_as(_personal(client, "alice")),
    )

    assert r.status_code == 200, r.text
    text = r.json()["data"]["text"]
    assert f"项目「P」（{slug}）" in text
    assert f"在频道「话题」（{slug}/channels/{number}）" in text
    # Someone not in the project finds nothing of it.
    other = client.post(
        "/assistant/agent/search",
        json={"query": "8443"},
        headers=_as(_personal(client, "carol")),
    )
    assert "没有搜到" in other.json()["data"]["text"]


class _Gatekeeper:
    """The gate's model: answers each look from ``says`` in turn and keeps
    what it was shown."""

    def __init__(self, *says: str) -> None:
        self.says = list(says)
        self.shown: list[str] = []

    async def complete(self, *, system, prompt, timeout, max_tokens=None, extra=None):
        self.shown.append(prompt)
        return Completion(self.says.pop(0), Usage(100, 1, 80, 0), 0.0001)


def _asked_in(client, handle: str, said: str) -> str:
    """A conversation of ``handle``'s with their 芝士, where they said ``said``."""

    seed_user(client, handle)
    user_id = _ledger_user(client, handle)[0]

    async def make():
        async with client.test_factory() as db:
            conversation = AssistantConversation(
                user_id=user_id, place_kind="none", place_id="", title=said[:20]
            )
            db.add(conversation)
            await db.flush()
            db.add(
                AssistantMessage(
                    conversation_id=conversation.id, seq=1, role="user", text=said
                )
            )
            await db.commit()
            return str(conversation.id)

    return asyncio.run(make())


def _acting(client, handle: str, conversation: str) -> str:
    seed_user(client, handle)
    return mint_delegated_credential(
        user_id=_ledger_user(client, handle)[0],
        handle=handle,
        work=str(uuid.uuid4()),
        ttl_s=300,
        read_only=False,
        conversation=conversation,
    )


def _checks(client, handle: str) -> list:
    user_id = _ledger_user(client, handle)[0]

    async def read():
        async with client.test_factory() as db:
            return list(
                await db.scalars(
                    select(AssistantGateCheck)
                    .where(AssistantGateCheck.user_id == user_id)
                    .order_by(AssistantGateCheck.id)
                )
            )

    return asyncio.run(read())


def test_a_message_the_person_asked_for_is_sent_as_them_through_the_gate(
    client, monkeypatch
):
    room, _ = _room(client)
    slug, number = _names(client, room)
    conversation = _asked_in(client, "alice", "跟大家说周五组会改到下午三点")
    token = _acting(client, "alice", conversation)
    keeper = _Gatekeeper("放")

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)

    r = client.post(
        "/assistant/agent/act",
        json={
            "kind": "message",
            "where": f"{slug}/channels/{number}",
            "text": "周五组会改到下午三点",
        },
        headers=_as(token),
    )

    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["done"] is True
    # The gate read what she said, and the action; nothing 芝士 said.
    assert "[用户] 跟大家说周五组会改到下午三点" in keeper.shown[0]
    assert "周五组会改到下午三点" in keeper.shown[0].split("<要判断的操作>")[1]
    # The call it hands back is the one the channel page makes, as her.
    [call] = got["calls"]
    sent = client.post(
        call["path"],
        json=call["body"],
        headers=_as(got["pass"]),
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["author"] == "alice"
    # Once: the pass is spent.
    again = client.post(
        call["path"],
        json={**call["body"], "request_id": str(uuid.uuid4())},
        headers=_as(got["pass"]),
    )
    assert again.status_code == 403
    [check] = _checks(client, "alice")
    assert (check.verdict, check.stage, check.kind) == ("allowed", "quick", "message")
    assert check.cache_read_tokens == 80


def test_a_card_delivering_a_link_is_accepted_as_the_version_cheese_read(
    client, monkeypatch, stub_project_forge
):
    """A card that hands over a file or a link is accepted only for the version
    its reviewer saw. 芝士's call carries the one it read, as the card page's
    does, so the card is accepted rather than refused as out of date."""
    room, _ = _room(client)
    slug, _ = _names(client, room)
    task = open_task(client, room, "整理相关工作表格")

    async def file_card():
        async with client.test_factory() as db:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task["id"]),
                reviewer_handle="alice",
                status=AcceptStatus.pending,
                change_subject="相关工作对比表",
                deliverable_kind=DeliverableKind.link,
                deliverable_url="https://example.edu/table",
            )
            db.add(card)
            await db.commit()
            return card.id

    card_id = asyncio.run(file_card())
    conversation = _asked_in(client, "alice", "整理相关工作表格那张卡采纳了")
    token = _acting(client, "alice", conversation)

    async def looks(session):
        return _Gatekeeper("放")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    r = client.post(
        "/assistant/agent/act",
        json={
            "kind": "review",
            "ref": f"{slug}/tasks/{task['number']}",
            "choice": "accept",
        },
        headers=_as(token),
    )
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    [call] = got["calls"]
    accepted = client.post(
        call["path"],
        json=call["body"],
        headers=_as(got["pass"]),
    )

    assert accepted.status_code == 200, accepted.text

    async def status():
        async with client.test_factory() as db:
            return (await db.get(AcceptCard, card_id)).status

    assert asyncio.run(status()) == AcceptStatus.accepted


def test_without_the_gate_the_route_refuses_the_persons_cheese(client):
    room, _ = _room(client)
    token = _acting(client, "alice", _asked_in(client, "alice", "你好"))
    r = client.post(
        f"/topics/{room}/messages",
        json={"content": "我自己发", "request_id": str(uuid.uuid4())},
        headers=_as(token),
    )
    assert r.status_code == 403


def test_a_message_the_person_did_not_ask_for_goes_back_to_cheese_first(
    client, monkeypatch
):
    """A stopped message is handed back to 芝士 with the reason, not shown to
    the person: 芝士 may put it right or ask. Asked again with ``confirm``, it
    becomes a card."""
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "帮我看看综合频道"))
    stopped = "你只让芝士查看频道，没有提到发消息。\n结论：拦"

    async def looks(session):
        return _Gatekeeper("拦", stopped)

    monkeypatch.setattr(gate, "gatekeeper", looks)

    def act(**more) -> dict:
        r = client.post(
            "/assistant/agent/act",
            json={
                "kind": "message",
                "where": f"{slug}/channels/{number}",
                "text": "收到",
                **more,
            },
            headers=_as(token),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    back = act()
    assert back["done"] is False and back["returned"] is True
    assert "card" not in back and "pass" not in back
    assert "你只让芝士查看频道，没有提到发消息。" in back["said"]

    got = act(confirm=True)
    assert got["done"] is False and "pass" not in got
    assert got["card"]["kind"] == "message" and got["card"]["text"] == "收到"
    assert got["reason"] == "你只让芝士查看频道，没有提到发消息。"
    returned, asked = _checks(client, "alice")
    assert (returned.verdict, asked.verdict) == ("returned", "asked")
    assert asked.stage == "reasoned"


def test_after_three_returns_in_an_answer_a_stopped_action_is_a_card(
    client, monkeypatch
):
    """芝士 cannot keep trying: the fourth stopped action in one answer goes to
    the person whether 芝士 asked for that or not."""
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "帮我看看综合频道"))

    async def looks(session):
        return _Gatekeeper("拦", "你没有提到发消息。\n结论：拦")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    got = [
        client.post(
            "/assistant/agent/act",
            json={"kind": "message", "where": f"{slug}/channels/{number}", "text": t},
            headers=_as(token),
        ).json()["data"]
        for t in ("收到", "好的", "明白", "了解")
    ]
    assert [bool(g.get("returned")) for g in got] == [True, True, True, False]
    assert got[3]["card"]["text"] == "了解"


def test_cheese_waits_for_the_person_to_decide_on_its_card(client, monkeypatch):
    """A card 芝士 put up is waited on: how the person decided is what its
    tool hears, and an undecided card is said to be still waiting."""
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "帮我看看综合频道"))

    async def looks(session):
        return _Gatekeeper("拦", "你没有提到发消息。\n结论：拦")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    monkeypatch.setattr(assistant_agent, "WAIT_S", 0.2)
    monkeypatch.setattr(assistant_agent, "LOOK_EVERY_S", 0.05)

    def card(text: str) -> int:
        r = client.post(
            "/assistant/agent/act",
            json={
                "kind": "message",
                "where": f"{slug}/channels/{number}",
                "text": text,
                "confirm": True,
            },
            headers=_as(token),
        )
        return r.json()["data"]["card"]["id"]

    def wait(card_id: int) -> dict:
        r = client.post(
            "/assistant/agent/wait", json={"card": card_id}, headers=_as(token)
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    waiting, declined = card("收到"), card("好的")
    assert wait(waiting) == {"state": "pending"}
    me = _auth_headers(client, "alice")
    client.post(f"/assistant/proposals/{waiting}", json={"state": "sent"}, headers=me)
    client.post(
        f"/assistant/proposals/{declined}", json={"state": "dismissed"}, headers=me
    )

    sent = wait(waiting)
    assert sent["state"] == "sent" and "已确认" in sent["said"]
    assert "没有执行" in wait(declined)["said"]


def test_a_card_decided_after_the_answer_is_told_with_the_next_question(client):
    """A card the person decided on once its answer had ended is told to 芝士
    with their next question, once."""
    conversation = _asked_in(client, "alice", "帮我回一下李老师")
    owner = _ledger_user(client, "alice")[0]

    async def put_up():
        async with client.test_factory() as db:
            row = await proposals.propose(
                db,
                user_id=owner,
                work=str(uuid.uuid4()),
                kind="act",
                payload={"summary": "在频道「组会」发消息：周五前发你", "calls": []},
            )
            message = AssistantMessage(
                conversation_id=uuid.UUID(conversation),
                seq=2,
                role="assistant",
                text="卡片在面板中。",
                parts={"proposals": [proposals.view(row)]},
            )
            db.add(message)
            await db.commit()
            return row.id

    card_id = asyncio.run(put_up())
    client.post(
        f"/assistant/proposals/{card_id}",
        json={"state": "sent"},
        headers=_auth_headers(client, "alice"),
    )

    async def untold():
        async with client.test_factory() as db:
            return await proposals.untold(db, owner, [card_id])

    [row] = asyncio.run(untold())
    assert (
        proposals.outcome(row)
        == "「在频道「组会」发消息：周五前发你」：用户已确认，已执行。"
    )


def test_what_waits_on_the_person_reads_by_its_ref(client):
    """The inbox names an alert by its ref, and that ref reads: the alert as
    the inbox lists it, or that it no longer waits."""
    project = new_project(client, name="组会", owner="bob")
    add_external_member(client, project["id"], "alice", by="bob")
    r = client.post(
        f"/projects/{project['id']}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "中期答辩时间",
            "body": "选一个日子",
            "target_handle": "alice",
            "payload": {"options": ["11 月 5 日", "11 月 12 日"]},
        },
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text
    token = _personal(client, "alice")
    inbox = client.post("/assistant/agent/inbox", json={}, headers=_as(token))
    ref = re.search(r"alerts/\d+", inbox.json()["data"]["text"]).group(0)

    read = _read(client, token, ref)
    assert read.status_code == 200, read.text
    assert "中期答辩时间" in read.json()["data"]["text"]
    assert "11 月 12 日" in read.json()["data"]["text"]
    gone = _read(client, token, "alerts/999999")
    assert (
        gone.json()["data"]["text"]
        == "「alerts/999999」已经不在待办里了：处理过了，或者已被撤回。"
    )


def test_removing_someone_is_asked_by_the_persons_rule_with_no_model(
    client, monkeypatch
):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "把 bob 移出综合频道"))

    async def no_model(session):
        raise AssertionError("a rule settles it")

    monkeypatch.setattr(gate, "gatekeeper", no_model)

    r = client.post(
        "/assistant/agent/act",
        json={
            "kind": "member",
            "where": f"{slug}/channels/{number}",
            "who": "bob",
            "remove": True,
        },
        headers=_as(token),
    )

    got = r.json()["data"]
    assert got["done"] is False
    assert got["card"]["kind"] == "act"
    assert got["card"]["calls"][0]["method"] == "DELETE"
    [check] = _checks(client, "alice")
    assert check.stage == "rule"


def test_someone_named_by_their_name_is_added_by_their_handle(client, monkeypatch):
    """People say names, the routes take handles: a name one account goes by
    is that account, named both ways in the action the gate reads, and a name
    nobody goes by is said so rather than tried as a handle."""
    room, _ = _room(client)
    slug, number = _names(client, room)
    seed_user(client, "zhaoym")
    user_id = _ledger_user(client, "zhaoym")[0]

    async def name():
        async with client.test_factory() as db:
            profiles = UserProfileRepository(db)
            profile = await profiles.get_profile_by_user_id(user_id)
            if profile is None:
                await profiles.create_profile(
                    user_id=user_id, nickname="赵一鸣", intro="", avatar_id=1
                )
            else:
                profile.nickname = "赵一鸣"
            await db.commit()

    asyncio.run(name())
    token = _acting(client, "alice", _asked_in(client, "alice", "把赵一鸣加进综合频道"))
    where = f"{slug}/channels/{number}"

    r = client.post(
        "/assistant/agent/act",
        json={"kind": "member", "where": where, "who": "赵一鸣"},
        headers=_as(token),
    )

    assert r.status_code == 200, r.text
    [call] = r.json()["data"]["calls"]
    assert call["body"] == {"handle": "zhaoym"}
    [check] = _checks(client, "alice")
    assert "将 赵一鸣（zhaoym） 加入频道" in check.summary
    unknown = client.post(
        "/assistant/agent/act",
        json={"kind": "member", "where": where, "who": "钱多多"},
        headers=_as(token),
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["i18n"]["key"] == "assistantWhoUnknown"


def test_what_the_person_can_undo_is_done_with_no_look(client, monkeypatch):
    """Adding someone to an open channel — one they could join themselves —
    and making a channel are done without asking the model; adding them to a
    private channel lets them in, and is looked at."""
    room, _ = _room(client)
    slug, number = _names(client, room)
    seed_user(client, "zhaoym")
    token = _acting(client, "alice", _asked_in(client, "alice", "整理一下频道"))

    async def no_model(session):
        raise AssertionError("nothing here needs a look")

    monkeypatch.setattr(gate, "gatekeeper", no_model)
    added = client.post(
        "/assistant/agent/act",
        json={"kind": "member", "where": f"{slug}/channels/{number}", "who": "zhaoym"},
        headers=_as(token),
    )
    made = client.post(
        "/assistant/agent/act",
        json={"kind": "channel", "where": slug, "title": "论文阅读"},
        headers=_as(token),
    )

    assert added.json()["data"]["done"] is True, added.text
    assert made.json()["data"]["done"] is True, made.text
    assert [(c.verdict, c.stage) for c in _checks(client, "alice")] == [
        ("allowed", "low"),
        ("allowed", "low"),
    ]

    async def private():
        async with client.test_factory() as db:
            (await db.get(Topic, uuid.UUID(room))).members_only = True
            await db.commit()

    asyncio.run(private())
    keeper = _Gatekeeper("拦", "你没有提到要把赵一鸣加进这个频道\n结论：拦")

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)
    let_in = client.post(
        "/assistant/agent/act",
        json={"kind": "member", "where": f"{slug}/channels/{number}", "who": "zhaoym"},
        headers=_as(token),
    )

    assert let_in.json()["data"]["done"] is False, let_in.text
    assert "加入私密频道" in keeper.shown[0]


def test_what_the_person_wants_asked_about_is_read_even_when_it_is_low(
    client, monkeypatch
):
    """Rules the person wrote in their own words only a look can read, so
    with them a low action is looked at too."""
    room, _ = _room(client)
    slug, _ = _names(client, room)
    seed_user(client, "alice")
    r = client.put(
        "/assistant/rules",
        json={"ask": [], "askItems": ["建频道先问我。"]},
        headers=_auth_headers(client, "alice"),
    )
    assert r.status_code == 200, r.text
    token = _acting(client, "alice", _asked_in(client, "alice", "整理一下频道"))
    keeper = _Gatekeeper("拦", "你设置了建频道先问你\n结论：拦")

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)

    made = client.post(
        "/assistant/agent/act",
        json={"kind": "channel", "where": slug, "title": "论文阅读"},
        headers=_as(token),
    )

    assert made.json()["data"]["done"] is False, made.text
    assert "建频道先问我" in keeper.shown[0]


def test_the_persons_own_settings_change_at_once_and_undo_from_the_panel(client):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "晚上别推送了"))
    me = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}

    pushed = client.post(
        "/notifications/preferences/agent/set",
        json={"key": "push", "value": "off"},
        headers=_as(token),
    )
    muted = client.post(
        "/assistant/agent/notify",
        json={"where": f"{slug}/channels/{number}", "level": "mute"},
        headers=_as(token),
    )

    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["data"]["text"] == "已把推送通知改成 off。"
    assert muted.status_code == 200, muted.text
    assert (
        client.get("/notifications/preferences", headers=me).json()["data"][
            "pushEnabled"
        ]
        is False
    )
    read = client.post(
        "/notifications/preferences/agent/read", json={}, headers=_as(token)
    )
    assert "推送通知（push）：off" in read.json()["data"]["text"]

    async def made():
        async with client.test_factory() as db:
            return list(
                await db.scalars(
                    select(AssistantChange.id).order_by(AssistantChange.id)
                )
            )

    push_change, mute_change = asyncio.run(made())[-2:]
    for change in (push_change, mute_change):
        undone = client.post(f"/assistant/changes/{change}/undo", json={}, headers=me)
        assert undone.status_code == 200, undone.text
    assert (
        client.get("/notifications/preferences", headers=me).json()["data"][
            "pushEnabled"
        ]
        is True
    )


def test_the_person_sets_their_rules_and_sees_what_was_done_for_them(
    client, monkeypatch
):
    room, _ = _room(client)
    slug, number = _names(client, room)
    me = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    rules = client.get("/assistant/rules", headers=me).json()["data"]
    assert set(rules["ask"]) == set(gate.CATEGORIES)
    # Messages to everyone she lets through; she asks about channels.
    saved = client.put(
        "/assistant/rules",
        json={"ask": ["remove_member"], "askItems": ["建频道先问我"]},
        headers=me,
    )
    assert saved.json()["data"]["ask"] == ["remove_member"]
    token = _acting(client, "alice", _asked_in(client, "alice", "在综合频道说一声收到"))
    # 芝士 cannot change them: its credential does not open that route.
    assert (
        client.put("/assistant/rules", json={"ask": []}, headers=_as(token)).status_code
        == 403
    )
    keeper = _Gatekeeper("放")

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)
    client.post(
        "/assistant/agent/act",
        json={"kind": "message", "where": f"{slug}/channels/{number}", "text": "收到"},
        headers=_as(token),
    )
    assert "需要确认：\n- 建频道先问我" in keeper.shown[0]

    done = client.get("/assistant/activity", headers=me).json()["data"]["items"]
    assert [(i["kind"], i["verdict"], i["stage"]) for i in done] == [
        ("message", "allowed", "quick")
    ]
    assert "收到" in done[0]["summary"]


def test_the_steps_of_a_long_piece_of_work_are_counted_back(client):
    me = _as(_personal(client, "todo-asker"))
    todos = [
        {"content": "读评分标准", "status": "completed"},
        {"content": "给 40 份报告打分", "status": "in_progress"},
        {"content": "汇总成表", "status": "pending"},
    ]

    r = client.post("/assistant/agent/todo", json={"todos": todos}, headers=me)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == "清单已更新：1/3 项已完成。"
    too_many = [{"content": f"第 {n} 步", "status": "pending"} for n in range(31)]
    assert (
        client.post(
            "/assistant/agent/todo", json={"todos": too_many}, headers=me
        ).status_code
        == 400
    )
    assert client.post("/assistant/agent/todo", json={"todos": todos}).status_code in (
        401,
        403,
    )
