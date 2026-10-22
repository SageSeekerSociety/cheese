"""A person's memory is tidied now and then: duplicates merged, what was
replaced removed, and nothing applied when the model's answer is not a tidy
the platform can trust."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.domain.assistant import memory as assistant_memory
from app.domain.assistant import memory_tidy
from app.domain.assistant.models import AssistantRules
from app.domain.gateway_chat import Completion, Usage
from app.domain.usage.models import ResourceUsage
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_assistant_memory import _auth, _memory

MEMORIES = [
    ("formal-to-teachers", "给老师写东西用书面语", "feedback", "不用「哈」「呀」。"),
    ("formal-writing-teachers", "写给老师的消息要正式", "feedback", "称呼「X 老师」。"),
    ("defense-date", "中期答辩定在 11 月 5 日", "project", "11 月 5 日答辩。"),
    ("defense-moved", "中期答辩改到 11 月 12 日", "project", "改到 11 月 12 日。"),
    ("ta-os", "在带操作系统助教课", "user", "第 3、4 次实验。"),
    ("weekly-report", "周报按三段写", "feedback", "完成 / 问题 / 计划。"),
    ("short-answers", "回答先给结论", "feedback", "简短。"),
    ("thesis", "毕设做视觉问答", "user", "细粒度理解。"),
]

TIDY = {
    "ops": [
        {
            "op": "merge",
            "into": "formal-to-teachers",
            "from": ["formal-writing-teachers"],
            "description": "给老师写东西用书面语、称呼「X 老师」",
            "text": "不用「哈」「呀」，开头称呼「X 老师」。",
        },
        {"op": "delete", "name": "defense-date", "by": "defense-moved"},
    ]
}


class _Model:
    def __init__(self, answer: dict | str) -> None:
        self.answer = answer if isinstance(answer, str) else json.dumps(answer)

    async def complete(self, **kwargs) -> Completion:
        return Completion(self.answer, Usage(900, 120, 0, 0), 0.0002)


def _seed(client, handle: str = "alice") -> int:
    _auth(client, handle)
    user_id = _ledger_user(client, handle)[0]

    async def put():
        async with client.test_factory() as db:
            for name, description, kind, body in MEMORIES:
                await assistant_memory.save(
                    db,
                    handle,
                    assistant_memory.made(name, description, kind, body),
                    by=handle,
                )
            await db.commit()

    asyncio.run(put())
    return user_id


def _tidy(client, user_id: int, answer, *, now: datetime | None = None) -> str:
    async def run():
        async with client.test_factory() as db:
            return await memory_tidy.tidy(
                db, user_id, "alice", now=now or datetime.now(UTC), chat=_Model(answer)
            )

    return asyncio.run(run())


def _names(client) -> list[str]:
    return [i["name"] for i in _memory(client, _auth(client, "alice"))["items"]]


def _db(client, fn):
    async def run():
        async with client.test_factory() as db:
            out = await fn(db)
            await db.commit()
            return out

    return asyncio.run(run())


def test_duplicates_merge_and_what_was_replaced_goes_and_the_person_pays(client):
    user_id = _seed(client)

    assert _tidy(client, user_id, TIDY) == "tidied"

    names = _names(client)
    assert "formal-writing-teachers" not in names and "defense-date" not in names
    assert "defense-moved" in names and len(names) == len(MEMORIES) - 2
    items = {i["name"]: i for i in _memory(client, _auth(client, "alice"))["items"]}
    assert (
        items["formal-to-teachers"]["description"]
        == "给老师写东西用书面语、称呼「X 老师」"
    )

    async def charged(db):
        return await db.scalar(
            select(ResourceUsage.kind).where(ResourceUsage.kind == memory_tidy.KIND)
        )

    assert _db(client, charged) == memory_tidy.KIND


def test_an_answer_naming_a_memory_that_is_not_there_changes_nothing(client):
    user_id = _seed(client)
    bad = {"ops": [*TIDY["ops"], {"op": "delete", "name": "no-such", "by": "ta-os"}]}

    assert _tidy(client, user_id, bad) == "refused"
    assert len(_names(client)) == len(MEMORIES)


def test_a_deletion_must_name_what_replaces_it_and_that_one_stays(client):
    user_id = _seed(client)
    both_dates = {
        "ops": [
            {"op": "delete", "name": "defense-date", "by": "defense-moved"},
            {"op": "delete", "name": "defense-moved", "by": "defense-date"},
        ]
    }

    assert _tidy(client, user_id, both_dates) == "refused"
    assert {"defense-date", "defense-moved"} <= set(_names(client))


def test_a_tidy_that_would_remove_too_much_is_refused(client):
    user_id = _seed(client)
    sweeping = {
        "ops": [
            {"op": "delete", "name": name, "by": "thesis"}
            for name, *_ in MEMORIES
            if name != "thesis"
        ]
    }

    assert _tidy(client, user_id, sweeping) == "refused"
    assert len(_names(client)) == len(MEMORIES)


def test_who_is_due_enough_changed_not_too_soon_and_memory_on(client):
    user_id = _seed(client)
    now = datetime.now(UTC)

    async def due(db):
        return [u for u, _ in await memory_tidy.due(db, now)]

    assert user_id in _db(client, due)
    assert _tidy(client, user_id, {"ops": []}, now=now) == "nothing"
    # Just tidied and nothing changed since: not again.
    assert user_id not in _db(client, due)
    assert _tidy(client, user_id, TIDY, now=now + timedelta(hours=1)) == "taken"

    async def off(db):
        await db.execute(
            update(AssistantRules)
            .where(AssistantRules.user_id == user_id)
            .values(memory=False, memory_tidied_at=None)
        )

    _db(client, off)
    assert user_id not in _db(client, due)
