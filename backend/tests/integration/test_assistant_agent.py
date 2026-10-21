"""What a person's 芝士 reads with (`routes/assistant_agent.py`): things by the
words of their address, as the person, in text."""

import asyncio
import uuid

from sqlalchemy import select

from app.core.sandbox_auth import mint_delegated_credential
from app.domain.assistant import gate
from app.domain.assistant.models import (
    AssistantChange,
    AssistantConversation,
    AssistantGateCheck,
    AssistantMessage,
)
from app.domain.gateway_chat import Completion, Usage
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_message_edit import _room, _say


def _as(token: str) -> dict:
    return {"X-Cheese-Token": token}


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
    assert "周五组会改到下午三点" in keeper.shown[0].split("<要判断的这件事>")[1]
    # The call it hands back is the one the channel page makes, as her.
    [call] = got["calls"]
    sent = client.post(
        call["path"],
        json=call["body"],
        headers={**_as(token), "X-Cheese-Pass": got["pass"]},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["author"] == "alice"
    # Once: the pass is spent.
    again = client.post(
        call["path"],
        json={**call["body"], "request_id": str(uuid.uuid4())},
        headers={**_as(token), "X-Cheese-Pass": got["pass"]},
    )
    assert again.status_code == 403
    [check] = _checks(client, "alice")
    assert (check.verdict, check.stage, check.kind) == ("allowed", "quick", "message")
    assert check.cache_read_tokens == 80


def test_without_the_gate_the_route_refuses_the_persons_cheese(client):
    room, _ = _room(client)
    token = _acting(client, "alice", _asked_in(client, "alice", "你好"))
    r = client.post(
        f"/topics/{room}/messages",
        json={"content": "我自己发", "request_id": str(uuid.uuid4())},
        headers=_as(token),
    )
    assert r.status_code == 403


def test_a_message_the_person_did_not_ask_for_becomes_a_card(client, monkeypatch):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", "帮我看看综合频道"))
    keeper = _Gatekeeper("拦", "用户只让看看，没让发消息。\n结论：拦")

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)

    r = client.post(
        "/assistant/agent/act",
        json={"kind": "message", "where": f"{slug}/channels/{number}", "text": "收到"},
        headers=_as(token),
    )

    got = r.json()["data"]
    assert got["done"] is False and "pass" not in got
    assert got["card"]["kind"] == "message" and got["card"]["text"] == "收到"
    assert got["reason"] == "用户只让看看，没让发消息。"
    [check] = _checks(client, "alice")
    assert (check.verdict, check.stage) == ("asked", "reasoned")


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
        json={"ask": ["remove_member"], "allowText": "", "askText": "建频道先问我"},
        headers=me,
    )
    assert saved.json()["data"]["ask"] == ["remove_member"]
    token = _acting(client, "alice", _asked_in(client, "alice", "在综合频道说一声收到"))
    # 芝士 cannot change them.
    assert (
        client.put("/assistant/rules", json={"ask": []}, headers=_as(token)).status_code
        == 401
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
    assert "要先问：建频道先问我" in keeper.shown[0]

    done = client.get("/assistant/activity", headers=me).json()["data"]["items"]
    assert [(i["kind"], i["verdict"], i["stage"]) for i in done] == [
        ("message", "allowed", "quick")
    ]
    assert "收到" in done[0]["summary"]
