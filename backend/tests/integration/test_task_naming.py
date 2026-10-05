"""The platform names tasks and renames them only when their direction changes
(app/domain/room_task/naming.py), over the real HTTP stack, database and Valkey.

What is pinned: a task opened without a title is named from its first real
message, and an `@` inside a sentence does not hide what the person said; the
name is checked once against the first turn; later changes wait for a signal
and a throttle; a task an AI teammate proposed keeps its title until its
direction changes; a name a person chose is never overwritten, including by a
rename computed while the person was renaming; a rename is written quietly,
with no line in the conversation; a channel is never named by the platform; a
project on manual naming is left alone; the model is asked not to think before
it answers, which is what used to eat the answer; a trigger that asks nothing
says which task and why; an answer cut off before the model wrote anything
leaves the task as it was without counting as a failed call, while one that
never arrives at all still backs the task off. The gateway is a MockTransport
that answers from a script and records what it was asked.
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
from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.room_task import naming
from app.domain.room_task.models import Task, TaskTitle
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    room_agent_seat,
)
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
    monkeypatch.setattr(naming, "nudge", lambda conversation_id, reason: None)
    monkeypatch.setattr(naming, "nudge_document", lambda document_id: None)
    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("task-naming:*"):
        r.delete(key)
    return seen


@pytest.fixture
def alice(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, 'alice')}"}


def _project(client, alice) -> dict:
    r = post_project(client, json={"name": "P"}, headers=alice, owner="alice")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _task(client, alice, room_id: str, title: str | None = None) -> str:
    """A task in ``room_id``; without a title, an unnamed one."""
    body = {"title": title} if title else {}
    r = client.post(f"/topics/{room_id}/tasks", json=body, headers=alice)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _unnamed(client, alice) -> str:
    return _task(client, alice, _project(client, alice)["root_topic_id"])


def _say(client, conversation_id: str, text: str, *, author: str = "alice") -> None:
    async def add():
        async with client.test_factory() as s:
            task = await s.get(Task, uuid.UUID(conversation_id))
            s.add(
                Block(
                    project_id=task.project_id,
                    conversation_id=task.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=author,
                    content=text,
                )
            )
            await s.commit()

    asyncio.run(add())


def _row(client, task_id: str) -> Task:
    async def read():
        async with client.test_factory() as s:
            return await s.get(Task, uuid.UUID(task_id))

    return asyncio.run(read())


def _set(client, task_id: str, **values) -> None:
    async def write():
        async with client.test_factory() as s:
            task = await s.get(Task, uuid.UUID(task_id))
            for k, v in values.items():
                setattr(task, k, v)
            await s.commit()

    asyncio.run(write())


def _history(client, task_id: str) -> list[tuple[str, str, str]]:
    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(TaskTitle)
                .where(TaskTitle.task_id == uuid.UUID(task_id))
                .order_by(TaskTitle.created_at)
            )
            return [(r.title, r.source.value, r.reason) for r in rows]

    return asyncio.run(read())


def _events(client, conversation_id: str) -> list[Block]:
    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(Block).where(
                    Block.conversation_id == uuid.UUID(conversation_id),
                    Block.kind == BlockKind.event,
                )
            )
            return list(rows)

    return asyncio.run(read())


def _run(client, conversation_id: str, reason: str):
    return client.portal.call(
        lambda: naming.run(
            uuid.UUID(conversation_id),
            reason,
            session_factory=client.test_request_factory,
        )
    )


def _shown(client, task_id: str) -> tuple[str, str]:
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    return task["title"], task["title_source"]


def _named(client, alice, gateway, title: str = "dev 外网访问慢排查") -> str:
    tid = _unnamed(client, alice)
    _say(client, tid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": title})
    assert _run(client, tid, "message") is not None
    return tid


def _cut_off() -> httpx.Response:
    """The model spent its whole budget thinking and never wrote a title."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    )


def _backed_off(task_id: str) -> bool:
    """Whether the task is being made to wait before the gateway is asked again."""
    r = redis.Redis.from_url(settings.redis_url)
    return bool(r.exists(f"task-naming:backoff:{task_id}"))


def _proposed(client, alice, title: str) -> str:
    """A task an AI teammate proposed under ``title`` and a person created."""
    room = _project(client, alice)["root_topic_id"]
    proposal = client.post(
        f"/topics/{room}/task-proposals",
        json={"title": title, "summary": "把旧表搬到新表"},
        headers=room_agent_headers(client, room),
    )
    assert proposal.status_code == 200, proposal.text
    accepted = client.post(
        f"/topics/{room}/task-proposals/{proposal.json()['data']['id']}/accept",
        headers=alice,
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["data"]["id"]


# ---------- naming ----------


def test_a_task_opened_with_a_title_is_a_persons_and_one_without_is_not(
    client, alice, gateway
):
    room = _project(client, alice)["root_topic_id"]
    assert _shown(client, _task(client, alice, room)) == ("新任务", "placeholder")
    assert _shown(client, _task(client, alice, room, "周报")) == ("周报", "human")


def test_a_channel_is_never_named_by_the_platform(client, alice, gateway):
    """A channel is named by whoever creates it; one without a name is refused,
    and nothing said in a channel asks the model anything."""
    pid = _project(client, alice)["id"]
    for body in ({}, {"title": ""}, {"title": "   "}):
        r = client.post("/topics", json={"project_id": pid, **body}, headers=alice)
        assert r.status_code in (400, 422), r.text
    room = client.post(
        "/topics", json={"project_id": pid, "title": "设计"}, headers=alice
    ).json()["data"]["id"]
    r = client.post(
        f"/topics/{room}/messages",
        json={
            "content": "帮我排查一下 dev 机器从外网访问很慢的问题",
            "request_id": str(uuid.uuid4()),
        },
        headers=alice,
    )
    assert r.status_code == 200, r.text
    for reason in ("message", "turn", "signal"):
        assert _run(client, room, reason) is None
    assert gateway["asked"] == []


def test_an_unnamed_task_waits_for_something_worth_naming_it_by(client, alice, gateway):
    tid = _unnamed(client, alice)
    _say(client, tid, "@芝士 在吗")
    assert _run(client, tid, "message") is None
    assert gateway["asked"] == []

    _say(client, tid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "「dev 外网访问慢排查」"})
    renamed = _run(client, tid, "message")
    assert renamed is not None and renamed.title == "dev 外网访问慢排查"

    assert _shown(client, tid) == ("dev 外网访问慢排查", "auto")
    # The model read the task as data, and the key was minted once.
    assert (
        "外网访问很慢" in gateway["asked"][0]
        and "<conversation>" in gateway["asked"][0]
    )
    assert gateway["mints"] == 1
    assert _history(client, tid) == [("dev 外网访问慢排查", "auto", "name")]


def test_the_task_document_is_what_the_model_reads_as_its_goal(client, alice, gateway):
    tid = _unnamed(client, alice)
    document = document_of(client, tid, headers=alice)
    r = client.put(
        f"/documents/{document}",
        json={"content": "## 目标\n给 gateway 加限流", "expected_version": 0},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    _say(client, tid, "按文档里写的来做")
    gateway["answers"].append({"keep": False, "title": "gateway 限流"})
    assert _run(client, tid, "message") is not None
    assert "给 gateway 加限流" in gateway["asked"][0]


def test_naming_is_recorded_as_the_platforms_spend_and_charges_no_team(
    client, alice, gateway
):
    """Naming a task is work the platform does unasked (#2233): what it spent
    is on the platform's books, and no team's credits or usage move."""
    from app.domain.usage.ledger import Ledger, payer_for_project
    from app.domain.usage.models import ResourceUsage

    project = _project(client, alice)
    pid = project["id"]
    rid = _task(client, alice, project["root_topic_id"])
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
    """The one person message has to be read as what it says. Written before
    the fix, this opener left four characters behind, counted as an empty
    opener, and the room it was in was never named (b031c720, 2026-09-27)."""
    rid = _unnamed(client, alice)
    _say(
        client,
        rid,
        "<@芝士> 当我打开@的时候，我不能通过键盘上的上下键进行AI队友的切换，"
        "得通过鼠标移动，我希望加上这个功能",
    )
    gateway["answers"].append({"keep": False, "title": "「提及候选列表键盘导航」"})
    renamed = _run(client, rid, "message")
    assert renamed is not None and renamed.title == "提及候选列表键盘导航"
    assert _shown(client, rid)[1] == "auto"


def test_a_trigger_that_asks_nothing_says_so(client, alice, gateway, caplog):
    """A task that keeps its title is quiet, not traceless: the trigger says
    which task it was and why it asked nothing, or 「为什么这个任务没改名」
    has no answer anywhere (b031c720, 2026-09-27)."""
    rid = _unnamed(client, alice)
    _say(client, rid, "@芝士 在吗")
    with caplog.at_level(logging.INFO, logger="app.domain.room_task.naming"):
        assert _run(client, rid, "message") is None
    [line] = [r.getMessage() for r in caplog.records if str(rid) in r.getMessage()]
    assert "opener_says_too_little" in line

    # A call that is made says nothing of the sort — the task was named.
    caplog.clear()
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "「dev 外网访问慢排查」"})
    with caplog.at_level(logging.INFO, logger="app.domain.room_task.naming"):
        assert _run(client, rid, "message") is not None
    assert [r for r in caplog.records if str(rid) in r.getMessage()] == []


def test_a_title_cut_off_before_it_was_written_is_not_a_failure(client, alice, gateway):
    rid = _unnamed(client, alice)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")

    gateway["answers"].append(_cut_off())
    assert _run(client, rid, "message") is None
    # Nothing was written, and the task is not made to wait for it: the next
    # message asks the gateway again.
    assert _row(client, rid).title == "新任务"
    assert not _backed_off(rid)
    gateway["answers"].append({"keep": False, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "message") is not None
    assert len(gateway["asked"]) == 2


def test_a_gateway_that_does_not_answer_backs_the_task_off(client, alice, gateway):
    rid = _unnamed(client, alice)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")

    gateway["answers"].append(httpx.Response(503, json={"error": "no upstream"}))
    assert _run(client, rid, "message") is None
    assert _backed_off(rid)
    # And the task waits rather than asking again on the next message.
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

    # Still right: kept, and the task counts as calibrated.
    gateway["answers"].append({"keep": True, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "turn") is None
    row = _row(client, rid)
    assert row.title == "dev 外网访问慢排查" and row.title_calibrated is True

    # Calibration does not run twice; a later turn with no signal asks nothing.
    asked = len(gateway["asked"])
    assert _run(client, rid, "turn") is None
    assert len(gateway["asked"]) == asked


def test_a_calibration_that_renames_does_so_quietly(client, alice, gateway):
    """The new title shows where the old one did; nothing is said in the
    task's conversation about it."""
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": False, "title": "Valkey 连接池耗尽"})
    renamed = _run(client, rid, "turn")
    assert renamed is not None and renamed.previous == "dev 外网访问慢排查"
    assert _shown(client, rid) == ("Valkey 连接池耗尽", "auto")
    assert _events(client, rid) == []
    assert _history(client, rid)[-1] == ("Valkey 连接池耗尽", "auto", "calibrate")


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
    assert r.status_code == 200 and _shown(client, rid) == ("我的名字", "human")
    asked = len(gateway["asked"])
    for reason in ("message", "turn", "signal"):
        assert _run(client, rid, reason) is None
    assert len(gateway["asked"]) == asked
    assert _row(client, rid).title == "我的名字"


def test_a_rename_computed_while_a_person_renamed_is_dropped(client, alice, gateway):
    rid = _named(client, alice, gateway)

    async def race():
        async with client.test_request_factory() as s:
            stale = await s.get(Task, uuid.UUID(rid))
            await s.commit()
            # Meanwhile, a person renames the task.
            r = await asyncio.to_thread(
                client.post,
                f"/topics/{rid}/title",
                json={"title": "人起的名字"},
                headers=alice,
            )
            assert r.status_code == 200, r.text
            return await naming._write(
                s,
                stale,
                stage="follow",
                verdict=naming.Verdict(keep=False, title="机器起的名字"),
            )

    assert client.portal.call(race) is None
    assert _shown(client, rid) == ("人起的名字", "human")


# ---------- proposed tasks ----------


def test_a_proposed_task_keeps_its_title_until_its_direction_changes(
    client, alice, gateway
):
    """An AI teammate's proposal names the task, and the platform does not
    second-guess it after the first turn; only a change of direction does."""
    tid = _proposed(client, alice, "迁移旧数据")
    assert _shown(client, tid) == ("迁移旧数据", "auto")
    _say(client, tid, "先从用户表开始")
    for reason in ("message", "turn"):
        assert _run(client, tid, reason) is None
    assert gateway["asked"] == []

    gateway["answers"].append({"keep": False, "title": "用户表拆分"})
    renamed = _run(client, tid, "signal")
    assert renamed is not None and renamed.stage == "follow"
    assert _shown(client, tid) == ("用户表拆分", "auto")


# ---------- naming by the task's own session ----------


def test_a_title_the_tasks_own_session_gives_is_still_the_platforms(
    client, alice, gateway
):
    """Where the platform cannot name, the task's AI teammate is asked to; what
    it picks may still change with the task's direction. A person's may not."""
    project = _project(client, alice)
    room = project["root_topic_id"]
    tid = _task(client, alice, room)
    own = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"],
            topic_id=tid,
            agent_handle=room_agent_seat(client, room),
        )
    }
    r = client.post(f"/topics/{tid}/title", json={"title": "限流开关"}, headers=own)
    assert r.status_code == 200, r.text
    assert _shown(client, tid) == ("限流开关", "auto")

    _say(client, tid, "其实要做的是 gateway 限流")
    gateway["answers"].append({"keep": False, "title": "gateway 限流"})
    assert _run(client, tid, "signal") is not None
    assert _shown(client, tid) == ("gateway 限流", "auto")


# ---------- people ----------


def test_a_project_on_manual_naming_is_left_alone(client, alice, gateway):
    project = _project(client, alice)
    pid = project["id"]
    assert (
        client.get(f"/projects/{pid}/task-naming", headers=alice).json()["data"]["mode"]
        == "auto"
    )
    r = client.put(
        f"/projects/{pid}/task-naming", json={"mode": "manual"}, headers=alice
    )
    assert r.status_code == 200 and r.json()["data"]["mode"] == "manual"
    bad = client.put(f"/projects/{pid}/task-naming", json={"mode": "x"}, headers=alice)
    assert bad.status_code == 422

    rid = _task(client, alice, project["root_topic_id"])
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    assert _run(client, rid, "message") is None
    assert gateway["asked"] == []
    assert _row(client, rid).title == "新任务"


def test_without_a_gateway_nothing_is_asked(client, alice, gateway, monkeypatch):
    monkeypatch.setattr(settings, "llm_gateway_admin_base", None)
    rid = _unnamed(client, alice)
    _say(client, rid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    assert _run(client, rid, "message") is None
    assert gateway["asked"] == []


# ---------- what nudges naming ----------


def test_what_happens_in_a_task_nudges_its_naming_and_a_channel_does_not(
    client, alice, gateway, monkeypatch
):
    """A person's message in a task and a rewrite of its document are moments
    its title may need; a message in the channel it hangs in is not."""
    said: list[tuple[str, str]] = []
    rewritten: list[str] = []
    monkeypatch.setattr(
        naming,
        "nudge",
        lambda conversation_id, reason: said.append((str(conversation_id), reason)),
    )
    monkeypatch.setattr(
        naming, "nudge_document", lambda document_id: rewritten.append(str(document_id))
    )
    room = _project(client, alice)["root_topic_id"]
    tid = _task(client, alice, room)
    for place in (room, tid):
        r = client.post(
            f"/topics/{place}/messages",
            json={
                "content": "帮我排查一下 dev 机器从外网访问很慢的问题",
                "request_id": str(uuid.uuid4()),
            },
            headers=alice,
        )
        assert r.status_code == 200, r.text
    assert (tid, "message") in said
    assert all(conversation != room for conversation, _ in said)

    document = document_of(client, tid, headers=alice)
    r = client.put(
        f"/documents/{document}",
        json={"content": "## 目标\n改成 gateway 限流", "expected_version": 0},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    assert rewritten == [document]
