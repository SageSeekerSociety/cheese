"""Block data access. 写入与单块小读；分页、历史、问答和表情分到了同包的四个模块。

每一块住在哪儿，看那句话问的是什么：

- `paging.py` —— 一条对话的时间线怎么切片读（`list_for_topic` / `page_for_topic` /
  `count_for_topic` / `holds` / `count_messages`），连同它们共用的 `BlockPage` 与
  `_narrow()`。
- `history.py` —— 时间线上的定点小读（`turn_history` / `latest_for_topic` /
  `messages_before` / `last_messages` / `between`）。
- `awaiting.py` —— 哪几段对话停在一个未回答的提问上，以及一条回话答了哪几道题。
- `reactions.py` —— 表情（Slack 语义的增删与聚合读数）。

剩下的是写入（`add` / `mark_consumed` / `restate` …）和单块小读（`get` / `has_*` /
`latest_artifact` / `shown_in_room` …）。

拆开的理由是文件体积（这个文件拆分前 1199 行），不是分层：`BlockRepository` 继承那四个
模块的基类，方法签名、SQL 与 MRO 行为都没变 —— 被搬走的方法在 `git diff` 里是整段搬迁，
不是改写。

`BlockPage` 原来定义在本模块，现在定义在 `paging.py`，在这里再导出一次 —— 仓库内外按
`from app.domain.block.repositories import BlockPage` 取它。按**类对象**打的 monkeypatch
也照旧（`test_block_paging.py` 的 `reactions_for_blocks`、
`scripts/perf-awaiting-me/measure_awaiting_me.py` 的 `awaiting_answer_blocks`）：它们先
拿到 `BlockRepository` 再取属性，方法从哪个基类来不影响。
"""

import copy
import uuid
from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import (
    Text,
    bindparam,
    cast,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import with_keys
from app.core.work_context import current_work_id
from app.domain.block.authorship import is_participant
from app.domain.block.awaiting import AwaitingQueries
from app.domain.block.history import HistoryQueries
from app.domain.block.indexed_rows import (
    COALESCED_ROWS,
    EID,
)
from app.domain.block.models import (
    CHECKLIST_META_KEY,
    CONSUMED_TURN_META_KEY,
    EDITED_AT_META_KEY,
    PROMPT_ATTEMPTS_META_KEY,
    PROMPTED_TURN_META_KEY,
    READ_FAILED_META_KEY,
    Block,
    BlockKind,
    prompt_attempts,
)

# `BlockPage` 原来定义在本模块，拆分后定义在 `paging.py`。这条 import 不是「顺手
# 拿来用」，是把公开路径留在原地：仓库内外按 `from app.domain.block.repositories
# import BlockPage` 取它。所以 F401 在这里是故意的。
from app.domain.block.paging import BlockPage, PagingQueries  # noqa: F401
from app.domain.block.reactions import Reactions
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_handle_column, looks_like_agent_handle
from app.domain.notice_meta import shaped


class BlockRepository(
    AwaitingQueries,
    HistoryQueries,
    PagingQueries,
    Reactions,
):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        author: str,
        author_type: AuthorType,
        content: str,
        kind: BlockKind = BlockKind.message,
        reply_to: uuid.UUID | None = None,
        refs: list[str] | None = None,
        turn_id: uuid.UUID | None = None,
        mime_type: str | None = None,
        meta: dict | None = None,
        created_at: datetime | None = None,
        own_output: bool = False,
        block_id: uuid.UUID | None = None,
    ) -> Block:
        # Cheese-side handlers don't pass turn_id explicitly; fall back to the
        # ambient turn id set from the X-Cheese-Turn header (R4).
        if turn_id is None:
            turn_id = current_work_id.get()
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
        # A platform line nobody needs to read is kept out of the room as it is
        # written (`notice_meta.shown_as`), so the conversation's pages hold
        # only lines that are drawn.
        if kind == BlockKind.event:
            meta = shaped(meta, in_turn=turn_id is not None)
        # A platform sentence carries its key; the reader's screen renders it
        # in the reader's language (`app/core/sentences.py`).
        meta = with_keys(meta, content=content)
        # The row owns its meta. A caller keeps the dict it passed and edits it
        # after this write — `answer_questions` rewrites a message's
        # `agent_recipient` in place, then re-assigns `meta` — and with the
        # nested objects shared, that new value compared equal to the one just
        # mutated under it and SQLAlchemy emitted no UPDATE, so the stored row
        # kept the old recipient. `with_keys` already copies the top level;
        # this copies what is under it.
        if meta is not None:
            meta = copy.deepcopy(meta)
        block = Block(
            project_id=project_id,
            conversation_id=conversation_id,
            author=author,
            author_type=author_type,
            content=content,
            kind=kind,
            reply_to=reply_to,
            refs=refs or [],
            turn_id=turn_id,
            mime_type=mime_type,
            meta=meta,
            # A caller that already knows the row's id hands it over: the
            # participant send writes `turn_id = <this block's own id>` in the
            # same INSERT, and it can only know that id before the flush. Left
            # out, the column default mints one, exactly as before.
            **({"id": block_id} if block_id is not None else {}),
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
        # No refresh: `eager_defaults` puts the INSERT's RETURNING values
        # (`seq`, from `FetchedValue`) on the row at flush, and `created_at` /
        # `updated_at` are Python-side defaults. Re-reading the row here bought
        # nothing and cost a whole-row SELECT on every block written.
        return block

    async def get(self, block_id: uuid.UUID) -> Block | None:
        return await self._session.get(Block, block_id)

    async def many(self, block_ids: Collection[uuid.UUID]) -> list[Block]:
        """These blocks, those that exist, in no particular order."""
        if not block_ids:
            return []
        stmt = select(Block).where(Block.id.in_(list(block_ids)))
        return list((await self._session.scalars(stmt)).all())

    async def has_eid(self, conversation_id: uuid.UUID, eid: str) -> bool:
        """Whether this topic already materialized a hook event id."""
        return await self.has_any_eid(conversation_id, [eid])

    async def has_any_eid(self, conversation_id: uuid.UUID, eids: list[str]) -> bool:
        """Whether ANY of these hook event ids is already materialized —
        matching ``meta.eid`` or a coalesced message's ``meta.eids`` list, so
        a redelivered flush of an already-landed message is recognized by
        whichever of its constituent ids it arrives under."""
        if not eids:
            return False
        # Two lookups, each written with the exact expression its index is
        # built on (`indexed_rows`), so the planner can use the index even on
        # the generic plan a cached statement switches to.
        by_eid = (
            select(Block.id)
            .where(
                Block.conversation_id == conversation_id,
                text(f"{EID.text} = ANY(:eids)").bindparams(
                    bindparam("eids", eids, type_=ARRAY(Text))
                ),
            )
            .limit(1)
        )
        if await self._session.scalar(by_eid) is not None:
            return True
        coalesced = (
            select(Block.id)
            .where(
                Block.conversation_id == conversation_id,
                COALESCED_ROWS,
                # meta is JSON (not JSONB); cast the array for `?|`
                # (jsonb "contains any of these strings").
                cast(Block.meta["eids"], JSONB).op("?|")(array(eids, type_=Text)),
            )
            .limit(1)
        )
        return await self._session.scalar(coalesced) is not None

    async def last_said_in_turn(
        self, conversation_id: uuid.UUID, turn_id: uuid.UUID
    ) -> str | None:
        """The agent's latest published message during this turn, if any.

        Read from the rows, not from the turn's in-memory bookkeeping: a turn
        closed by a backend that took it over mid-way (a deploy's handover)
        has none, and the room is what actually knows what was said."""
        stmt = (
            select(Block.content)
            .where(
                Block.conversation_id == conversation_id,
                Block.turn_id == turn_id,
                Block.kind == BlockKind.message,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def has_action(
        self, conversation_id: uuid.UUID, turn_id: uuid.UUID, action: str
    ) -> bool:
        """Whether this turn already announced this kind of action here."""
        stmt = (
            select(Block.id)
            .where(
                Block.conversation_id == conversation_id,
                Block.turn_id == turn_id,
                Block.meta["action"].as_string() == action,
            )
            .limit(1)
        )
        return await self._session.scalar(stmt) is not None

    async def delete(self, block: Block) -> None:
        await self._session.delete(block)
        await self._session.flush()

    async def mark_consumed(
        self, block_ids: list[uuid.UUID], turn_id: uuid.UUID
    ) -> None:
        """Stamp human blocks as read, into the work `turn_id`, once the
        session's runner reported it read them.

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

    async def mark_read_failed(self, block_ids: list[uuid.UUID], failure: str) -> None:
        """Say on these read blocks that the work they were read into failed
        (``READ_FAILED_META_KEY``), replacing ``meta`` as ``mark_consumed`` does."""
        if not block_ids:
            return
        stmt = select(Block).where(Block.id.in_(block_ids))
        for block in (await self._session.scalars(stmt)).all():
            block.meta = {**(block.meta or {}), READ_FAILED_META_KEY: failure}
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
        self,
        conversation_id: uuid.UUID,
        author: str,
        *,
        message: uuid.UUID | None = None,
    ) -> Block | None:
        """The newest checklist message ``author`` posted in the conversation,
        or, given ``message``, that checklist whoever wrote it: whether its
        writer may edit it is the edit's own rule."""
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.message,
                Block.author == author if message is None else Block.id == message,
                Block.meta[CHECKLIST_META_KEY].as_string().is_not(None),
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

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

    async def latest_artifact(self, conversation_id: uuid.UUID) -> Block | None:
        """The topic's current preview (spec §9.1): the most recent artifact block
        芝士 pointed at. Newest wins — re-running `cheese show` repoints it."""
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.artifact,
            )
            .order_by(Block.created_at.desc())
        )
        return (await self._session.scalars(stmt)).first()

    async def shown_in_room(self, conversation_id: uuid.UUID) -> list[Block]:
        """这个房间里摆出来过的东西，每样一次，新的在前 (#1085 结论四)。

        一个房间常有好几样东西值得摆出来 —— 一份改好的 .docx、一张图、一个跑起来
        的应用 —— 而「当前预览」只说得出最后那一样。同一份被重新摆过几次只算一
        样：那是同一个东西的新一次渲染，不是又一件东西。
        """
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.artifact,
            )
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
