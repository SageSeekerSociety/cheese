"""哪几段对话停在一个没人回答的提问上 —— 以及一条回话答了哪几道题。

`repositories.py` 拆分前是 1199 行，一个文件里同时住着写入、单块小读、时间线读、问答
读和表情读写。这一块是其中的问答读；拆开的理由是文件体积，不是分层：它和
`repositories.py` 是同一件事的两半，方法签名与 SQL 逐字未动（`git diff` 里那几段是
整段搬迁），`BlockRepository` 继承这里的 `AwaitingQueries`。

所以 `from app.domain.block.repositories import BlockRepository` 的取法照旧，按**类
对象**打的 monkeypatch 也照旧 —— 包括
`scripts/perf-awaiting-me/measure_awaiting_me.py` 按
`BlockRepository.awaiting_answer_blocks` 打的那一条：它先拿到类再取属性，方法从哪个
基类来不影响它。

和 `waits.py` 不是一回事：那一份问的是这个房间在等**哪个人**，这一份问的是哪几段对话
停在一个未回答的提问上（#1084）。判据只有一个来源 —— `block.asked` 的 `settled` /
`may_answer` / `skipped_by` —— 「还等着谁」（`awaiting_an_answer`）和「这下答上了没
有」（`questions_a_reply_answers`）是同一个判据的两个方向，所以住在一起。

`_awaiting_statements` 仍是这个基类的属性：`_awaiting_statement` 是 classmethod，
绑定参数的查询建一次处处复用。待回答只读题上的结题记录，不再另查题后的发言。
"""

import uuid
from datetime import datetime
from typing import Literal, overload

from sqlalchemy import (
    Select,
    Text,
    Uuid,
    any_,
    bindparam,
    cast,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.block.asked import (
    Awaited,
    answerable,
    may_answer,
    settled,
    skipped_by,
)
from app.domain.block.authorship import participant_blocks
from app.domain.block.indexed_rows import (
    QUESTION_ROWS,
)
from app.domain.block.models import (
    Block,
    BlockKind,
)


class AwaitingQueries:
    """停在一个未回答的提问上的那几段对话，和一条回话答了哪几道题。

    没有 `__init__`：session 由 `BlockRepository.__init__` 装上来；这些方法本来
    就只碰 `self._session` 和类属性，放在基类里与放在原来的类里是同一件事。
    """

    _session: AsyncSession

    async def awaiting_an_answer(
        self, conversation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Awaited]:
        """这些对话里，哪几段停在一个未回答的提问上，各自在等谁 —— 一次查完。

        判据是 #1084 定的那一条：**最近一条提问消息没有作答记录**。不需要新增
        存储，因为回答本来就记在提问那一块上（`meta.answer_log`，答过它的每一句
        回话）。取「最近一条」而不是「有没有任何一条」：已回答的旧提问不该让这段
        对话长期停留在待处理。任务的负责人点了「开始」，在那之前问的题也不再算：
        开始就是他说这件事讨论够了。

        等谁也记在那一块上（`meta.asked`，提问那一刻写下的；跳过了的人不再等，见
        `block.asked`）。空的是「这道题指不到具体的人」。只关心停没停的调用方照样拿
        它做 `in`。走 `ix_blocks_questions`。
        """
        return await self._awaiting_an_answer(conversation_ids)

    async def awaiting_answer_blocks(self, conversation_ids):
        """Return addressed person, exact question, its text and when it was
        asked, for each waiting conversation."""
        return await self._awaiting_an_answer(conversation_ids, with_blocks=True)

    @overload
    async def _awaiting_an_answer(
        self,
        place_ids: list[uuid.UUID],
        with_blocks: Literal[False] = False,
    ) -> dict[uuid.UUID, Awaited]: ...

    @overload
    async def _awaiting_an_answer(
        self,
        place_ids: list[uuid.UUID],
        with_blocks: Literal[True],
    ) -> dict[uuid.UUID, tuple[Awaited, uuid.UUID, str, datetime]]: ...

    #: 频道、活、支线的 id 作为按名绑定的数组参数传入，查询本身建一次处处复用。
    #: 语句是只读的，并发执行不往语句上挂编译期状态。
    _places_param = bindparam("places", type_=ARRAY(Uuid))
    _awaiting_statements: dict[str, Select] = {}

    @classmethod
    def _awaiting_statement(cls, key: str, build) -> Select:
        """建一次就留着；绑定值每次执行时按名给（`keys` 见上面的参数）。"""
        statement = cls._awaiting_statements.get(key)
        if statement is None:
            statement = build()
            cls._awaiting_statements[key] = statement
        return statement

    @classmethod
    def _build_places_statement(cls) -> Select:
        place_column = Block.conversation_id.label("place_id")
        # `in_()` 会把这一串 id 摊成同样多个绑定参数，每执行一次都要重新拼一遍
        # SQL（一屏「待我处理」要拿七百来个对话来问，量出来光这一步就 8~9 毫秒）。
        # 收成一个数组参数（`_places_param`）之后语句只编译一次，后面每次都由驱动
        # 直接复用。
        #
        # 每段只取最近一题（#1084 的原语义），连同**题文**一起回来：清单第二行要
        # 显示题文，本来就要和这一趟的答案并起来读，再问一趟就是白白多一次往返。
        latest = (
            select(
                place_column,
                Block.meta,
                Block.id,
                Block.content,
                # 这一行右边那个时刻是**题**问出来的时刻，不是频道最后被改的时刻
                # （`awaiting_deps` 的频道行原先取 `topic.updated_at`，一道 10-13 的
                # 题因此显示成 10-12）。
                Block.created_at,
            )
            .where(
                place_column == any_(cls._places_param),
                # 和 `ix_blocks_questions` 的谓词是同一个对象，规划器才认得出
                # 能用那个部分索引。
                QUESTION_ROWS,
            )
            .order_by(place_column, Block.created_at.desc())
            .distinct(place_column)
            .subquery()
        )
        return select(latest)

    async def _awaiting_an_answer(self, place_ids: list[uuid.UUID], with_blocks=False):
        if not place_ids:
            return {}
        if not with_blocks:
            found_cache, cached = recall(self._session, "awaiting_an_answer")
            known: dict[uuid.UUID, Awaited | None] = dict(cached) if found_cache else {}
            query_ids = [pid for pid in dict.fromkeys(place_ids) if pid not in known]
            if not query_ids:
                return {
                    pid: val for pid in place_ids if (val := known.get(pid)) is not None
                }
        else:
            known = {}
            query_ids = list(place_ids)
        selected = {}
        waiting: dict[uuid.UUID, Awaited] = {}
        contents: dict[uuid.UUID, str] = {}
        asked_at: dict[uuid.UUID, datetime] = {}
        for place_id, meta, block_id, content, created_at in (
            await self._session.execute(
                self._awaiting_statement("places", self._build_places_statement),
                {"places": query_ids},
            )
        ).all():
            if place_id is None or settled(meta):
                continue
            # Answers and skips are recorded on the question. Later talk — by
            # its addressee or by the agent who asked — is not another outcome.
            waiting[place_id] = Awaited.of(meta)
            selected[place_id] = block_id
            contents[place_id] = content or ""
            asked_at[place_id] = created_at
        if with_blocks:
            return {
                place: (asked, selected[place], contents[place], asked_at[place])
                for place, asked in waiting.items()
            }
        for pid in query_ids:
            known[pid] = waiting.get(pid)
        remember(self._session, "awaiting_an_answer", known)
        return {pid: val for pid in place_ids if (val := known.get(pid)) is not None}

    async def questions_a_reply_answers(
        self, reply: Block, *, first_word_only: bool = True
    ) -> list[Block]:
        """The questions a person's ``reply`` answers, locked for the caller to
        record the answer on.

        A reply to a question (`reply_to` — what clicking one of its options
        sends) answers that question, but only while it is unanswered: a
        question takes one answer, so a reply to one already answered records
        nothing. Nor does a reply from someone who may not answer it
        (`block.asked.may_answer`): it is talk about the question — a teammate
        quoting it to say which option he would pick — posted like any other
        message, not its answer. A reply to any other message answers nothing.

        A message that replies to nothing answers the open questions in its
        conversation its author may answer (`block.asked.may_answer`: asked of
        him, among others, of everyone in the channel, or of nobody in
        particular), that he has not skipped and that nobody has answered yet,
        but only when it is his first word since they were asked: what he says
        after that is talk, not his answer. Only an answer recorded on the
        question changes the waiting state; talk on its own does not.
        ``first_word_only=False`` drops that last condition, for a message the
        caller knows is addressed to an asker.
        Whom it names is the caller's to weigh.
        """
        place = (Block.conversation_id == reply.conversation_id,)
        if reply.reply_to is not None:
            parent = await self._session.scalar(
                select(Block)
                .where(Block.id == reply.reply_to, *place, QUESTION_ROWS)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                return []
            # 一道题只收一个回答：点选项那条路（`reply_to` 指过来的就是它）也拦下已
            # 经了结的题，否则第二个人再点一下就又记一条。判据是 `answerable`，不是
            # `settled`：过期收起来的那道题仍答得上（题卡留着输入框），而撤回过、
            # 有人答过、被所有人跳过的都答不上。
            if not answerable(parent.meta) or not may_answer(parent.meta, reply.author):
                return []
            return [parent]
        asked_filter = (
            *place,
            QUESTION_ROWS,
            # `meta` is json, not jsonb: cast to ask "does it name him". `@>` of
            # a string holds for a list containing it and for the bare handle a
            # backend from before the list wrote (blue/green deploys keep one
            # running past the migration); json_array_length would raise on it.
            or_(
                cast(Block.meta, JSONB)["asked"].op("@>")(
                    func.to_jsonb(cast(reply.author, Text))
                ),
                cast(Block.meta, JSONB)["asked"] == func.jsonb_build_array(),
                cast(Block.meta, JSONB)["asked_all"].as_boolean().is_(True),
            ),
            Block.meta["answered"].as_string().is_(None),
            Block.id != reply.id,
            Block.created_at <= reply.created_at,
        )
        candidates = [
            block.id
            for block in (
                await self._session.scalars(
                    select(Block)
                    .where(*asked_filter)
                    .order_by(Block.created_at, Block.id)
                )
            ).all()
            if may_answer(block.meta, reply.author)
            and answerable(block.meta)
            and reply.author not in skipped_by(block.meta)
        ]
        if not candidates:
            return []
        spoke_before = (
            select(func.max(Block.created_at))
            .where(
                *place,
                Block.kind == BlockKind.message,
                participant_blocks(),
                Block.author == reply.author,
                Block.id != reply.id,
            )
            .scalar_subquery()
        )
        stmt = (
            select(Block)
            .where(
                Block.id.in_(candidates),
                *asked_filter,
                or_(spoke_before.is_(None), Block.created_at > spoke_before)
                if first_word_only
                else true(),
            )
            .order_by(Block.created_at, Block.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # 答不上的不收（`answerable`，和点选项、跳过同一条）：已经有人答过、被所有人
        # 跳过、或者被提问的那位撤回的；过期的除外，那道题还收回答。他自己跳过的也
        # 不收：跳过之后他说的话是说话，不是回答。上面的 SQL 只是先筛一遍，谁能答由
        # `may_answer` 定。
        return [
            block
            for block in (await self._session.scalars(stmt)).all()
            if may_answer(block.meta, reply.author)
            and answerable(block.meta)
            and reply.author not in skipped_by(block.meta)
        ]
