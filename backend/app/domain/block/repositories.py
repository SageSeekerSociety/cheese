"""Block data access."""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, and_, cast, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.work_context import current_work_id
from app.domain.block.authorship import is_participant, participant_blocks
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    CHECKLIST_META_KEY,
    CONSUMED_TURN_META_KEY,
    EDITED_AT_META_KEY,
    PROMPT_ATTEMPTS_META_KEY,
    PROMPTED_TURN_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    BlockReaction,
    prompt_attempts,
)
from app.domain.block.notice_text import with_keys
from app.domain.identity.handles import agent_handle_column, looks_like_agent_handle


@dataclass(frozen=True)
class BlockPage:
    """A chronological window; has_more follows the requested cursor direction."""

    items: list[Block]
    has_more: bool


@dataclass(frozen=True)
class ReplyWait:
    """一个房间在等 AI：从什么时候开始等，以及为什么多半还没回。

    `reason`：`mention`（有人点了 AI 的名）；卡停在要 AI 修的那几种上时是
    `check` / `conflict` / `rejected` / `gate`（见 `presentation.agent_fix_kind`）；
    或者等待期间最近一条机器/环境事件的类型（`MACHINE_EVENTS`）。`source` 说这段
    等待本来是从哪来的（`mention` / `card`），机器事件只改 `reason` 不改它。
    `pr` 是那张卡的 PR 号，悬停时写出来。
    """

    since: datetime
    reason: str
    source: str = "mention"
    pr: int | None = None


@dataclass(frozen=True)
class StuckCard:
    """一个房间里停在「要 AI 去修」上的那张卡：哪一种，PR 号几。"""

    kind: str
    pr: int | None


class BlockRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        author: str,
        author_type: AuthorType,
        content: str,
        kind: BlockKind = BlockKind.message,
        reply_to: uuid.UUID | None = None,
        struct_parent: uuid.UUID | None = None,
        refs: list[str] | None = None,
        node_type: str | None = None,
        struct_order: float | None = None,
        turn_id: uuid.UUID | None = None,
        anchor_quote: str | None = None,
        mime_type: str | None = None,
        meta: dict | None = None,
        created_at: datetime | None = None,
        own_output: bool = False,
    ) -> Block:
        # Cheese-side handlers don't pass turn_id explicitly; fall back to the
        # ambient turn id set from the X-Cheese-Turn header (R4).
        if turn_id is None:
            turn_id = current_work_id.get()
        # `topic_id` names a room — the only thing a place is. A block that
        # belongs to one of the room's cards says so with `task_id`, which the
        # attribution of a 分身's events supplies explicitly; everything else
        # lands on the room's own line, which is where it is read.
        # Explicit null means "tracked and still pending". Without that marker,
        # legacy compatibility has to infer consumption from the last AI block;
        # a newer, receipted mid-turn message could then move that positional
        # watermark past an older pending attachment and lose it forever.
        #
        # 待读输入 = **某个参与者说的一句话，而且不是哪一轮自己产出的**。
        #
        # 判据的前半截从「作者是人」放宽到「作者是参与者」，不是把条件放松：结论
        # 1 之下另一个参与者说的话同样是这一轮要读的输入，一个 AI 队友在房间里说
        # 的话，对坐在同一个房间里的另一个 agent 是消息，不是背景噪音。
        #
        # 后半截是这次必须新加的：两档一合，芝士**自己这一轮写下的**那条回复也成
        # 了「参与者说的话」，会带上 pending 标记，下一轮再把它当输入喂回去——它对
        # 着自己的上一句话又答一遍，而那句话本来就在它的 transcript 里。
        #
        # 「自己产出的」要两件事一起看，单看 `turn_id` 是错的：一条**到达**的消息
        # 也会带轮次号。一次发送里的附件块跟着正文块的 id 走（`chat.py` 的
        # `attribution_id`），额度耗尽那条落地路径拿着运行中的轮次号调 `converse`
        # ——两处都是人说的话，却都带着非空的 `turn_id`，光看它就一个标记都不盖，
        # 于是「一句话 + 一张图」里那张图落回上面那段注释说的位置水位兜底，而那正
        # 是会把它永久丢掉的那条路。所以判据是**署名是 agent** 且**落在某一轮里**
        # 才算那一轮自己的产出；人说的话不论有没有轮次号都是输入。
        #
        # `own_output` 是调用方直接给出的答案，给那种「署名是 agent、平台这边却
        # 填不出轮次号」的写入端用：`cheese ask` 问出口的那句话由平台代写进房间
        # （`api/routes/topics.py`），而问话的那一轮跑在机器上，平台没有它的轮次号。
        # 它在等**人**回答，不是在等自己读一遍。
        if (
            not own_output
            and is_participant(author_type)
            and kind in (BlockKind.message, BlockKind.attachment)
            and not (looks_like_agent_handle(author) and turn_id is not None)
        ):
            meta = {CONSUMED_TURN_META_KEY: None, **(meta or {})}
        # A platform sentence carries its key; the reader's screen renders it
        # in the reader's language (`notice_text.py`).
        meta = with_keys(meta, content=content)
        block = Block(
            project_id=project_id,
            topic_id=topic_id,
            task_id=task_id,
            author=author,
            author_type=author_type,
            content=content,
            kind=kind,
            reply_to=reply_to,
            struct_parent=struct_parent,
            refs=refs or [],
            node_type=node_type,
            struct_order=struct_order,
            turn_id=turn_id,
            anchor_quote=anchor_quote,
            mime_type=mime_type,
            meta=meta,
        )
        # Timeline order is `created_at`, so a caller that knows WHEN the thing
        # happened must be able to say so. The default (now) is right for
        # anything written as it happens and wrong for anything reassembled
        # after the fact — a 芝士 message is only known to be complete once the
        # event after it arrives, so left to default it files itself behind the
        # tool call it actually preceded.
        if created_at is not None:
            block.created_at = created_at
        self._session.add(block)
        await self._session.flush()
        await self._session.refresh(block)
        return block

    async def get(self, block_id: uuid.UUID) -> Block | None:
        return await self._session.get(Block, block_id)

    async def client_delivery(
        self, topic_id: uuid.UUID, *, author: str, client_id: str
    ) -> list[Block]:
        """Return the block bundle one browser delivery wrote.

        ``author`` + ``client_id`` already name one send by one sender; the
        author's 档位 never selected anything on top of that.
        """
        anchor = await self._session.scalar(
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.author == author,
                Block.meta["client_id"].as_string() == client_id,
            )
            .order_by(Block.created_at, Block.id)
            .limit(1)
        )
        if anchor is None:
            return []
        rows = await self._session.scalars(
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.author == author,
                Block.turn_id == anchor.turn_id,
            )
            .order_by(Block.created_at, Block.id)
        )
        return list(rows)

    @staticmethod
    def _in_place(topic_id: uuid.UUID, task_id: uuid.UUID | None):
        """Rows belonging to one place: a room's own main line, or one thread.

        Every timeline query needs this and none of them needed it before, when
        a piece of work was a room and `topic_id` alone said everything. It is a
        helper rather than an inlined pair of predicates because forgetting the
        `task_id` half does not fail — it quietly answers for the room's main
        line, which for a room read is right and for a thread read is a bug that
        renders someone else's conversation.
        """
        return (
            Block.topic_id == topic_id,
            Block.task_id.is_(None) if task_id is None else Block.task_id == task_id,
        )

    async def has_eid(self, topic_id: uuid.UUID, eid: str) -> bool:
        """Whether this topic already materialized a hook event id."""
        return await self.has_any_eid(topic_id, [eid])

    async def has_any_eid(self, topic_id: uuid.UUID, eids: list[str]) -> bool:
        """Whether ANY of these hook event ids is already materialized —
        matching ``meta.eid`` or a coalesced message's ``meta.eids`` list, so
        a redelivered flush of an already-landed message is recognized by
        whichever of its constituent ids it arrives under."""
        if not eids:
            return False
        stmt = (
            select(Block.id)
            .where(
                Block.topic_id == topic_id,
                or_(
                    Block.meta["eid"].as_string().in_(eids),
                    # meta is JSON (not JSONB); cast the array for `?|`
                    # (jsonb "contains any of these strings").
                    cast(Block.meta["eids"], JSONB).op("?|")(array(eids, type_=Text)),
                ),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt) is not None

    async def last_said_in_turn(
        self, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> str | None:
        """The words the agent last kept in 现场 during this turn, if any.

        Read from the rows, not from the turn's in-memory bookkeeping: a turn
        closed by a backend that took it over mid-way (a deploy's handover)
        has none, and the room is what actually knows what was said."""
        stmt = (
            select(Block.content, Block.meta)
            .where(
                Block.topic_id == topic_id,
                Block.turn_id == turn_id,
                Block.kind == BlockKind.event,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(50)
        )
        for content, meta in (await self._session.execute(stmt)).all():
            if isinstance(meta, dict) and meta.get("progress"):
                return content
        return None

    async def has_action(
        self, topic_id: uuid.UUID, turn_id: uuid.UUID, action: str
    ) -> bool:
        """Whether this turn already announced this kind of action here."""
        stmt = (
            select(Block.id)
            .where(
                Block.topic_id == topic_id,
                Block.turn_id == turn_id,
                Block.meta["action"].as_string() == action,
            )
            .limit(1)
        )
        return await self._session.scalar(stmt) is not None

    async def delete(self, block: Block) -> None:
        await self._session.delete(block)
        await self._session.flush()

    async def set_upgraded_to_place(
        self,
        block: Block,
        *,
        topic_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        """Point this block's position at the place it became.

        Exactly one of the two: upgrading inside a room dispatches work
        (`task_id`), upgrading out of a private chat opens a room
        (`topic_id`). Both are real foreign keys, which is why this is a pair
        of nullable columns rather than one column holding either.
        """
        block.upgraded_to_topic_id = topic_id
        block.upgraded_to_task_id = task_id
        await self._session.flush()

    async def set_doc_content(
        self, doc: Block, content: str, *, expected_version: int
    ) -> Block | None:
        """Overwrite the living doc, but only if it is still at
        ``expected_version``. Returns the updated block, or ``None`` when
        somebody else wrote it first.

        The check is the WHERE clause, not an `if` above the write. Reading the
        version in Python and comparing it there leaves the two writers that
        read the same number both passing the comparison and both writing —
        which is the bug, restated one layer up.
        """
        stmt = (
            update(Block)
            .where(Block.id == doc.id, Block.doc_version == expected_version)
            .values(content=content, doc_version=Block.doc_version + 1)
            # The row is re-read below; letting the ORM guess how to sync it
            # against an expression it cannot evaluate in Python buys nothing.
            .execution_options(synchronize_session=False)
        )
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        won = (await self._session.execute(stmt)).rowcount == 1  # type: ignore[attr-defined]
        # Either way the in-memory block is now behind the row: it either just
        # gained a version, or somebody else's write is what our WHERE missed.
        # The caller reports the current version, so it has to be the real one.
        await self._session.refresh(doc)
        return doc if won else None

    async def mark_consumed(
        self, block_ids: list[uuid.UUID], turn_id: uuid.UUID
    ) -> None:
        """Stamp human blocks as read, by the turn `turn_id` whose clean Stop
        showed the session got through them.

        This is what makes the next turn's pending window a fact instead of a
        guess (see CONSUMED_TURN_META_KEY). `meta` is a plain JSON column, so the
        dict is REPLACED rather than mutated in place — an in-place mutation is
        invisible to SQLAlchemy's change detection and would silently not save.
        """
        if not block_ids:
            return
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            block.meta = {**(block.meta or {}), CONSUMED_TURN_META_KEY: str(turn_id)}
        await self._session.flush()

    async def bump_prompt_attempts(
        self, block_ids: list[uuid.UUID], turn_id: uuid.UUID
    ) -> int:
        """Record that these blocks went into turn `turn_id`'s prompt AGAIN,
        and return the highest attempt count in the batch.

        Called when the prompt is built, not when the turn ends — that is the
        whole point. `mark_consumed` runs only on a turn that finished, so a
        turn that dies leaves no trace at all and the next turn re-sends the
        identical batch. This counter is the trace.

        Same `meta` replacement rule as `mark_consumed`: in-place mutation of a
        JSON column is invisible to SQLAlchemy and would silently not save.
        """
        if not block_ids:
            return 0
        highest = 0
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            n = prompt_attempts(block) + 1
            block.meta = {
                **(block.meta or {}),
                PROMPT_ATTEMPTS_META_KEY: n,
                PROMPTED_TURN_META_KEY: str(turn_id),
            }
            highest = max(highest, n)
        await self._session.flush()
        return highest

    async def forget_prompted_turn(self, block_ids: list[uuid.UUID]) -> None:
        """Withdraw these blocks' claim to a delivered prompt: the turn that
        carried them failed, so a later clean Stop must not read them as heard.
        The next prompt that carries them records its own turn again."""
        if not block_ids:
            return
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            meta = dict(block.meta or {})
            if meta.pop(PROMPTED_TURN_META_KEY, None) is not None:
                block.meta = meta
        await self._session.flush()

    async def mark_step_failed(self, block_id: uuid.UUID, error: str) -> Block | None:
        """Record on a 现场 step that its tool came back an error.

        Written onto the step that is already there rather than as a second
        block: "it failed" is a property of that one line, and a block of its
        own would put the verdict somewhere the eye has to pair back up with
        the action. Same `meta` replacement rule as `mark_consumed` — an
        in-place mutation of a JSON column never saves.

        Returns the step as it now reads, for whoever tells the room; None when
        it is gone.
        """
        block = await self._session.get(Block, block_id)
        if block is None:
            return None
        meta = {**(block.meta or {}), "failed": True}
        if error:
            meta["error"] = error
        block.meta = meta
        await self._session.flush()
        return block

    async def record_step_output(
        self, block_id: uuid.UUID, output: str, total_bytes: int
    ) -> Block | None:
        """Keep on a 现场 step the tail of what its tool printed
        (``domain/agent/step_output``) and how long the whole was. Same `meta`
        replacement rule as above."""
        block = await self._session.get(Block, block_id)
        if block is None:
            return None
        block.meta = {
            **(block.meta or {}),
            "output": output,
            "output_bytes": total_bytes,
            # When the step last changed: 现场 reads its latest activity off it.
            "at": datetime.now(UTC).isoformat(),
        }
        await self._session.flush()
        return block

    async def restate(
        self, block_id: uuid.UUID, *, content: str, meta: dict
    ) -> Block | None:
        """Say again, in place, what an event block says — a notice whose news
        moved on (the third retry of the same request, the wait that ended)
        rather than a second line for the same thing. `meta` is merged over
        what is there, by replacement, for the same reason as above."""
        block = await self._session.get(Block, block_id)
        if block is None:
            return None
        block.content = content
        block.meta = with_keys({**(block.meta or {}), **meta}, content=content)
        await self._session.flush()
        return block

    async def replace_content(
        self, block: Block, content: str, *, checklist: dict | None = None
    ) -> Block:
        """Replace a message's text and stamp when that happened. The checklist
        it carries is replaced along with it, or dropped: text written some
        other way no longer says what the old list said."""
        meta = {
            key: value
            for key, value in (block.meta or {}).items()
            if key != CHECKLIST_META_KEY
        }
        if checklist is not None:
            meta[CHECKLIST_META_KEY] = checklist
        block.content = content
        block.meta = {**meta, EDITED_AT_META_KEY: datetime.now(UTC).isoformat()}
        await self._session.flush()
        return block

    async def current_checklist(
        self, room_id: uuid.UUID, author: str, *, message: uuid.UUID | None = None
    ) -> Block | None:
        """The newest checklist message ``author`` posted on the room's own line,
        or, given ``message``, that checklist on the room's own line whoever
        wrote it: whether its writer may edit it is the edit's own rule."""
        stmt = (
            select(Block)
            .where(
                *self._in_place(room_id, None),
                Block.kind == BlockKind.message,
                Block.author == author if message is None else Block.id == message,
                Block.meta[CHECKLIST_META_KEY].as_string().is_not(None),
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def update_node(
        self, block: Block, *, node_type: str, struct_order: float
    ) -> Block:
        """Reposition / retype a doc-tree node (B1)."""
        block.node_type = node_type
        block.struct_order = struct_order
        await self._session.flush()
        return block

    async def doc_root(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> Block | None:
        """This PLACE's canonical living-doc block (markdown blob, spec §2.2).

        The `task_id` half is load-bearing, not decoration: a room and every
        thread in it now carry `topic_id` of the room, so without it the room's
        document resolves to whichever doc block happens to be oldest — which
        after the first split is a thread's task brief.
        """
        stmt = (
            select(Block)
            .where(*self._in_place(topic_id, task_id), Block.kind == BlockKind.doc)
            .order_by(Block.created_at)
        )
        return (await self._session.scalars(stmt)).first()

    async def doc_roots(self, topic_ids: list[uuid.UUID]) -> dict[uuid.UUID, Block]:
        """Several rooms' living docs at once, keyed by room id.

        总览要列每个活跃话题的「现状」：一间房一次往返，而这是每一轮都要拼
        的东西，170 间房就是 170 次。只取房间自己那一份（``task_id`` 为空）——
        线程的文档是那张卡的东西，不是房间的状态。
        """
        if not topic_ids:
            return {}
        stmt = (
            select(Block)
            .where(
                Block.topic_id.in_(topic_ids),
                Block.task_id.is_(None),
                Block.kind == BlockKind.doc,
            )
            .order_by(Block.created_at)
        )
        roots: dict[uuid.UUID, Block] = {}
        for block in (await self._session.scalars(stmt)).all():
            # Oldest first, so the first one seen is the room's canonical doc.
            roots.setdefault(block.topic_id, block)
        return roots

    async def list_doc_nodes(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> list[Block]:
        """The living doc's structured node tree (B1), in document order."""
        stmt = (
            select(Block)
            .where(*self._in_place(topic_id, task_id), Block.kind == BlockKind.doc_node)
            .order_by(Block.struct_order)
        )
        return list((await self._session.scalars(stmt)).all())

    # Document-view kinds — never part of the conversation timeline. An artifact
    # (`cheese show`) IS: 芝士 putting something in front of the room is something
    # it said, and the chat renders it as a card the reader can open. Left out, it
    # reached people only as the preview tab, which shows the last one alone.
    NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment)

    async def ai_turn_ids(self, turn_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these turns produced at least one block signed by 芝士.

        One query rather than loading a topic's whole timeline to filter it in
        Python: the caller (the orphan sweep) asks about a handful of turn ids
        on a topic that may hold thousands of blocks.
        """
        if not turn_ids:
            return set()
        stmt = (
            select(Block.turn_id)
            .where(
                Block.turn_id.in_(turn_ids),
                agent_handle_column(Block.author),
            )
            .distinct()
        )
        return {row for row in (await self._session.scalars(stmt)).all() if row}

    async def tasks_awaiting_an_answer(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        """这些活里，哪几条停在一个未回答的提问上，各自在等谁 —— 一次查完。

        判据是 #1084 定的那一条：**最近一条提问消息没有作答记录**。不需要新增
        存储，因为回答本来就记在提问那一块上（`meta.answer_log`，末条是当前生效的
        那一版）。取「最近一条」而不是「有没有任何一条」：已回答的旧提问不该让这条
        活长期停留在待处理。

        等谁也记在那一块上（`meta.asked`，提问那一刻写下的，见 `ask_options`）。
        None 是「这道题指不到具体的人」。只关心停没停的调用方照样拿它做 `in`。

        每条活只取一行（`DISTINCT ON`），走 `ix_blocks_task_id_created_at`。
        """
        return await self._awaiting_an_answer(Block.task_id, task_ids)

    async def rooms_awaiting_an_answer(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        """同一个判据，问的是房间自己那条线（`task_id IS NULL`）。

        分成两个方法而不是一个带开关的：房间和活是两种东西，而「房间自己那条线」
        这个条件只对前者成立 —— 合成一个函数就得在里面判断主语是谁。
        """
        return await self._awaiting_an_answer(
            Block.topic_id, topic_ids, Block.task_id.is_(None)
        )

    #: 只往回看这么久。红灯报的是「现在有人在干等」，一条一周前没人接的消息已经
    #: 不是这件事了；而不设界的话，这条查询要扫整个项目全部历史消息。
    REPLY_LOOKBACK = timedelta(days=7)

    #: 机器 / 环境这一侧的平台事件。AI 没回话时，如果等待期间最近一条是它们，
    #: 那 AI 多半不是卡住，而是脚下的机器还没好——侧栏据此换阈值和说法。
    MACHINE_EVENTS = (
        "machine_provisioning",
        "device_waiting",
        "sandbox_rebuilt",
        "environment_repaired",
    )

    async def rooms_awaiting_a_reply(
        self,
        topic_ids: list[uuid.UUID],
        *,
        now: datetime,
        stuck_rooms: Mapping[uuid.UUID, StuckCard] | None = None,
    ) -> dict[uuid.UUID, ReplyWait]:
        """{房间: 从什么时候开始有人在等 AI 回话} —— 一次查完，只看房间自己那条线。

        「在等」有两种：一个人发了一条**点了 AI 名**的消息（`agent_recipient.
        mentioned`，也就是会叫起一轮的那种），而这之后房间里还没有任何 AI 说过话；
        或者房间（连同名下的活）有一张卡停在「检查红了 / 冲突了，要 AI 去修」上
        （`stuck_rooms`，调用方按看板判据算好传进来），从最近一次检查报错算起，见
        `_checks_awaiting_an_agent`。人和人之间
        聊天叫不起 AI，也就谈不上等它。值是这批没人接的消息里**最早**那一条的时间：
        等得最久的那个人决定灯什么时候亮。

        平台自己写的事件（排队提示、额度提醒）不算回话 —— 它们不是 AI 在回应人。
        """
        if not topic_ids:
            return {}
        since = now - self.REPLY_LOOKBACK
        room_line = (
            Block.topic_id.in_(topic_ids),
            Block.task_id.is_(None),
            Block.kind == BlockKind.message,
            Block.created_at >= since,
        )
        last_reply = (
            select(Block.topic_id, func.max(Block.created_at).label("at"))
            .where(*room_line, participant_blocks(), agent_handle_column(Block.author))
            .group_by(Block.topic_id)
            .subquery()
        )
        stmt = (
            select(Block.topic_id, func.min(Block.created_at))
            .outerjoin(last_reply, last_reply.c.topic_id == Block.topic_id)
            .where(
                *room_line,
                participant_blocks(),
                ~agent_handle_column(Block.author),
                Block.meta["agent_recipient"]["mentioned"].as_boolean(),
                # 读进过一轮、而那一轮跑完了，就算接到了 —— 哪怕 AI 选择不说话
                # （「不用管，我只是想知道原因」本来就不期待回复）。这一位只由
                # 跑完的轮次盖上，死掉的轮次不盖，所以它不会把卡住的情况吞掉。
                Block.meta[CONSUMED_TURN_META_KEY].as_string().is_(None),
                or_(last_reply.c.at.is_(None), Block.created_at > last_reply.c.at),
            )
            .group_by(Block.topic_id)
        )
        waiting = {
            topic_id: ReplyWait(since=at, reason="mention")
            for topic_id, at in (await self._session.execute(stmt)).all()
        }
        stuck_rooms = stuck_rooms or {}
        stuck = [t for t in topic_ids if t in stuck_rooms]
        for topic_id, at in await self._checks_awaiting_an_agent(stuck, since):
            if topic_id not in waiting or at < waiting[topic_id].since:
                card = stuck_rooms[topic_id]
                waiting[topic_id] = ReplyWait(
                    since=at, reason=card.kind, source="card", pr=card.pr
                )
        if waiting:
            machine = await self._machine_events_since_last_reply(list(waiting), since)
            for topic_id, (event, at) in machine.items():
                wait = waiting[topic_id]
                # 只认等待开始之后的：等之前机器早就好了，那这次没回话跟它无关。
                if at >= wait.since:
                    waiting[topic_id] = replace(wait, reason=event)
        return waiting

    async def _machine_events_since_last_reply(
        self, topic_ids: list[uuid.UUID], since: datetime
    ) -> dict[uuid.UUID, tuple[str, datetime]]:
        """{房间: (最近一条机器/环境事件, 时间)} —— 只算最后一次 AI 说话之后的。"""
        under_room = (Block.topic_id.in_(topic_ids), Block.created_at >= since)
        last_reply = (
            select(Block.topic_id, func.max(Block.created_at).label("at"))
            .where(
                *under_room,
                Block.kind == BlockKind.message,
                participant_blocks(),
                agent_handle_column(Block.author),
            )
            .group_by(Block.topic_id)
            .subquery()
        )
        event = Block.meta["event_type"].as_string()
        stmt = (
            select(Block.topic_id, event, Block.created_at)
            .outerjoin(last_reply, last_reply.c.topic_id == Block.topic_id)
            .where(
                *under_room,
                ~participant_blocks(),
                event.in_(self.MACHINE_EVENTS),
                or_(last_reply.c.at.is_(None), Block.created_at > last_reply.c.at),
            )
            .order_by(Block.topic_id, Block.created_at.desc())
            .distinct(Block.topic_id)
        )
        return {
            topic_id: (kind, at)
            for topic_id, kind, at in (await self._session.execute(stmt)).all()
        }

    #: 要 AI 去接手的平台事件：PR 上有评审意见、检查没过、合不进去。这些落地时就
    #: 已经轮到芝士了（`platform_notices` 里各自写着「芝士要去改」）。
    CHECKS_FOR_THE_AGENT = (
        "pr_review",
        "pr_conflict",
        "ci_failed",
        "gate_failed",
        "gate_blocked",
        "gate_abandoned",
        "merge_refused",
        "accept_conflict",
        "upstream_conflict",
        "migration_collision",
        # 验收人把改动退回了：下一步是 AI 改完重递。
        "card_rejected",
    )

    async def _checks_awaiting_an_agent(
        self, topic_ids: list[uuid.UUID], since: datetime
    ) -> list[tuple[uuid.UUID, datetime]]:
        """[(房间, 最近一次要 AI 接手的检查事件的时间)] —— 只问卡还停着的房间。

        「有没有人接」不看 AI 说没说过话（回一句不相干的话也会被当成接了），而看
        卡本身：调用方只把卡还停在检查红 / 冲突上的房间传进来。取**最近**一条事件：
        AI 推了修复、检查又红了，时钟从这次重新算。事件多半落在某条活的卡上，所以
        连同名下的活一起看。
        """
        if not topic_ids:
            return []
        stmt = (
            select(Block.topic_id, func.max(Block.created_at))
            .where(
                Block.topic_id.in_(topic_ids),
                Block.created_at >= since,
                ~participant_blocks(),
                Block.meta["event_type"].as_string().in_(self.CHECKS_FOR_THE_AGENT),
            )
            .group_by(Block.topic_id)
        )
        return [(t, at) for t, at in (await self._session.execute(stmt)).all()]

    #: 「这一轮坏了」的那几种平台提示：没分类的失败（HTTP 502/404、异常原话）和
    #: 分类过的平台故障。超时、部署打断这些是 warn —— 平台会自己接着跑，不算坏。
    FAILED_TURN_EVENTS = ("turn_failed", "platform_error")

    async def rooms_with_a_failed_turn(
        self, topic_ids: list[uuid.UUID], *, now: datetime
    ) -> dict[uuid.UUID, datetime]:
        """{房间: 最近一次轮次报错的时间} —— 只算报错之后还没有 AI 说过话的。

        一轮以「本轮未完成：…」收场，这个房间就是坏着的，不用等五分钟；之后 AI
        开口说话了（重试成功、或下一轮正常回话），就不再算。只看房间自己那条线。
        """
        if not topic_ids:
            return {}
        since = now - self.REPLY_LOOKBACK
        room_line = (
            Block.topic_id.in_(topic_ids),
            Block.task_id.is_(None),
            Block.created_at >= since,
        )
        last_reply = (
            select(Block.topic_id, func.max(Block.created_at).label("at"))
            .where(
                *room_line,
                Block.kind == BlockKind.message,
                participant_blocks(),
                agent_handle_column(Block.author),
            )
            .group_by(Block.topic_id)
            .subquery()
        )
        stmt = (
            select(Block.topic_id, func.max(Block.created_at))
            .outerjoin(last_reply, last_reply.c.topic_id == Block.topic_id)
            .where(
                *room_line,
                ~participant_blocks(),
                Block.meta["event_type"].as_string().in_(self.FAILED_TURN_EVENTS),
                Block.meta["severity"].as_string() == "error",
                or_(last_reply.c.at.is_(None), Block.created_at > last_reply.c.at),
            )
            .group_by(Block.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: at for topic_id, at in rows}

    async def awaiting_answer_blocks(self, topic_ids, task_ids):
        """Return addressed person and exact question for each waiting place."""
        return (
            await self._awaiting_an_answer(
                Block.topic_id, topic_ids, Block.task_id.is_(None), with_blocks=True
            ),
            await self._awaiting_an_answer(Block.task_id, task_ids, with_blocks=True),
        )

    async def _awaiting_an_answer(
        self, place_column, place_ids: list[uuid.UUID], *extra, with_blocks=False
    ):
        if not place_ids:
            return {}
        stmt = (
            select(place_column, Block.meta, Block.created_at, Block.author, Block.id)
            .where(
                place_column.in_(place_ids),
                Block.kind == BlockKind.message,
                # `meta` 是 json（不是 jsonb），所以用 `->>` 判存在，和
                # `ix_blocks_cloud_provisioning` 那个部分索引同一个写法。判的是
                # 「这是一道题」——`options` 这个键在不在，和它装的是字符串还是
                # {text, explain} 对象无关（`->>` 取到的都不是 NULL）。
                Block.meta["options"].as_string().isnot(None),
                *extra,
            )
            .order_by(place_column, Block.created_at.desc())
            .distinct(place_column)
        )
        latest = (await self._session.execute(stmt)).all()
        member_ids = {
            uuid.UUID(member)
            for _, meta, _, _, _ in latest
            for member in (meta or {}).get("ask_group", {}).get("members", [])
        }
        members = (
            {
                str(block.id): block
                for block in await self._session.scalars(
                    select(Block).where(Block.id.in_(member_ids))
                )
            }
            if member_ids
            else {}
        )
        rows = []
        selected = {}
        for place_id, meta, at, asker, block_id in latest:
            if place_id is None:
                continue
            meta = meta or {}
            group = meta.get("ask_group")
            if group:
                # A wake or follow-up message must not erase deferred members.
                pending = [
                    members[key]
                    for key in group["members"]
                    if key in members
                    and not (members[key].meta or {}).get("answer_log")
                ]
                if not pending:
                    continue
                block = pending[0]
                meta, at, asker, block_id = (
                    block.meta,
                    block.created_at,
                    block.author,
                    block.id,
                )
            elif meta.get("answer_log"):
                continue
            rows.append((place_id, meta, at, asker))
            selected[place_id] = block_id
        if not rows:
            return {}
        # 没点按钮、直接打字回了一句，也是回应过了：题问出来之后，被问的那个人
        # （题上没记是谁，就任何一个人）在这里说过话，这道题就不再挂在他身上。
        spoke = (
            select(place_column, Block.author, func.max(Block.created_at))
            .where(
                place_column.in_([place_id for place_id, _, _, _ in rows]),
                Block.kind == BlockKind.message,
                participant_blocks(),
                ~agent_handle_column(Block.author),
                *extra,
            )
            .group_by(place_column, Block.author)
        )
        last_said: dict[uuid.UUID, dict[str, datetime]] = {}
        for place_id, author, at in (await self._session.execute(spoke)).all():
            last_said.setdefault(place_id, {})[author] = at
        # 芝士问完，自己又在同一条线上接着说了一句 —— 那道题它自己已经绕过去了，
        # 不该继续挂在被问的人身上 (#2046：芝士问完没等回答就自己做完、又发了几句
        # 进展，房间却一直停在「待回答」，直到人真去点一下才灭)。人问的题不适用：
        # 人问完再补一句，不等于他不用答了。所以这里只认**提问那条消息的作者**，
        # 且他本人是芝士 —— 别的席位上有人说话，是那名参与者的发言，不是这道题的
        # 提问者收回了它。
        agent_spoke = (
            select(place_column, Block.author, func.max(Block.created_at))
            .where(
                place_column.in_([place_id for place_id, _, _, _ in rows]),
                Block.kind == BlockKind.message,
                participant_blocks(),
                agent_handle_column(Block.author),
                *extra,
            )
            .group_by(place_column, Block.author)
        )
        said_by_agent: dict[uuid.UUID, dict[str, datetime]] = {}
        for place_id, author, at in (await self._session.execute(agent_spoke)).all():
            said_by_agent.setdefault(place_id, {})[author] = at
        waiting: dict[uuid.UUID, str | None] = {}
        for place_id, meta, asked_at, asker in rows:
            asked = meta.get("asked")
            if meta.get("ask_group"):
                waiting[place_id] = asked
                continue
            said = last_said.get(place_id, {})
            if asked:
                times = [said[asked]] if asked in said else []
            else:
                times = list(said.values())
            if any(at > asked_at for at in times):
                continue
            if looks_like_agent_handle(asker):
                own = said_by_agent.get(place_id, {}).get(asker)
                if own is not None and own > asked_at:
                    continue
            waiting[place_id] = asked
        if with_blocks:
            return {place: (asked, selected[place]) for place, asked in waiting.items()}
        return waiting

    async def last_summoner(self, room_id: uuid.UUID) -> str | None:
        """房间自己那条线上，最近一个点了 AI 名的人。

        `cheese_ask` 要记下「这道题在等谁」，本该问开着的那一轮是谁发起的；轮次
        没记下来的时候（有些执行路径不开轮次区间），退到这一条：芝士此刻在回应的，
        就是最近叫它的那个人。
        """
        stmt = (
            select(Block.author)
            .where(
                Block.topic_id == room_id,
                Block.task_id.is_(None),
                Block.kind == BlockKind.message,
                participant_blocks(),
                ~agent_handle_column(Block.author),
                Block.meta["agent_recipient"]["mentioned"].as_boolean(),
            )
            .order_by(Block.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_topic(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> list[Block]:
        """Timeline view: blocks of a topic, oldest first (spec §5).

        Excludes doc_node tree blocks and inline comments — those belong to the
        document view, not the conversation timeline.

        Ties on created_at break by id, the same total order `page_for_topic`
        walks — otherwise the full view and the paged view could disagree about
        the order of blocks stamped in the same instant.
        """
        stmt = (
            select(Block)
            .where(
                *self._in_place(topic_id, task_id),
                Block.kind.not_in(self.NON_TIMELINE),
            )
            .order_by(Block.created_at, Block.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def turn_history(self, topic_id: uuid.UUID) -> list[Block]:
        """Inputs awaiting consumption and the two turn-preparation boundaries.

        Inputs, not the timeline: a room's events are mostly for people to read,
        and only the ones whose author wrote a sentence for 芝士 are addressed to
        it. Those are selected by carrying that sentence, the same way a human
        input is selected by being one — not by their kind, which says who can
        see them rather than who they are for.
        """
        place = self._in_place(topic_id, None)
        latest_ai = (
            select(Block.id)
            .where(
                *place,
                agent_handle_column(Block.author),
                Block.kind == BlockKind.message,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        latest_cloud = (
            select(Block.id)
            .where(
                *place,
                Block.kind.not_in(self.NON_TIMELINE),
                Block.meta["event_type"].as_string() == "cloud_provisioning",
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        # The latest AI message remains the watermark for untracked legacy
        # inputs. Explicitly pending inputs can precede it and must survive.
        stmt = (
            select(Block)
            .where(
                *place,
                or_(
                    Block.id == latest_ai,
                    Block.id == latest_cloud,
                    and_(
                        participant_blocks(),
                        Block.kind.in_((BlockKind.message, BlockKind.attachment)),
                        Block.meta[CONSUMED_TURN_META_KEY].as_string().is_(None),
                        # 没有戳的块有两种：还没被读过的（带 null 标记），和记账
                        # 存在之前写下的（什么也不带）。第二种只有靠上面那条水位
                        # 线才判得了，而水位线本身就是最后一条芝士的消息 —— 一条
                        # 芝士签名的老消息永远在它之前，取回来也只是被丢掉。房间
                        # 的历史只增不减，所以在这里挡掉，不是在 Python 里。
                        or_(
                            cast(Block.meta, JSONB).has_key(CONSUMED_TURN_META_KEY),
                            ~agent_handle_column(Block.author),
                        ),
                    ),
                    and_(
                        Block.meta[AGENT_NOTICE_META_KEY].as_string().is_not(None),
                        Block.meta[CONSUMED_TURN_META_KEY].as_string().is_(None),
                    ),
                ),
            )
            .order_by(Block.created_at, Block.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_for_topic(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> Block | None:
        """The newest block in the topic's timeline, or None for an empty topic.

        Same total order and same exclusions as `list_for_topic`, so "the last
        thing in the topic" means here exactly what the reader sees at the
        bottom of the conversation — which is what makes a stall verdict built
        on it checkable by hand.
        """
        stmt = (
            select(Block)
            .where(
                *self._in_place(topic_id, task_id),
                Block.kind.not_in(self.NON_TIMELINE),
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def page_for_topic(
        self,
        topic_id: uuid.UUID,
        *,
        task_id: uuid.UUID | None = None,
        limit: int,
        before: Block | None = None,
        kinds: Collection[BlockKind] | None = None,
        after: Block | None = None,
        query: str | None = None,
        reply_to: uuid.UUID | None = None,
        author: str | None = None,
    ) -> BlockPage:
        """The newest `limit` blocks, or a page before/after a cursor.

        An after cursor reads the oldest newer records first, so catching up
        through multiple pages cannot skip intervening messages. Explicit kinds
        include document-view records; otherwise the timeline exclusions apply.

        Cursor, not offset, because chat grows at the tail while you read it: an
        offset window slides every time a message lands, so page 2 re-serves or
        skips rows. A cursor anchored on a real block is immune to tail inserts.

        The order key is the (created_at, id) pair, not created_at alone —
        created_at is stamped in Python, so a burst of streamed blocks can share
        a timestamp and single-column ordering wouldn't be a total order (the
        cursor could then skip or repeat the tied rows).
        """
        stmt = select(Block).where(*self._in_place(topic_id, task_id))
        # 现场 wants events and nothing else; narrowing HERE rather than in the
        # caller is the difference between paging and pretending to — filtering
        # a page after the fact returns fewer rows than asked for and reports
        # has_more against the wrong set.
        if kinds is not None:
            stmt = stmt.where(Block.kind.in_(list(kinds)))
        else:
            stmt = stmt.where(Block.kind.not_in(self.NON_TIMELINE))
        if query:
            # Literal matching: a pasted log containing % or _ is not SQL syntax.
            pattern = (
                "%"
                + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            stmt = stmt.where(
                or_(
                    Block.content.ilike(pattern, escape="\\"),
                    # JSONB renders stored Unicode escapes as searchable text.
                    cast(cast(Block.meta, JSONB), Text).ilike(pattern, escape="\\"),
                    Block.anchor_quote.ilike(pattern, escape="\\"),
                )
            )
        if reply_to is not None:
            stmt = stmt.where(Block.reply_to == reply_to)
        if author is not None:
            stmt = stmt.where(Block.author == author)
        if before is not None:
            # Row-value comparison: `(created_at, id) < (:ts, :id)` in one go,
            # so the cursor test matches the ORDER BY key exactly. There is no
            # composite index on (topic_id, created_at, id) today — the topic_id
            # index narrows to one topic's rows and Postgres sorts those (a few
            # thousand at worst). Paging's win is the payload, not the scan.
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id) < (before.created_at, before.id)
            )
        if after is not None:
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id) > (after.created_at, after.id)
            )
        # One row past the window tells us whether more blocks remain, without
        # a second COUNT query.
        order = (
            (Block.created_at, Block.id)
            if after is not None
            else (Block.created_at.desc(), Block.id.desc())
        )
        stmt = stmt.order_by(*order).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        if after is None:
            rows.reverse()  # callers render oldest-first, same as list_for_topic
        return BlockPage(items=rows, has_more=has_more)

    async def count_for_topic(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Block)
            .where(
                *self._in_place(topic_id, task_id),
                Block.kind.not_in(self.NON_TIMELINE),
            )
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def count_messages(
        self, room_id: uuid.UUID, *, excluding: Collection[uuid.UUID] = ()
    ) -> int:
        """Chat messages on a room's own line, leaving out ``excluding``."""
        stmt = (
            select(func.count())
            .select_from(Block)
            .where(*self._in_place(room_id, None), Block.kind == BlockKind.message)
        )
        if excluding:
            stmt = stmt.where(Block.id.not_in(list(excluding)))
        return int((await self._session.scalar(stmt)) or 0)

    async def list_comments_for_topic(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> list[Block]:
        """Inline comments (B4), oldest first; each anchors to a doc node via
        reply_to."""
        stmt = (
            select(Block)
            .where(*self._in_place(topic_id, task_id), Block.kind == BlockKind.comment)
            .order_by(Block.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_artifact(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> Block | None:
        """The topic's current preview (spec §9.1): the most recent artifact block
        芝士 pointed at. Newest wins — re-running `cheese show` repoints it."""
        stmt = (
            select(Block)
            .where(*self._in_place(topic_id, task_id), Block.kind == BlockKind.artifact)
            .order_by(Block.created_at.desc())
        )
        return (await self._session.scalars(stmt)).first()

    async def shown_in_room(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> list[Block]:
        """这个房间里摆出来过的东西，每样一次，新的在前 (#1085 结论四)。

        一个房间常有好几样东西值得摆出来 —— 一份改好的 .docx、一张图、一个跑起来
        的应用 —— 而「当前预览」只说得出最后那一样。同一份被重新摆过几次只算一
        样：那是同一个东西的新一次渲染，不是又一件东西。
        """
        stmt = (
            select(Block)
            .where(*self._in_place(topic_id, task_id), Block.kind == BlockKind.artifact)
            .order_by(Block.created_at.desc())
        )
        seen: set[str] = set()
        newest_first: list[Block] = []
        for block in (await self._session.scalars(stmt)).all():
            if block.content in seen:
                continue
            seen.add(block.content)
            newest_first.append(block)
        return newest_first

    # ---- Emoji reactions (Slack semantics) ----

    async def _get_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> BlockReaction | None:
        stmt = select(BlockReaction).where(
            BlockReaction.block_id == block_id,
            BlockReaction.emoji == emoji,
            BlockReaction.author == author,
        )
        return (await self._session.scalars(stmt)).first()

    async def toggle_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Slack-style toggle: add the (emoji, author) reaction, or remove it if
        it already exists. Returns True when added, False when removed."""
        existing = await self._get_reaction(block_id, emoji, author)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
        return True

    async def add_reaction_if_absent(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Idempotent add (never removes) — for platform receipts like 芝士's 👀
        on the message that summoned it. Returns True when a row was created."""
        if await self._get_reaction(block_id, emoji, author) is not None:
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
        return True

    async def reactions_for_blocks(
        self, block_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[dict]]:
        """Aggregated reactions for many blocks in ONE query (no N+1):
        block_id → [{"emoji", "count", "authors"}], groups ordered by the emoji's
        first appearance on the block, authors by reaction time (Slack)."""
        if not block_ids:
            return {}
        stmt = (
            select(BlockReaction)
            .where(BlockReaction.block_id.in_(block_ids))
            .order_by(BlockReaction.created_at, BlockReaction.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        grouped: dict[uuid.UUID, dict[str, dict]] = {}
        for r in rows:
            per_block = grouped.setdefault(r.block_id, {})
            agg = per_block.setdefault(
                r.emoji, {"emoji": r.emoji, "count": 0, "authors": []}
            )
            agg["count"] += 1
            agg["authors"].append(r.author)
        return {bid: list(per.values()) for bid, per in grouped.items()}

    async def reactions_for_block(self, block_id: uuid.UUID) -> list[dict]:
        """Aggregated reactions of one block (same shape as the batch form)."""
        agg = await self.reactions_for_blocks([block_id])
        return agg.get(block_id, [])

    async def list_by_kind_for_project(
        self, project_id: uuid.UUID, kind: BlockKind
    ) -> list[Block]:
        """Project-wide blocks of a kind, newest first — e.g. the weekly reports
        (kind=weekly), each traceable to its source topic (§7.1)."""
        stmt = (
            select(Block)
            .where(Block.project_id == project_id, Block.kind == kind)
            .order_by(Block.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())
