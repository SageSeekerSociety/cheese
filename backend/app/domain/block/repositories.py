"""Block data access."""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, and_, cast, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.work_context import current_work_id
from app.domain.block.authorship import is_participant, participant_blocks
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    CONSUMED_TURN_META_KEY,
    PROMPT_ATTEMPTS_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    BlockReaction,
    prompt_attempts,
)
from app.domain.identity.handles import agent_handle_column, looks_like_agent_handle


@dataclass(frozen=True)
class BlockPage:
    """A chronological window; has_more follows the requested cursor direction."""

    items: list[Block]
    has_more: bool


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
        # 填不出轮次号」的写入端用：远程控制里芝士问出口的那句话由平台代写进房间
        # （`api/routes/remote_control.py` 的 `voice_pending`），而问话的那一轮跑
        # 在机器上，平台没有它的轮次号。它在等**人**回答，不是在等自己读一遍。
        if (
            not own_output
            and is_participant(author_type)
            and kind in (BlockKind.message, BlockKind.attachment)
            and not (looks_like_agent_handle(author) and turn_id is not None)
        ):
            meta = {CONSUMED_TURN_META_KEY: None, **(meta or {})}
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
        """Stamp human blocks as read into turn `turn_id`'s prompt.

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

    async def bump_prompt_attempts(self, block_ids: list[uuid.UUID]) -> int:
        """Record that these blocks went into a prompt AGAIN, and return the
        highest attempt count in the batch.

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
            block.meta = {**(block.meta or {}), PROMPT_ATTEMPTS_META_KEY: n}
            highest = max(highest, n)
        await self._session.flush()
        return highest

    async def mark_step_failed(self, block_id: uuid.UUID, error: str) -> bool:
        """Record on a 现场 step that its tool came back an error.

        Written onto the step that is already there rather than as a second
        block: "it failed" is a property of that one line, and a block of its
        own would put the verdict somewhere the eye has to pair back up with
        the action. Same `meta` replacement rule as `mark_consumed` — an
        in-place mutation of a JSON column never saves.
        """
        block = await self._session.get(Block, block_id)
        if block is None:
            return False
        meta = {**(block.meta or {}), "failed": True}
        if error:
            meta["error"] = error
        block.meta = meta
        await self._session.flush()
        return True

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

    # Document-view / render-only kinds — never part of the conversation timeline.
    # Artifacts are preview pointers surfaced in the preview window, not chat.
    _NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment, BlockKind.artifact)

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
    ) -> set[uuid.UUID]:
        """这些活里，哪几条停在一个未回答的提问上 —— 一次查完，看板用。

        判据是 #1084 定的那一条：**最近一条提问消息没有 `answered`**。不需要新增
        存储，因为回答本来就记在提问那一块上（`meta.answered`）。取「最近一条」而
        不是「有没有任何一条」：已回答的旧提问不该让这条活长期停留在待处理。

        每条活只取一行（`DISTINCT ON`），走 `ix_blocks_task_id_created_at`。
        """
        return await self._awaiting_an_answer(Block.task_id, task_ids)

    async def rooms_awaiting_an_answer(
        self, topic_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """同一个判据，问的是房间自己那条线（`task_id IS NULL`）。

        分成两个方法而不是一个带开关的：房间和活是两种东西，而「房间自己那条线」
        这个条件只对前者成立 —— 合成一个函数就得在里面判断主语是谁。
        """
        return await self._awaiting_an_answer(
            Block.topic_id, topic_ids, Block.task_id.is_(None)
        )

    async def _awaiting_an_answer(
        self, place_column, place_ids: list[uuid.UUID], *extra
    ) -> set[uuid.UUID]:
        if not place_ids:
            return set()
        stmt = (
            select(place_column, Block.meta)
            .where(
                place_column.in_(place_ids),
                Block.kind == BlockKind.message,
                # `meta` 是 json（不是 jsonb），所以用 `->>` 判存在，和
                # `ix_blocks_cloud_provisioning` 那个部分索引同一个写法。
                Block.meta["options"].as_string().isnot(None),
                *extra,
            )
            .order_by(place_column, Block.created_at.desc())
            .distinct(place_column)
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            place_id
            for place_id, meta in rows
            if place_id is not None and not (meta or {}).get("answered")
        }

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
                Block.kind.not_in(self._NON_TIMELINE),
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
                Block.kind.not_in(self._NON_TIMELINE),
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
                Block.kind.not_in(self._NON_TIMELINE),
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
            stmt = stmt.where(Block.kind.not_in(self._NON_TIMELINE))
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
                Block.kind.not_in(self._NON_TIMELINE),
            )
        )
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
        """Project-wide blocks of a kind, newest first — e.g. the decision log
        (kind=decision), each traceable to its source topic via refs (§7.1)."""
        stmt = (
            select(Block)
            .where(Block.project_id == project_id, Block.kind == kind)
            .order_by(Block.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())
