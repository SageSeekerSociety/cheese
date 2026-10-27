"""The platform names tasks and renames them only when their direction changes
(app/domain/room_task/naming.py), over the real HTTP stack, database and Valkey.

What is pinned: a task opened without a title is named from its first real
message — for a task turned from a message, that message and its 支线 — and an
`@` inside a sentence does not hide what the person said; the
name is checked once against the first turn; later changes wait for a signal
and a throttle; a task an AI teammate proposed keeps its title until its
direction changes; a name a person chose is never overwritten, including by a
rename computed while the person was renaming; a rename is written quietly,
with no line in the conversation; a channel is never named by the platform; a
project on manual naming is left alone; the model is asked not to think before
it answers, which is what used to eat the answer; a trigger that asks nothing
says which task and why; an answer cut off before the model wrote anything
leaves the task as it was without counting as a failed call, while one that
never arrives at all still backs the task off; a title too wide for the
sidebar is sent back with its width a bounded number of times, inside the one
naming timeout, and left unchanged when that does not help; unread attachments
wait for textual context, and the model may abstain from naming. The gateway is a MockTransport
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
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.room_task import naming
from app.domain.room_task.models import Task, TaskTitle
from app.domain.room_task.title_width import TITLE_WIDTH, title_width
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_message,
    post_project,
    room_agent_headers,
    room_agent_seat,
    task_agent_headers,
)
from tests.support.living_doc import document_of


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The LiteLLM gateway, stubbed: ``answers`` is what the model says next.

    An answer is the JSON object the model replies with, or an
    ``httpx.Response`` for a call that does not come back the usual way (the
    model cut off mid-thought, an upstream error). ``bodies`` is what the model
    was asked."""
    seen: dict = {
        "answers": [],
        "asked": [],
        "bodies": [],
        "mints": 0,
        "nudge": naming.nudge,
    }

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


def _say(
    client,
    conversation_id: str,
    text: str,
    *,
    author: str = "alice",
    kind: BlockKind = BlockKind.message,
    mime_type: str | None = None,
) -> None:
    async def add():
        async with client.test_factory() as s:
            task = await s.get(Task, uuid.UUID(conversation_id))
            s.add(
                Block(
                    project_id=task.project_id,
                    conversation_id=task.id,
                    kind=kind,
                    author_type=AuthorType.participant,
                    author=author,
                    content=text,
                    mime_type=mime_type,
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
    """The events people in the conversation see. A line the room does not
    show (``in_room: false``) is not said to them: the platform tells the
    task's teammate a rename that way."""

    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(Block).where(
                    Block.conversation_id == uuid.UUID(conversation_id),
                    Block.kind == BlockKind.event,
                )
            )
            return [b for b in rows if (b.meta or {}).get("in_room") is not False]

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


def _by_teammate(client, alice, title: str) -> str:
    """A task an AI teammate created under ``title``."""
    room = _project(client, alice)["root_topic_id"]
    made = client.post(
        f"/topics/{room}/teammate-tasks",
        json={"title": title, "summary": "把旧表搬到新表"},
        headers=room_agent_headers(client, room),
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


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
    for reason in ("message", "idle", "signal"):
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


def _turned_from(
    client, alice, text: str, replies: tuple[str, ...] = (), *, attachment: bool = False
) -> str:
    """A task turned from a message, with its attachments and discussion."""
    room = _project(client, alice)["root_topic_id"]
    said = post_message(client, room, "alice", {"content": text})
    if attachment:

        async def attach():
            async with client.test_factory() as session:
                root = await session.get(Block, uuid.UUID(said["id"]))
                root.turn_id = root.turn_id or root.id
                session.add(
                    Block(
                        project_id=root.project_id,
                        conversation_id=root.conversation_id,
                        turn_id=root.turn_id,
                        kind=BlockKind.attachment,
                        author_type=AuthorType.participant,
                        author="alice",
                        content="uploads/screenshot.png",
                        mime_type="image/png",
                    )
                )
                await session.commit()

        asyncio.run(attach())
    if replies:
        thread = client.post(f"/blocks/{said['id']}/thread", headers=alice)
        assert thread.status_code == 200, thread.text
        for reply in replies:
            post_message(
                client, thread.json()["data"]["id"], "alice", {"content": reply}
            )
    r = client.post(f"/blocks/{said['id']}/upgrade", headers=alice)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_a_task_turned_from_a_message_is_named_by_that_message(client, alice, gateway):
    """What the task is was said in the message it was turned from; its own
    conversation may hold nothing but its AI teammate's words."""
    tid = _turned_from(
        client, alice, "请在项目里新建一个 hello.md，写一句介绍这个项目的话"
    )
    _say(client, tid, "hello.md 里那句介绍按什么口径写？", author="cheese-0123456789ab")

    gateway["answers"].append({"keep": False, "title": "hello.md 项目介绍"})
    renamed = _run(client, tid, "idle")

    assert renamed is not None
    assert _shown(client, tid) == ("hello.md 项目介绍", "auto")
    asked = gateway["asked"][0]
    assert '<message role="person">请在项目里新建一个 hello.md' in asked
    assert asked.index("新建一个 hello.md") < asked.index("按什么口径写")


def test_a_task_turned_from_a_discussion_reads_what_was_said_under_it(
    client, alice, gateway
):
    """An opener that says too little is not the whole discussion: what was said
    under it in its 支线 before the task was made is read with it."""
    tid = _turned_from(
        client, alice, "在吗", replies=("dev 机器从外网访问很慢，帮我排查一下",)
    )

    gateway["answers"].append({"keep": False, "title": "dev 外网访问慢排查"})
    renamed = _run(client, tid, "signal")

    assert renamed is not None and renamed.title == "dev 外网访问慢排查"
    assert "从外网访问很慢" in gateway["asked"][0]


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


def test_naming_is_charged_to_the_tasks_project(client, alice, gateway):
    """Naming a task is a model call made for its project: the project's team
    pays for it, and its usage says what it was for."""
    from app.domain.usage.credits import CREDIT_USD
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
            return rows, payer.team_id, await Ledger(s).balance(payer)

    rows, team_id, balance = client.portal.call(read)
    assert [
        (r.input_tokens, r.output_tokens, r.cost_usd, r.project_id, r.team_id)
        for r in rows
    ] == [(300, 12, 0.0004, uuid.UUID(pid), team_id)]
    assert rows[0].credits == pytest.approx(0.0004 / CREDIT_USD)
    assert rows[0].conversation_id == uuid.UUID(rid)
    assert balance.credits_used == pytest.approx(0.0004 / CREDIT_USD)


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


# ---------- wait for the subject of unread attachments ----------


@pytest.mark.parametrize("reason", ["message", "idle", "signal"])
@pytest.mark.parametrize("earlier_reply", [False, True])
def test_an_unread_attachment_waits_for_its_subject(
    client, alice, gateway, reason, earlier_reply
):
    """Only context after the unread attachment may explain its subject."""
    rid = _unnamed(client, alice)
    if earlier_reply:
        _say(client, rid, "你好，我在，请发一下截图。", author="cheese-c82aeb40555a")
    _say(client, rid, "这什么鬼<@cheese-c82aeb40555a>还活着吗")
    _say(
        client,
        rid,
        "uploads/screenshot.png",
        kind=BlockKind.attachment,
        mime_type="image/png",
    )
    gateway["answers"].append({"keep": False, "title": "cheese-c82aeb40555a"})

    assert _run(client, rid, reason) is None
    assert _shown(client, rid) == ("新任务", "placeholder")
    assert _history(client, rid) == []

    _say(
        client,
        rid,
        "截图中上面那轮跑了 21 小时还是 0 步，卡住了。",
        author="cheese-c82aeb40555a",
    )
    gateway["answers"][:] = [{"keep": False, "title": "卡住的轮次排查"}]
    renamed = _run(client, rid, "idle")

    assert renamed is not None and renamed.title == "卡住的轮次排查"
    assert _shown(client, rid) == ("卡住的轮次排查", "auto")


def test_an_image_only_opener_can_be_named_after_an_explanation(client, alice, gateway):
    rid = _unnamed(client, alice)
    _say(
        client,
        rid,
        "uploads/screenshot.png",
        kind=BlockKind.attachment,
        mime_type="image/png",
    )
    assert _run(client, rid, "idle") is None

    _say(
        client,
        rid,
        "截图中是轮次卡住了，21 小时还没走第一步。",
        author="cheese-c82aeb40555a",
    )
    gateway["answers"].append({"keep": False, "title": "卡住的轮次排查"})

    assert _run(client, rid, "idle").title == "卡住的轮次排查"
    assert _shown(client, rid) == ("卡住的轮次排查", "auto")


def test_a_person_can_supply_the_missing_attachment_context(client, alice, gateway):
    rid = _unnamed(client, alice)
    _say(client, rid, "这张图怎么回事？")
    _say(
        client,
        rid,
        "uploads/screenshot.png",
        kind=BlockKind.attachment,
        mime_type="image/png",
    )
    gateway["answers"].append({"keep": False, "title": "这张图"})
    assert _run(client, rid, "message") is None
    assert _shown(client, rid) == ("新任务", "placeholder")
    assert _history(client, rid) == []
    _say(client, rid, "轮次跑了 21 小时还是 0 步，帮我排查卡在哪里。")
    gateway["answers"][:] = [{"keep": False, "title": "卡住的轮次排查"}]

    assert _run(client, rid, "message").title == "卡住的轮次排查"


def test_a_model_can_wait_for_more_context_without_losing_first_naming(
    client, alice, gateway
):
    rid = _unnamed(client, alice)
    _say(client, rid, "你看这又是怎么了呀")
    gateway["answers"].append({"keep": True, "title": ""})
    assert _run(client, rid, "message") is None
    assert _history(client, rid) == []

    _say(client, rid, "上面那轮跑了 21 小时还是 0 步，查查卡在哪里。")
    gateway["answers"].append({"keep": False, "title": "卡住的轮次排查"})
    assert _run(client, rid, "message").title == "卡住的轮次排查"


def test_a_product_name_is_not_rejected_for_matching_someones_account(
    client, alice, gateway
):
    seed_user(client, "Redis")
    rid = _unnamed(client, alice)
    _say(client, rid, "给缓存数据库 Redis 建一个知识页")
    gateway["answers"].append({"keep": False, "title": "Redis"})

    assert _run(client, rid, "message").title == "Redis"


def test_a_turned_task_keeps_the_unread_attachment_context(client, alice, gateway):
    rid = _turned_from(client, alice, "这什么鬼，还活着吗", attachment=True)
    gateway["answers"].append({"keep": False, "title": "cheese-c82aeb40555a"})
    assert _run(client, rid, "message") is None
    assert _shown(client, rid) == ("新任务", "placeholder")

    _say(
        client,
        rid,
        "截图上那轮卡住了，21 小时还是 0 步。",
        author="cheese-c82aeb40555a",
    )
    gateway["answers"][:] = [{"keep": False, "title": "卡住的轮次排查"}]
    assert _run(client, rid, "idle").title == "卡住的轮次排查"
    assert "image/png" in gateway["asked"][-1]
    assert "uploads/screenshot.png" not in gateway["asked"][-1]


def test_the_document_can_explain_an_unread_attachment(client, alice, gateway):
    rid = _unnamed(client, alice)
    _say(
        client,
        rid,
        "uploads/screenshot.png",
        kind=BlockKind.attachment,
        mime_type="image/png",
    )
    assert _run(client, rid, "signal") is None
    document = document_of(client, rid, headers=alice)
    response = client.put(
        f"/documents/{document}",
        json={
            "content": "截图中那轮跑了 21 小时仍是 0 步，要查清卡在哪里。",
            "expected_version": 0,
        },
        headers=alice,
    )
    assert response.status_code == 200, response.text
    gateway["answers"].append({"keep": False, "title": "卡住的轮次排查"})

    assert _run(client, rid, "signal").title == "卡住的轮次排查"


# ---------- a title that fits the sidebar ----------


def test_a_title_too_wide_goes_back_to_the_model_with_its_width(client, alice, gateway):
    """The sidebar draws a title on one line; one wider than that is not
    written. The model is told how wide it was, and its shorter title is the
    one the task gets."""
    rid = _unnamed(client, alice)
    _say(client, rid, "仓库巡检报表要能自动导出，周报邮件的模板也要统一")
    gateway["answers"] += [
        {"keep": False, "title": "仓库巡检报表自动导出与周报邮件模板"},
        {"keep": False, "title": "巡检报表与周报模板"},
    ]

    renamed = _run(client, rid, "message")

    assert renamed is not None and renamed.title == "巡检报表与周报模板"
    assert _shown(client, rid) == ("巡检报表与周报模板", "auto")
    assert _history(client, rid) == [("巡检报表与周报模板", "auto", "name")]
    assert len(gateway["asked"]) == 2
    told = gateway["asked"][1]
    assert str(title_width("仓库巡检报表自动导出与周报邮件模板")) in told
    assert str(TITLE_WIDTH) in told


def test_a_title_that_never_fits_keeps_the_previous_name(client, alice, gateway):
    """Failed rewrites must not replace a complete name with a cut prefix."""
    rid = _named(client, alice, gateway)
    before = _shown(client, rid)
    history = _history(client, rid)
    gateway["answers"] += [{"keep": False, "title": "lz123y 与 2026201782 显示"}] * 10

    assert _run(client, rid, "idle") is None
    assert _shown(client, rid) == before
    assert _history(client, rid) == history


@pytest.mark.parametrize(
    "retry",
    [
        {"keep": True, "title": "仓库巡检报表自动导出与周报邮件模板"},
        httpx.Response(503, json={"error": "no upstream"}),
        "not a verdict",
    ],
)
def test_a_failed_rewrite_leaves_an_unnamed_task_unnamed(client, alice, gateway, retry):
    """No valid replacement means no partial name or title-history entry."""
    rid = _unnamed(client, alice)
    _say(client, rid, "仓库巡检报表要能自动导出，周报邮件的模板也要统一")
    gateway["answers"] += [
        {"keep": False, "title": "仓库巡检报表自动导出与周报邮件模板"},
        retry,
    ]

    assert _run(client, rid, "message") is None
    assert _shown(client, rid) == ("新任务", "placeholder")
    assert _history(client, rid) == []
    # The next trigger can still give this unnamed task a complete name.
    gateway["answers"].append({"keep": False, "title": "巡检报表与周报模板"})
    assert _run(client, rid, "message").title == "巡检报表与周报模板"


@pytest.mark.parametrize("previous", ["季度预算编制流程说明 Word 文档"])
def test_an_old_unusable_name_is_not_cut_when_the_model_keeps_it(
    client, alice, gateway, previous
):
    """An existing name is left whole when no better name comes back."""
    rid = _named(client, alice, gateway)
    _set(client, rid, title=previous)
    gateway["answers"] += [
        {"keep": True, "title": previous},
        {"keep": True, "title": previous},
    ]

    assert _run(client, rid, "idle") is None
    assert _row(client, rid).title == previous


@pytest.mark.parametrize("previous", ["季度预算编制流程说明 Word 文档"])
def test_an_old_unusable_name_can_be_rewritten_whole(client, alice, gateway, previous):
    rid = _named(client, alice, gateway)
    _set(client, rid, title=previous)
    gateway["answers"] += [
        {"keep": True, "title": previous},
        {"keep": False, "title": "预算文档与名字显示"},
    ]

    renamed = _run(client, rid, "idle")

    assert renamed is not None and renamed.title == "预算文档与名字显示"
    assert _row(client, rid).title == "预算文档与名字显示"


def test_asking_again_stays_inside_the_naming_timeout(
    client, alice, gateway, monkeypatch
):
    """Too little time to rewrite must leave the task's title unchanged."""
    monkeypatch.setattr(settings, "topic_naming_timeout_seconds", 1.0)
    rid = _unnamed(client, alice)
    _say(client, rid, "仓库巡检报表要能自动导出，周报邮件的模板也要统一")
    gateway["answers"] += [
        {"keep": False, "title": "仓库巡检报表自动导出与周报邮件模板"},
        {"keep": False, "title": "巡检报表与周报模板"},
    ]

    renamed = _run(client, rid, "message")

    assert renamed is None
    assert _shown(client, rid) == ("新任务", "placeholder")
    assert _history(client, rid) == []
    assert len(gateway["asked"]) == 1


def test_the_first_turn_checks_the_name_once(client, alice, gateway):
    rid = _named(client, alice, gateway)
    assert _row(client, rid).title_calibrated is False

    # Still right: kept, and the task counts as calibrated.
    gateway["answers"].append({"keep": True, "title": "dev 外网访问慢排查"})
    assert _run(client, rid, "idle") is None
    row = _row(client, rid)
    assert row.title == "dev 外网访问慢排查" and row.title_calibrated is True

    # Calibration does not run twice; a later turn with no signal asks nothing.
    asked = len(gateway["asked"])
    assert _run(client, rid, "idle") is None
    assert len(gateway["asked"]) == asked


def test_a_calibration_that_renames_does_so_quietly(client, alice, gateway):
    """The new title shows where the old one did; nothing is said in the
    task's conversation about it."""
    rid = _named(client, alice, gateway)
    # A task made already started says so in its own conversation, so this one
    # is not empty; what is pinned here is that the rename adds nothing to it.
    said = [b.id for b in _events(client, rid)]
    gateway["answers"].append({"keep": False, "title": "Valkey 连接池耗尽"})
    renamed = _run(client, rid, "idle")
    assert renamed is not None and renamed.previous == "dev 外网访问慢排查"
    assert _shown(client, rid) == ("Valkey 连接池耗尽", "auto")
    assert [b.id for b in _events(client, rid)] == said
    assert _history(client, rid)[-1] == ("Valkey 连接池耗尽", "auto", "calibrate")


def test_following_up_needs_a_signal_and_respects_the_throttle(client, alice, gateway):
    rid = _named(client, alice, gateway)
    gateway["answers"].append({"keep": True, "title": "dev 外网访问慢排查"})
    _run(client, rid, "idle")
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
    said = [b.id for b in _events(client, rid)]
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
    assert [b.id for b in _events(client, rid)] == said


def test_a_persons_name_is_never_overwritten(client, alice, gateway):
    rid = _named(client, alice, gateway)
    r = client.post(f"/topics/{rid}/title", json={"title": "我的名字"}, headers=alice)
    assert r.status_code == 200 and _shown(client, rid) == ("我的名字", "human")
    asked = len(gateway["asked"])
    for reason in ("message", "idle", "signal"):
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


def test_a_task_a_teammate_created_keeps_its_title_until_its_direction_changes(
    client, alice, gateway
):
    """The AI teammate that creates a task names it, and the platform does not
    second-guess it once its session first goes idle; only a change of direction does."""
    tid = _by_teammate(client, alice, "迁移旧数据")
    assert _shown(client, tid) == ("迁移旧数据", "auto")
    _say(client, tid, "先从用户表开始")
    for reason in ("message", "idle"):
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
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=project['id'],
                topic_id=tid,
                agent_handle=room_agent_seat(client, room),
            )
        }"
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


@pytest.mark.parametrize("recover_publication", [False, True])
def test_publishing_an_attachment_explanation_names_the_task_before_idle(
    client, alice, gateway, monkeypatch, recover_publication
):
    """A published explanation names the task without waiting for session idle."""
    project = _project(client, alice)
    room = project["root_topic_id"]
    rid = _task(client, alice, room)
    headers = task_agent_headers(project["id"], rid, room_agent_seat(client, room))
    _say(client, rid, "我之前的任务怎么突然又成待办了？")
    _say(
        client,
        rid,
        "uploads/screenshot.png",
        kind=BlockKind.attachment,
        mime_type="image/png",
    )
    assert _run(client, rid, "message") is None

    message = {
        "content": "这两条是以前的问题卡，今天重新冒出来成了待办，我去查来源。",
        "request_id": str(uuid.uuid4()),
    }
    first = None
    if recover_publication:
        # The reply was committed, but the process never nudged naming.
        first = client.post(f"/topics/{rid}/messages", json=message, headers=headers)
        assert first.status_code == 200, first.text
    monkeypatch.setattr(naming, "_default_factory", lambda: client.test_request_factory)
    monkeypatch.setattr(naming, "nudge", gateway["nudge"])
    gateway["answers"].append({"keep": False, "title": "旧问题卡重新冒出待办"})

    response = client.post(f"/topics/{rid}/messages", json=message, headers=headers)
    assert response.status_code == 200, response.text
    if first is not None:
        assert response.json()["data"]["id"] == first.json()["data"]["id"]

    async def settle_naming():
        await asyncio.gather(*tuple(naming._tasks))

    client.portal.call(settle_naming)
    assert _shown(client, rid) == ("旧问题卡重新冒出待办", "auto")
    assert _history(client, rid) == [("旧问题卡重新冒出待办", "auto", "name")]


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


def test_a_rename_is_heard_on_the_tasks_own_page_and_in_its_channel(
    client, alice, gateway, monkeypatch
):
    """The task's page listens on the task's conversation and the channel's
    pages on the channel's: a new title reaches both without a reload."""
    from app.domain.agent import live_notices

    heard: list[tuple[str, str, str | None]] = []

    class _Broker:
        """发布出去的那几帧，代替 broker（`agent/live_notices` 在提交后发的）。"""

        async def publish(self, channel, frame):
            heard.append((str(channel), frame.get("resource"), frame.get("id")))

    async def _told(*args, **kwargs):
        pass

    monkeypatch.setattr(live_notices, "get_broker", lambda: _Broker())
    monkeypatch.setattr(live_notices, "tell_project", _told)
    project = _project(client, alice)
    room = project["root_topic_id"]
    tid = _task(client, alice, room)
    _say(client, tid, "帮我排查一下 dev 机器从外网访问很慢的问题")
    gateway["answers"].append({"keep": False, "title": "dev 外网访问慢排查"})
    assert _run(client, tid, "message") is not None
    # 帧是提交之后后台发的，再走一趟 portal 让那个循环把它发出去。
    _shown(client, tid)
    assert (tid, "topics") in [(c, r) for c, r, _ in heard]
    assert (room, "topics") in [(c, r) for c, r, _ in heard]
    # Both frames name the ROOM's row: the sidebar holds rooms, and a task id is
    # not an address (`GET /topics/{task}` is a 404), so a page listening on the
    # task's own conversation must not be told to refetch by the task's id.
    assert (tid, "topics", room) in heard
    assert (room, "topics", room) in heard
