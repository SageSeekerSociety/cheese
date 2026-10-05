"""The platform names rooms and renames them only when their direction changes
(app/domain/topic/naming.py), over the real HTTP stack, database and Valkey.

What is pinned: an unnamed room is named from its first real message, and an
`@` inside a sentence does not hide what the person said; the name is checked
once against the first turn; later changes wait for a signal and a throttle; a
name a person chose is never overwritten, including by a rename computed while
the person was renaming; an automatic rename can be undone and a room handed
back; a project on manual naming is left alone; the model is asked not to think
before it answers, which is what used to eat the answer; a trigger that asks
nothing says which room and why; an answer cut off before the model wrote
anything leaves the room as it was without counting as a failed call, while one
that never arrives at all still backs the room off. The gateway is a
MockTransport that answers from a script and records what it was asked.
"""

import asyncio
import json
import logging
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
from tests.integration.conftest import open_task, post_project
from tests.support.living_doc import document_of


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The LiteLLM gateway, stubbed: ``answers`` is what the model says next.

    An answer is the JSON object the model replies with, or an
    ``httpx.Response`` for a call that does not come back the usual way (the
    model cut off mid-thought, an upstream error). ``bodies`` is what the model
    was asked."""
    seen: dict = {"answers": [], "asked": [], "bodies": [], "mints": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            seen["mints"] += 1
            return httpx.Response(200, json={"key": "sk-naming"})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            seen["asked"].append(body["messages"][-1]["content"])
            seen["bodies"].append(body)
            answer = seen["answers"].pop(0) if seen["answers"] else {"keep": True}
            if isinstance(answer, httpx.Response):
                return answer
            content = answer if isinstance(answer, str) else json.dumps(answer)
            return httpx.Response(
                200,
                headers={"x-litellm-response-cost": "0.0004"},
                json={
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 300, "completion_tokens": 12},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            # Only the model's calls: a client already given a transport (the
            # collaboration service's stand-in) keeps it.
            if kwargs.get("transport") is None:
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
    r = post_project(client, json={"name": "P"}, headers=alice, owner="alice")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, alice, project_id: str, title: str | None = None) -> str:
    """A room; without a title, an unnamed one — no title is how a client asks."""
    body = {"project_id": project_id} | ({"title": title} if title else {})
    r = client.post("/topics", json=body, headers=alice)
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


def _cut_off() -> httpx.Response:
    """The model spent its whole budget thinking and never wrote a title."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    )


def _backed_off(room_id: str) -> bool:
    """Whether the room is being made to wait before the gateway is asked again."""
    r = redis.Redis.from_url(settings.redis_url)
    return bool(r.exists(f"topic-naming:backoff:{room_id}"))


# ---------- naming ----------


def test_a_room_created_with_a_name_is_a_persons_and_an_unnamed_one_is_not(
    client, alice, gateway
):
    pid = _project(client, alice)
    unnamed = client.get(f"/topics/{_room(client, alice, pid)}").json()["data"]
    named = client.get(f"/topics/{_room(client, alice, pid, '周报')}").json()["data"]
    assert unnamed["title_source"] == "placeholder"
    assert named["title_source"] == "human"


def test_an_unnamed_room_is_known_by_its_flag_not_by_its_words(client, alice, gateway):
    """Screens name an unnamed room in their reader's language, so the flag is
    what says it has no name. The stored placeholder is what the agents read;
    a person who types those same words has named the room."""
    pid = _project(client, alice)
    for body in ({}, {"title": ""}, {"title": "   "}):
        r = client.post("/topics", json={"project_id": pid, **body}, headers=alice)
        assert r.status_code == 200, r.text
        room = r.json()["data"]
        assert (room["title"], room["title_source"]) == ("新话题", "placeholder")
    typed = _room(client, alice, pid, "新话题")
    assert client.get(f"/topics/{typed}").json()["data"]["title_source"] == "human"


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


def test_naming_is_recorded_as_the_platforms_spend_and_charges_no_team(
    client, alice, gateway
):
    """Naming a room is work the platform does unasked (#2233): what it spent
    is on the platform's books, and no team's credits or usage move."""
    from app.domain.usage.ledger import Ledger, payer_for_project
    from app.domain.usage.models import ResourceUsage

    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "message") is not None

    async def read():
        async with client.test_factory() as s:
            rows = list(
                await s.scalars(
                    select(ResourceUsage).where(ResourceUsage.kind == "topic_naming")
                )
            )
            payer = await payer_for_project(s, uuid.UUID(pid))
            balance = await Ledger(s).balance(payer)
            team_rows = list(
                await s.scalars(
                    select(ResourceUsage).where(
                        (ResourceUsage.team_id == payer.team_id)
                        | (ResourceUsage.project_id == uuid.UUID(pid))
                    )
                )
            )
            return rows, balance, team_rows

    rows, balance, team_rows = client.portal.call(read)
    assert [(r.input_tokens, r.output_tokens, r.cost_usd) for r in rows] == [
        (300, 12, 0.0004)
    ]
    assert rows[0].team_id is None and rows[0].credits == 0
    assert balance.credits_used == 0
    assert team_rows == []


def test_an_at_sign_in_the_middle_of_a_sentence_is_not_a_mention(
    client, alice, gateway
):
    """The room's one person message has to be read as what it says. Written
    before the fix, this opener left four characters behind, counted as an
    empty opener, and the room was never named (room b031c720, 2026-09-27)."""
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(
        client,
        rid,
        "<@芝士> 当我打开@的时候，我不能通过键盘上的上下键进行AI队友的切换，"
        "得通过鼠标移动，我希望加上这个功能",
    )
    gateway["answers"].append({"keep": False, "title": "「提及候选列表键盘导航」"})
    renamed = _run(client, rid, "message")
    assert renamed is not None and renamed.title == "提及候选列表键盘导航"
    assert (client.get(f"/topics/{rid}").json()["data"]["title_source"]) == "auto"


def test_a_trigger_that_asks_nothing_says_so(client, alice, gateway, caplog):
    """A room that keeps its title is quiet, not traceless: the trigger says
    which room it was and why it asked nothing, or 「为什么这个房间没改名」
    has no answer anywhere (room b031c720, 2026-09-27)."""
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "@芝士 在吗")
    with caplog.at_level(logging.INFO, logger="app.domain.topic.naming"):
        assert _run(client, rid, "message") is None
    [line] = [r.getMessage() for r in caplog.records if str(rid) in r.getMessage()]
    assert "opener_says_too_little" in line

    # A call that is made says nothing of the sort — the room was named.
    caplog.clear()
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "「dev 外网访问慢排查」"})
    with caplog.at_level(logging.INFO, logger="app.domain.topic.naming"):
        assert _run(client, rid, "message") is not None
    assert [r for r in caplog.records if str(rid) in r.getMessage()] == []


def test_a_title_cut_off_before_it_was_written_is_not_a_failure(client, alice, gateway):
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")

    gateway["answers"].append(_cut_off())
    assert _run(client, rid, "message") is None
    # Nothing was written, and the room is not made to wait for it: the next
    # message in the room asks the gateway again.
    assert _row(client, rid).title == "新话题"
    assert not _backed_off(rid)
    gateway["answers"].append({"keep": False, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "message") is not None
    assert len(gateway["asked"]) == 2


def test_a_gateway_that_does_not_answer_backs_the_room_off(client, alice, gateway):
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")

    gateway["answers"].append(httpx.Response(503, json={"error": "no upstream"}))
    assert _run(client, rid, "message") is None
    assert _backed_off(rid)
    # And the room waits rather than asking again on the next message.
    assert _run(client, rid, "message") is None
    assert len(gateway["asked"]) == 1


def test_the_naming_call_asks_the_model_not_to_think(client, alice, gateway):
    """Whatever it thinks comes out of the same budget as the title, and left
    to itself the model thinks the whole budget away: over two real rooms'
    material (2026-09-27, deepseek-flash) six of eight calls at 1024 tokens
    came back empty, and at 4096 five of twenty still did, those taking longer
    than the call's own timeout. With thinking off the same material answered
    twelve times out of twelve, 15–20 tokens an answer."""
    _named(client, alice, gateway)
    assert gateway["bodies"][-1]["thinking"] == {"type": "disabled"}


def test_the_naming_call_still_carries_a_ceiling(client, alice, gateway):
    """An answer costs about twenty tokens now; the ceiling is what keeps a
    runaway answer from becoming a runaway call."""
    _named(client, alice, gateway)
    assert gateway["bodies"][-1]["max_tokens"] >= 700


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


def test_a_rewritten_goal_and_a_new_task_are_signals(
    client, alice, gateway, monkeypatch
):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        naming, "nudge", lambda room_id, reason: seen.append((str(room_id), reason))
    )
    pid = _project(client, alice)
    rid = _room(client, alice, pid)
    document = document_of(client, rid, headers=alice)
    doc = client.get(f"/documents/{document}", headers=alice).json()["data"] or {}
    version = doc.get("doc_version", 0)
    r = client.put(
        f"/documents/{document}",
        json={"content": "## 目标\n改成 gateway 限流", "expected_version": version},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    open_task(client, rid, "限流开关", start=False)
    assert seen == [(rid, "signal"), (rid, "signal")]
