"""记忆整理 (dreaming) — issue #187 step 4.

Memory only accumulates. Nothing in the platform ever merges two facts that say
the same thing, replaces 「上周」 with a date, or removes a fact the work has
since disproved — so a project's pools drift toward being long, contradictory
and half-wrong, which is exactly the state in which injection silently drops the
oldest half of them.

This is the pass that fixes that, and it runs **on the machine**, in the sandbox
that is about to be destroyed, because the interesting half of the job is
checking claims against the workspace. A backend job could dedupe strings; only
something holding the code can say a remembered fact is no longer true.

Three properties make it safe to let 芝士 rewrite memory unattended:

- **Nothing is deleted.** Entries are retired (``retired_at``), grouped by the
  pass that retired them (``retired_by``), and the pass's own additions are
  tagged (``created_by``). :func:`revert_dream` undoes one pass as a unit.
- **The organizer yields, never the writer.** Every referenced entry is re-read
  at apply time and left completely alone if it moved after the snapshot the
  proposal was computed from. A fact remembered *while* 芝士 was thinking is a
  fact nobody proposed anything about; the proposal simply misses it, and says
  so in ``skipped``. Deliberately no row locks: a turn's ``remember`` must never
  block on housekeeping, and the gap this leaves is a write landing between the
  re-read and the commit — microseconds, against a proposal that took minutes,
  and only for a fact being EDITED rather than added (adding is what ``remember``
  does). Buying that window back would cost the property above.
- **A pass reaches only its own pools.** The proposal names entries by id, and
  an id is a global handle — so every reference is checked against the two pools
  this topic's 芝士 actually reads (its own ``agent_project`` pool, and the
  project's legacy shared pool). Without that, one topic could retire another
  project's memory by guessing a UUID.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.models import (
    MemoryDream,
    MemoryEntry,
    MemoryLayer,
    MemoryScope,
    agent_project_scope_id,
)

# Merging across layers keeps the HIGHEST one: a fact that was injected
# unconditionally must not lose that seat because it was consolidated with a
# lesser one. Demotion is a curation decision for a human, not a side effect of
# tidying — and it would be a silent one.
_LAYER_RANK = {MemoryLayer.fact: 0, MemoryLayer.core: 1}

# What the pass is asked to do, on the machine, with the workspace in front of
# it. Kept here as one constant rather than assembled at the call site: it is
# the whole specification of the feature that lives outside the code, and a
# prompt split across three f-strings is a prompt nobody rereads.
DREAM_PROMPT = """【记忆整理】这个话题的沙箱马上要被回收了。在它消失之前，把这段工作里\
真正值得留下的东西整理进项目记忆——**这一轮不写代码，只整理记忆**。

按这四步做：

1. **先读现有记忆。** `cheese api GET "/memory?project_id=$CHEESE_PROJECT"` 列出\
现有条目，记下每条的 `id`。**把返回里最大的那个 `updated_at` 记下来，它就是下面要用的\
`snapshot_at`**——用返回里的时间，不要用容器自己的当前时间（两个时钟不一定对得上）。\
它是并发保护的依据：你提交提案的时候，凡是在这个时间之后被别人改过的条目，\
平台会自动跳过不动，不会把别人刚写的东西盖掉。

2. **翻这个话题的记录。** 看对话里被纠正过的说法、定下来的约定、重复踩到的坑，\
以及 `~/.claude` 下自动记的东西。

3. **对着工作区的代码验证。** 提不出证据的不要写进记忆——这是整理必须在这台机器上做、\
不能在后端做的全部理由。记忆里已有但你在代码里核实为**已经不成立**的，也一并挑出来。

4. **提交提案。** 合并同义重复的、把「上周/昨天」这类相对时间换成绝对日期、\
把互相矛盾的两条解掉，然后：

```
cheese api POST "/topics/$CHEESE_TOPIC/memory/dream" --data '{
  "snapshot_at": "<第 1 步记下的时间，ISO 8601>",
  "merges": [{"replaces": ["<id>", "<id>"], "content": "合并后的一条"}],
  "drops": ["<已经不成立的条目 id>"],
  "adds": ["<这次新收割到的事实>"],
  "summary": "一句话说明这次整理做了什么"
}'
```

三个字段都可以是空数组。返回里的 `skipped` 是被并发写入挡掉、这次没动的条目——\
**不要重试它们**，下次整理会再看到。

最后在房间里留一句人话：合并了几条、退休了几条、新增了几条，分别是什么。\
记忆被改了必须有人看得见，不许静默改。"""


@dataclass
class DreamResult:
    """What one pass actually did — the receipt 芝士 reads back."""

    dream_id: uuid.UUID
    retired: int = 0
    added: int = 0
    # One entry per reference the pass declined to touch, with why. This is the
    # only way 芝士 learns that a proposal it spent a turn computing was partly
    # overtaken; silence here would read as "all applied".
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dream_id": str(self.dream_id),
            "applied": True,
            "retired": self.retired,
            "added": self.added,
            "skipped": self.skipped,
        }


def _aware(value: datetime | None) -> datetime | None:
    """TIMESTAMPTZ columns come back naive from some drivers; comparing a naive
    value against an aware one raises rather than merely being wrong."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def open_dream(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    turn_id: uuid.UUID | None = None,
) -> MemoryDream:
    """Record that a pass has been STARTED for this topic.

    Called by the reaper the moment it fires the turn, not when the proposal
    lands. A pass that dies — refused on credits, killed by a deploy, or simply
    finding nothing worth saying — must still leave a mark, or every sweep from
    then on sees a topic that has never been organized and starts another one.
    """
    dream = MemoryDream(
        topic_id=topic_id, project_id=project_id, turn_id=turn_id, applied=False
    )
    session.add(dream)
    await session.flush()
    return dream


async def latest_dream(
    session: AsyncSession, topic_id: uuid.UUID
) -> MemoryDream | None:
    """The most recent pass recorded for this topic, applied or not."""
    return await session.scalar(
        select(MemoryDream)
        .where(MemoryDream.topic_id == topic_id)
        .order_by(MemoryDream.created_at.desc())
        .limit(1)
    )


async def _pending_dream(
    session: AsyncSession, topic_id: uuid.UUID
) -> MemoryDream | None:
    """The open row this proposal belongs to, if the reaper started one."""
    return await session.scalar(
        select(MemoryDream)
        .where(MemoryDream.topic_id == topic_id, MemoryDream.applied.is_(False))
        .order_by(MemoryDream.created_at.desc())
        .limit(1)
    )


class _Claimer:
    """Resolves proposal ids into entries this pass is allowed to move.

    Every rejection is recorded rather than raised: a proposal is a batch, and
    one stale id must not throw away the other twenty edits 芝士 spent a turn
    working out.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        allowed: set[tuple[MemoryScope, str]],
        snapshot_at: datetime | None,
        skipped: list[dict],
    ):
        self._session = session
        self._allowed = allowed
        self._snapshot_at = _aware(snapshot_at)
        self._skipped = skipped

    def skip(self, entry_id: str, reason: str) -> None:
        self._skipped.append({"entry": entry_id, "reason": reason})

    async def claim(self, raw_id: object) -> MemoryEntry | None:
        entry_id = str(raw_id or "").strip()
        try:
            parsed = uuid.UUID(entry_id)
        except ValueError:
            self.skip(entry_id, "不是合法的记忆 id")
            return None
        # populate_existing: the row must come from the DB as it is NOW, not
        # from whatever this session loaded earlier. A proposal is minutes old
        # by the time it lands, so reading a cached copy would compare the
        # snapshot against itself and pass every time.
        entry = await self._session.get(MemoryEntry, parsed, populate_existing=True)
        if entry is None:
            self.skip(entry_id, "记忆不存在")
            return None
        if (entry.scope, entry.scope_id) not in self._allowed:
            # Not "forbidden" as an error: an id from another pool is not
            # something 芝士 could have seen, so it is a mistake, not an attack
            # — and either way the answer is the same, leave it alone.
            self.skip(entry_id, "不属于这个话题能整理的记忆池")
            return None
        if entry.retired_at is not None:
            self.skip(entry_id, "已经被退休了")
            return None
        if self._snapshot_at is not None:
            updated = _aware(entry.updated_at)
            if updated is not None and updated > self._snapshot_at:
                self.skip(entry_id, "在快照之后被改过，这次不动它")
                return None
        return entry


async def dream_pools(
    session: AsyncSession, topic_id: uuid.UUID
) -> tuple[uuid.UUID, tuple[MemoryScope, str], set[tuple[MemoryScope, str]]]:
    """The pools one topic's pass may reorganize, and where its additions go.

    Exactly the pair injection reads (see ``recall_pools`` call sites in
    agent/chat.py): this 芝士's own ``agent_project`` pool, plus the project's
    legacy shared pool. Organizing anything it does not read would be organizing
    memory it cannot check.
    """
    from app.domain.topic.services import TopicService
    from app.domain.topic_membership.services import TopicMemberService

    topic = await TopicService(session).get_or_404(topic_id)
    handle = await TopicMemberService(session).resolve_agent_handle(topic.id)
    own = (
        MemoryScope.agent_project,
        agent_project_scope_id(topic.project_id, handle),
    )
    shared = (MemoryScope.project, str(topic.project_id))
    return topic.project_id, own, {own, shared}


async def apply_dream(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
    snapshot_at: datetime | None,
    merges: list[dict],
    drops: list,
    adds: list,
    summary: str = "",
) -> DreamResult:
    """Land one proposal. The caller owns the transaction — commit once.

    All three edit kinds share the snapshot check, so a pass is atomic in the
    sense that matters: it never half-retires a merge, and it never applies an
    edit computed against a fact that has since changed.
    """
    project_id, own_pool, allowed = await dream_pools(session, topic_id)

    dream = await _pending_dream(session, topic_id)
    if dream is None:
        # No open row: the pass was started by hand rather than by the reaper.
        dream = await open_dream(
            session, topic_id=topic_id, project_id=project_id, turn_id=None
        )
    dream.snapshot_at = _aware(snapshot_at)
    dream.summary = summary.strip()
    dream.applied = True

    result = DreamResult(dream_id=dream.id)
    claimer = _Claimer(
        session, allowed=allowed, snapshot_at=snapshot_at, skipped=result.skipped
    )
    now = datetime.now(UTC)

    def retire(entry: MemoryEntry) -> None:
        entry.retired_at = now
        entry.retired_by = dream.id
        result.retired += 1

    for merge in merges:
        content = str((merge or {}).get("content") or "").strip()
        replaces = list((merge or {}).get("replaces") or [])
        if not content:
            for raw in replaces:
                claimer.skip(str(raw), "合并后的内容是空的")
            continue
        claimed = [e for e in [await claimer.claim(raw) for raw in replaces] if e]
        if not claimed:
            # Every source was stale, missing or out of reach. Writing the
            # merged text anyway would ADD a fact while retiring nothing — the
            # duplicate this pass exists to remove.
            continue
        # The merged fact stays in the pool AND the layer the originals lived
        # in. Moving a shared-pool fact into one agent's private pool would make
        # it vanish for every other 芝士 in the project; demoting a `core` fact
        # to `fact` would drop it out of the layer that is injected
        # unconditionally. Both are a visible loss dressed up as tidying, and
        # neither would raise anything.
        session.add(
            MemoryEntry(
                scope=claimed[0].scope,
                scope_id=claimed[0].scope_id,
                layer=max((e.layer for e in claimed), key=_LAYER_RANK.__getitem__),
                content=content,
                created_by=dream.id,
            )
        )
        result.added += 1
        for entry in claimed:
            retire(entry)

    for raw in drops:
        entry = await claimer.claim(raw)
        if entry is not None:
            retire(entry)

    for raw in adds:
        content = str(raw or "").strip()
        if not content:
            continue
        # New facts land in this 芝士's own pool — the same place `cheese
        # remember` puts them, so nothing about where memory lives depends on
        # whether a human or a pass wrote it.
        session.add(
            MemoryEntry(
                scope=own_pool[0],
                scope_id=own_pool[1],
                content=content,
                created_by=dream.id,
            )
        )
        result.added += 1

    await session.flush()
    return result


async def revert_dream(session: AsyncSession, dream_id: uuid.UUID) -> dict:
    """Undo one pass: put back what it retired, retire what it added.

    Idempotent through ``applied`` — reverting twice is a no-op rather than a
    resurrection of the additions.
    """
    dream = await session.get(MemoryDream, dream_id)
    if dream is None or not dream.applied:
        return {"reverted": False, "restored": 0, "retired": 0}

    restored = (
        await session.scalars(
            select(MemoryEntry).where(MemoryEntry.retired_by == dream_id)
        )
    ).all()
    for entry in restored:
        entry.retired_at = None
        entry.retired_by = None

    now = datetime.now(UTC)
    added = (
        await session.scalars(
            select(MemoryEntry).where(
                MemoryEntry.created_by == dream_id, MemoryEntry.retired_at.is_(None)
            )
        )
    ).all()
    for entry in added:
        # `retired_by` stays null on purpose: tagging these with the pass that
        # is being undone would make the next revert of the same pass bring them
        # back, which is the opposite of what it was asked to do.
        entry.retired_at = now

    dream.applied = False
    await session.flush()
    return {"reverted": True, "restored": len(restored), "retired": len(added)}
