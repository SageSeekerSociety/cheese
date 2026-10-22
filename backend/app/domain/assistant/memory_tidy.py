"""Tidying what a person's own 芝士 remembers of them, now and then.

A project's memory is tidied by a whole session of its 芝士 working on the
files (``memory.dream``). A person's memory has no file tree in its session, and
is small, so it is tidied with one model call instead: the memories go in, and
what comes back is what to change — merge several into one, delete one that is
outdated, rewrite one that is unclear — or nothing. The platform checks every
change, applies them together through ``assistant.memory`` (so the index stays
in step) or none of them, and never adds a fact the memories did not hold.

Only the memories are read, never the person's conversations: those hold
channel messages and web pages, which are not the person's words. A person who
turned memory off is not tidied, since tidying writes. A tidy is charged to
the person, as a project's is to the project; one out of credits waits.

Like the project's dream, a tidy that would remove too much is refused whole
(``tree.BULK_DELETE_*``), and a tidy cannot be undone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import model_roles
from app.domain.assistant import gate
from app.domain.assistant import memory as assistant_memory
from app.domain.assistant.models import AssistantRules
from app.domain.gateway_chat import GatewayCallError, GatewayChat
from app.domain.memory.files import INDEX_NAME, MemoryFile, MemoryFileError
from app.domain.memory.files_store import MemoryFileConflict, MemoryFileLimit
from app.domain.memory.models import MemoryFileRecord
from app.domain.memory.tree import BULK_DELETE_MIN, BULK_DELETE_RATIO
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.ledger import Ledger, payer_for_person
from app.domain.user.models import User

logger = logging.getLogger(__name__)

#: The usage rows a tidy is written under.
KIND = "assistant_memory_tidy"
#: Fewer memories than this are read at a glance; nothing to tidy.
AT_LEAST = 8
#: Between two tidies of one person's memory: it changes in conversations,
#: not by the hour.
EVERY = timedelta(hours=24)
#: People tidied in one sweep: the sweep runs hourly, and the rest wait.
PER_SWEEP = 20
#: How long the call may take, and how much it may say.
TIDY_S = 60.0
TIDY_TOKENS = 4000

POLICY = """你整理芝士关于一位用户的记忆。只做三种改动：
- merge：几条说的是同一件事，合成一条（保留 into 那条的名字，from 里的几条删掉）。只合并同一件事：对不同的人、不同场合的要求（如「给老师写东西用书面语」和「回同学可以随意」）是不同的事，不合并。
- delete：一条已经被另一条更新的取代（例如日期改了），删掉旧的那条，by 写取代它的那一条的名字；那一条必须保留。
- rewrite：一条含糊、啰嗦，改写得清楚简短，意思不变。

不要新增输入里没有的事实；拿不准的不动；没有要整理的就返回空列表。描述一句话，不超过 60 字；正文写结论和它为什么成立，不超过 400 字。

只输出 JSON：{"ops": [...]}，每个改动是下面之一：
{"op": "merge", "into": "名字", "from": ["名字", ...], "description": "一句话", "text": "正文"}
{"op": "delete", "name": "名字", "by": "取代它的那一条的名字"}
{"op": "rewrite", "name": "名字", "description": "一句话", "text": "正文"}"""


@dataclass
class Plan:
    """What a tidy will change, checked: memories to write, names to delete."""

    write: list[MemoryFile] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)


class Refused(ValueError):
    """The model's answer is not a tidy the platform will apply."""


def _memories_text(entries: list[assistant_memory.Entry], today: str) -> str:
    lines = [f"（今天是 {today}）", ""]
    for e in entries:
        m = e.memory
        lines += [
            f"名字：{m.name}（{m.type.value}，{e.updated_at[:10]} 改过）",
            f"描述：{m.description}",
            f"正文：{m.body}",
            "",
        ]
    return "\n".join(lines)


def plan(entries: list[assistant_memory.Entry], answer: str) -> Plan:
    """The model's answer as a checked plan; ``Refused`` when any part of it
    is malformed, names a memory that is not there, both keeps and removes
    one, or would remove too much. A memory written twice keeps the last
    writing (a merge, then a rewrite of what it merged into)."""
    text = answer.strip()
    if text.startswith("```"):
        # Asked for JSON, a weak model still fences it now and then.
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        ops = json.loads(text).get("ops")
    except (ValueError, AttributeError) as exc:
        raise Refused("not JSON") from exc
    if not isinstance(ops, list):
        raise Refused("no ops")
    have = {e.memory.name: e.memory for e in entries}
    written: dict[str, MemoryFile] = {}
    deleted: dict[str, None] = {}
    #: What each deleted memory was replaced by, which must stay.
    replaced_by: dict[str, str] = {}

    def claim(name: object) -> str:
        if not isinstance(name, str) or name not in have:
            raise Refused(f"unknown memory {name!r}")
        return name

    def rewritten(name: str, op: dict) -> MemoryFile:
        try:
            return assistant_memory.made(
                name,
                str(op.get("description") or ""),
                have[name].type.value,
                str(op.get("text") or ""),
            )
        except MemoryFileError as exc:
            raise Refused(str(exc)) from exc

    for op in ops:
        if not isinstance(op, dict):
            raise Refused("an op is not an object")
        kind = op.get("op")
        if kind == "delete":
            name = claim(op.get("name"))
            by = claim(op.get("by"))
            if by == name:
                raise Refused(f"{name} replaced by itself")
            deleted[name] = None
            replaced_by[name] = by
        elif kind == "rewrite":
            name = claim(op.get("name"))
            written[name] = rewritten(name, op)
        elif kind == "merge":
            into = claim(op.get("into"))
            merged = op.get("from")
            if not isinstance(merged, list) or not merged:
                raise Refused("a merge with nothing to merge")
            for name in merged:
                deleted[claim(name)] = None
            written[into] = rewritten(into, op)
        else:
            raise Refused(f"unknown op {kind!r}")
    both = set(written) & set(deleted)
    if both:
        raise Refused(f"{sorted(both)} both kept and removed")
    # What replaces a deleted memory has to be there after the tidy: else a
    # fact is gone with nothing standing for it.
    lost = sorted(n for n, by in replaced_by.items() if by in deleted)
    if lost:
        raise Refused(f"{lost} replaced by a memory that is removed too")
    out = Plan(write=list(written.values()), delete=list(deleted))
    gone = len(out.delete)
    if gone > len(entries) * BULK_DELETE_RATIO and gone > BULK_DELETE_MIN:
        raise Refused(f"would remove {gone} of {len(entries)}")
    return out


async def due(session: AsyncSession, now: datetime) -> list[tuple[int, str]]:
    """The people whose memory is worth tidying now, as (user id, handle):
    enough of it, changed since their last tidy, which was long enough ago,
    and memory on."""
    rows = (
        select(
            MemoryFileRecord.owner_handle.label("handle"),
            func.count().label("n"),
            func.max(MemoryFileRecord.updated_at).label("changed"),
        )
        .where(
            MemoryFileRecord.project_id.is_(None),
            MemoryFileRecord.scope == assistant_memory.SCOPE,
            MemoryFileRecord.path != INDEX_NAME,
        )
        .group_by(MemoryFileRecord.owner_handle)
        .subquery()
    )
    found = await session.execute(
        select(User.id, User.username)
        .join(rows, rows.c.handle == User.username)
        .outerjoin(AssistantRules, AssistantRules.user_id == User.id)
        .where(
            rows.c.n >= AT_LEAST,
            func.coalesce(AssistantRules.memory, True).is_(True),
            (AssistantRules.memory_tidied_at.is_(None))
            | (
                (AssistantRules.memory_tidied_at < now - EVERY)
                & (rows.c.changed > AssistantRules.memory_tidied_at)
            ),
        )
        .limit(PER_SWEEP)
    )
    return [(user_id, handle) for user_id, handle in found.all()]


async def claim(session: AsyncSession, user_id: int, now: datetime) -> bool:
    """Take this person's tidy for now; False when another has it or it is
    not yet time. Moved forward whether the tidy then works or not, so a
    tidy that is refused is not tried again every sweep."""
    await session.execute(
        insert(AssistantRules)
        .values(
            user_id=user_id,
            ask=list(gate.DEFAULT_ASK),
            allow_text="",
            ask_text="",
        )
        .on_conflict_do_nothing(index_elements=[AssistantRules.user_id])
    )
    taken = await session.execute(
        update(AssistantRules)
        .where(
            AssistantRules.user_id == user_id,
            (AssistantRules.memory_tidied_at.is_(None))
            | (AssistantRules.memory_tidied_at < now - EVERY),
        )
        .values(memory_tidied_at=now)
    )
    return bool(taken.rowcount)  # type: ignore[attr-defined]


async def tidy(
    session: AsyncSession,
    user_id: int,
    handle: str,
    *,
    now: datetime,
    chat: GatewayChat | None = None,
) -> str:
    """Tidy one person's memory; what happened, in a word. Commits."""
    if not await claim(session, user_id, now):
        await session.commit()
        return "taken"
    await session.commit()
    payer = await payer_for_person(session, user_id)
    if await Ledger(session).admit(payer) is not None:
        await session.commit()
        return "no_credits"
    entries = await assistant_memory.entries(session, handle)
    if len(entries) < AT_LEAST:
        return "small"
    if chat is None:
        chat = await gate.gatekeeper(session)
        if chat is None:
            return "no_model"
    try:
        said = await chat.complete(
            system=POLICY,
            prompt=_memories_text(entries, now.date().isoformat()),
            timeout=TIDY_S,
            max_tokens=TIDY_TOKENS,
            json_response=True,
            extra={"thinking": {"type": "disabled"}},
        )
    except GatewayCallError:
        return "failed"
    await Ledger(session).record(
        payer,
        credits=said.cost_usd / CREDIT_USD,
        model=model_roles.model("assistant_model"),
        input_tokens=said.usage.prompt_tokens,
        output_tokens=said.usage.completion_tokens,
        cache_read_tokens=said.usage.cache_read_tokens,
        cost_usd=said.cost_usd,
        route="gateway",
        kind=KIND,
        user_id=user_id,
    )
    await session.commit()
    try:
        changes = plan(entries, said.content)
    except Refused as exc:
        logger.info("memory tidy refused user=%s: %s", user_id, exc)
        return "refused"
    try:
        for memory in changes.write:
            await assistant_memory.save(session, handle, memory, by=handle)
        for name in changes.delete:
            await assistant_memory.put(
                session, handle, assistant_memory.path_of(name), None, by=handle
            )
    except (MemoryFileConflict, MemoryFileLimit) as exc:
        # The person's 芝士 wrote meanwhile, or a rewrite is over a limit:
        # nothing of this tidy is kept; the next one starts from what is there.
        await session.rollback()
        logger.info("memory tidy dropped user=%s: %s", user_id, exc)
        return "conflict"
    await session.commit()
    logger.info(
        "memory tidy user=%s wrote=%s deleted=%s",
        user_id,
        len(changes.write),
        len(changes.delete),
    )
    return "tidied" if changes.write or changes.delete else "nothing"


async def sweep(sessions: async_sessionmaker[AsyncSession]) -> dict:
    """Tidy the memory of everyone due; how many ended each way."""
    now = datetime.now(UTC)
    async with sessions() as session:
        people = await due(session, now)
    counts: dict[str, int] = {}
    for user_id, handle in people:
        async with sessions() as session:
            result = await tidy(session, user_id, handle, now=now)
        counts[result] = counts.get(result, 0) + 1
    return counts
