"""The platform names rooms and renames them only when their direction changes
(app/domain/topic/naming.py), over the real HTTP stack, database and Valkey.

What is pinned: an unnamed room is named from its first real message; the name
is checked once against the first turn; later changes wait for a signal and a
throttle; a name a person chose is never overwritten, including by a rename
computed while the person was renaming; an automatic rename can be undone and a
room handed back; a project on manual naming is left alone. The gateway is a
MockTransport that answers from a script and records what it was asked.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.topic import naming
from app.domain.topic.models import TitleSource, Topic, TopicTitle
from tests.conftest import seed_user
from tests.integration.conftest import post_project


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The LiteLLM gateway, stubbed: ``answers`` is what the model says next."""
    seen: dict = {"answers": [], "asked": [], "mints": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            seen["mints"] += 1
            return httpx.Response(200, json={"key": "sk-naming"})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            seen["asked"].append(body["messages"][-1]["content"])
            answer = seen["answers"].pop(0) if seen["answers"] else {"keep": True}
            content = answer if isinstance(answer, str) else json.dumps(answer)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": content}}]}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    # Routes nudge naming in the background; these tests drive it by hand.
    monkeypatch.setattr(naming, "nudge", lambda room_id, reason: None)
    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("topic-naming:*"):
        r.delete(key)
    return seen


@pytest.fixture
def alice(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, 'alice')}"}


def _project(client, alice) -> str:
    r = post_project(client, json={"name": "P", "owner_handle": "alice"}, headers=alice)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, alice, project_id: str, title: str = "新话题") -> str:
    r = client.post(
        "/topics", json={"project_id": project_id, "title": title}, headers=alice
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _say(client, room_id: str, text: str, *, author: str = "alice") -> None:
    async def add():
        async with client.test_factory() as s:
            room = await s.get(Topic, uuid.UUID(room_id))
            s.add(
                Block(
                    project_id=room.project_id,
                    topic_id=room.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=author,
                    content=text,
                )
            )
            await s.commit()

    asyncio.run(add())


def _row(client, room_id: str) -> Topic:
    async def read():
        async with client.test_factory() as s:
            return await s.get(Topic, uuid.UUID(room_id))

    return asyncio.run(read())


def _set(client, room_id: str, **values) -> None:
    async def write():
        async with client.test_factory() as s:
            room = await s.get(Topic, uuid.UUID(room_id))
            for k, v in values.items():
                setattr(room, k, v)
            await s.commit()

    asyncio.run(write())


def _history(client, room_id: str) -> list[tuple[str, str, str]]:
    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(TopicTitle)
                .where(TopicTitle.topic_id == uuid.UUID(room_id))
                .order_by(TopicTitle.created_at)
            )
            return [(r.title, r.source.value, r.reason) for r in rows]

    return asyncio.run(read())


def _events(client, room_id: str) -> list[Block]:
    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(Block).where(
                    Block.topic_id == uuid.UUID(room_id),
                    Block.kind == BlockKind.event,
                )
            )
            return [b for b in rows if (b.meta or {}).get("action") == "title"]

    return asyncio.run(read())


def _run(client, room_id: str, reason: str):
    return client.portal.call(
        lambda: naming.run(
            uuid.UUID(room_id), reason, session_factory=client.test_request_factory
        )
    )


def _named(client, alice, gateway, title: str = "dev 外网访问慢排查") -> str:
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": title})
    assert _run(client, rid, "message") is not None
    return rid


# ---------- naming ----------


def test_a_room_created_with_a_name_is_a_persons_and_an_unnamed_one_is_not(
    client, alice, gateway
):
    pid = _project(client, alice)
    unnamed = client.get(f"/topics/{_room(client, alice, pid)}").json()["data"]
    named = client.get(f"/topics/{_room(client, alice, pid, '周报')}").json()["data"]
    assert unnamed["title_source"] == "placeholder"
    assert named["title_source"] == "human"


def test_an_unnamed_room_waits_for_something_worth_naming_it_by(client, alice, gateway):
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "@芝士 在吗")
    assert _run(client, rid, "message") is None
    assert gateway["asked"] == []

    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "「dev 外网访问慢排查」"})
    renamed = _run(client, rid, "message")
    assert renamed is not None and renamed.title == "dev 外网访问慢排查"

    room = client.get(f"/topics/{rid}").json()["data"]
    assert (room["title"], room["title_source"]) == ("dev 外网访问慢排查", "auto")
    # The model read the room as data, and the key was minted once.
    assert (
        "外网访问很慢" in gateway["asked"][0]
        and "<conversation>" in gateway["asked"][0]
    )
    assert gateway["mints"] == 1
    # Naming an unnamed room is not announced; it is recorded.
    assert _events(client, rid) == []
    assert _history(client, rid) == [("dev 外网访问慢排查", "auto", "name")]


def test_the_first_turn_checks_the_name_once(client, alice, gateway):
    rid = _named(client, alice, gateway)
    assert _row(client, rid).title_calibrated is False

    # Still right: kept, and the room counts as calibrated.
    gateway["answers"].append({"keep": True, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "turn") is None
    row = _row(client, rid)
    assert row.title == "dev 外网访问慢排查" and row.title_calibrated is True

    # Calibration does not run twice; a later turn with no signal asks nothing.
    asked = len(gateway["asked"])
    assert _run(client, rid, "turn") is None
    assert len(gateway["asked"]) == asked


def test_a_calibration_that_renames_says_so_in_the_room(client, alice, gateway):
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": False, "title": "Valkey 连接池耗尽"})
    renamed = _run(client, rid, "turn")
    assert renamed is not None and renamed.previous == "dev 外网访问慢排查"
    [event] = _events(client, rid)
    assert event.author_type == AuthorType.platform
    assert (event.meta["from"], event.meta["to"]) == (
        "dev 外网访问慢排查",
        "Valkey 连接池耗尽",
    )


def test_following_up_needs_a_signal_and_respects_the_throttle(client, alice, gateway):
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": True, "title": "dev 外网访问慢排查"})
    _run(client, rid, "turn")
    asked = len(gateway["asked"])

    # An ordinary message is no reason to look again.
    _say(client, rid, "好的，继续")
    assert _run(client, rid, "message") is None
    # A signal right after a judgement waits for the interval…
    assert _run(client, rid, "signal") is None
    assert len(gateway["asked"]) == asked

    # …and is remembered: once the interval has passed, any trigger acts on it.
    _set(client, rid, title_checked_at=datetime.now(UTC) - timedelta(hours=1))
    gateway["answers"].append({"keep": False, "title": "gateway 限流策略"})
    renamed = _run(client, rid, "message")
    assert renamed is not None and renamed.stage == "follow"
    assert _row(client, rid).title == "gateway 限流策略"


def test_a_follow_up_that_only_rewords_keeps_the_title(client, alice, gateway):
    rid = _named(client, alice, gateway)
    _set(
        client,
        rid,
        title_calibrated=True,
        title_checked_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # The model says "change" but only the punctuation differs.
    gateway["answers"].append({"keep": False, "title": "dev-外网访问慢排查！"})
    assert _run(client, rid, "signal") is None
    assert _row(client, rid).title == "dev 外网访问慢排查"
    assert _events(client, rid) == []


def test_a_persons_name_is_never_overwritten(client, alice, gateway):
    rid = _named(client, alice, gateway)
    r = client.post(f"/topics/{rid}/title", json={"title": "我的名字"}, headers=alice)
    assert r.status_code == 200 and r.json()["data"]["title_source"] == "human"
    asked = len(gateway["asked"])
    for reason in ("message", "turn", "signal"):
        assert _run(client, rid, reason) is None
    assert len(gateway["asked"]) == asked
    assert _row(client, rid).title == "我的名字"


def test_a_rename_computed_while_a_person_renamed_is_dropped(client, alice, gateway):
    rid = _named(client, alice, gateway)

    async def race():
        async with client.test_request_factory() as s:
            stale = await s.get(Topic, uuid.UUID(rid))
            # Meanwhile, a person renames the room.
            async with client.test_request_factory() as other:
                room = await other.get(Topic, uuid.UUID(rid))
                await naming.rename_by_person(
                    other, room, "人起的名字", by="alice", reason="rename"
                )
                await other.commit()
            return await naming._write(
                s,
                stale,
                stage="follow",
                verdict=naming.Verdict(keep=False, title="机器起的名字"),
            )

    assert client.portal.call(race) is None
    row = _row(client, rid)
    assert (row.title, row.title_source) == ("人起的名字", TitleSource.human)


# ---------- people ----------


def test_an_automatic_rename_can_be_undone_once(client, alice, gateway):
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": False, "title": "Valkey 连接池耗尽"})
    _run(client, rid, "turn")
    [event] = _events(client, rid)

    r = client.post(
        f"/topics/{rid}/title/undo", json={"event_id": str(event.id)}, headers=alice
    )
    assert r.status_code == 200, r.text
    assert (r.json()["data"]["title"], r.json()["data"]["title_source"]) == (
        "dev 外网访问慢排查",
        "human",
    )
    again = client.post(
        f"/topics/{rid}/title/undo", json={"event_id": str(event.id)}, headers=alice
    )
    assert again.status_code == 422


def test_a_room_can_be_handed_back_to_automatic_naming(client, alice, gateway):
    rid = _named(client, alice, gateway)
    client.post(f"/topics/{rid}/title", json={"title": "我的名字"}, headers=alice)
    r = client.post(f"/topics/{rid}/title/auto", headers=alice)
    assert r.status_code == 200 and r.json()["data"]["title_source"] == "auto"
    # Judged again straight away, as a follow-up.
    gateway["answers"].append({"keep": False, "title": "新的方向"})
    renamed = _run(client, rid, "signal")
    assert renamed is not None and _row(client, rid).title == "新的方向"
    assert [h[2] for h in _history(client, rid)] == [
        "name",
        "rename",
        "restore",
        "follow",
    ]


def test_a_suggestion_is_only_a_suggestion(client, alice, gateway):
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": False, "title": "建议的名字"})
    r = client.post(f"/topics/{rid}/title/suggest", headers=alice)
    assert r.status_code == 200 and r.json()["data"]["title"] == "建议的名字"
    assert _row(client, rid).title == "dev 外网访问慢排查"

    confirmed = client.post(
        f"/topics/{rid}/title",
        json={"title": "建议的名字", "suggested": True},
        headers=alice,
    )
    assert confirmed.json()["data"]["title_source"] == "human"
    assert _history(client, rid)[-1] == ("建议的名字", "human", "suggest")


def test_a_project_on_manual_naming_is_left_alone(client, alice, gateway):
    pid = _project(client, alice)
    assert (
        client.get(f"/projects/{pid}/topic-naming", headers=alice).json()["data"][
            "mode"
        ]
        == "auto"
    )
    r = client.put(
        f"/projects/{pid}/topic-naming", json={"mode": "manual"}, headers=alice
    )
    assert r.status_code == 200 and r.json()["data"]["mode"] == "manual"
    bad = client.put(f"/projects/{pid}/topic-naming", json={"mode": "x"}, headers=alice)
    assert bad.status_code == 422

    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    assert _run(client, rid, "message") is None
    assert gateway["asked"] == []
    assert _row(client, rid).title == "新话题"


def test_without_a_gateway_nothing_is_asked(client, alice, gateway, monkeypatch):
    monkeypatch.setattr(settings, "llm_gateway_admin_base", None)
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    assert _run(client, rid, "message") is None
    assert gateway["asked"] == []


def test_a_rewritten_goal_and_a_split_are_signals(client, alice, gateway, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        naming, "nudge", lambda room_id, reason: seen.append((str(room_id), reason))
    )
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    doc = client.get(f"/topics/{rid}/doc", headers=alice).json()["data"] or {}
    version = doc.get("doc_version", 0)
    r = client.put(
        f"/topics/{rid}/doc",
        json={"content": "## 目标\n改成 gateway 限流", "expected_version": version},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/topics/{rid}/split",
        json={"title": "限流开关", "reviewer_handle": "alice"},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    assert seen == [(rid, "signal"), (rid, "signal")]
