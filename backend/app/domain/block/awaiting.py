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

`_awaiting_statements` 也搬过来了，仍是这个基类的属性：`_awaiting_statement` 是
classmethod，`cls._awaiting_statements` 走 MRO 拿到同一个 dict 对象，建一次处处复用
的性质不变（量出来的那 406µs / 449µs 省在这里）。
"""

import uuid
from datetime import datetime
from typing import Literal, overload

from sqlalchemy import (
    DateTime,
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

from app.domain.block.asked import Awaited, may_answer, settled, skipped_by
from app.domain.block.authorship import participant_blocks
from app.domain.block.indexed_rows import (
    QUESTION_ROWS,
)
from app.domain.block.models import (
    Block,
    BlockKind,
)
from app.domain.identity.handles import looks_like_agent_handle


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
        """Return addressed person and exact question for each waiting
        conversation."""
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
    ) -> dict[uuid.UUID, tuple[Awaited, uuid.UUID, str]]: ...

    #: 等一道题的地方（频道、活、支线）一屏最多七百来个，下面两句查询都只把这些 id
    #: 当**参数**收进来。按名绑定的数组参数，语句本身与调用无关，于是可以建一次、
    #: 处处复用：原先每次调用重建一遍，量出来这两句的构造本身约 406µs / 449µs（中位）。
    #: 一次「待我处理」一共 11 句查询，原先每请求都重建一遍。语句是只读的（执行不往
    #: 它上面挂编译期状态），一份拿给并发用安全，和 `room_task/repositories.py` 的
    #: `_uuids` 是同一个写法。
    _places_param = bindparam("places", type_=ARRAY(Uuid))
    _ats_param = bindparam("ats", type_=ARRAY(DateTime(timezone=True)))
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
                Block.created_at,
                Block.author,
                Block.id,
                Block.content,
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

    @classmethod
    def _build_spoke_statement(cls) -> Select:
        # 下面这一趟只回答「说过话的时间 > 提问时间吗」，每个对话的界限不一样，所以
        # 界限**按行带进查询**：两张同样长的数组（哪一段对话、它的提问时刻）展开成
        # 一个派生表，再按它横向读一次区块。以前是拿「最早那道题」当所有对话的共同
        # 下界、写成一个 `created_at > <那个时刻>` 的区间，那要分两趟问（人和芝士各
        # 一趟），而且非得把整张表按对话分组算一遍 —— 规划器在这里会走一次全表哈希
        # 连接（本机 48 万条区块实测 134ms），横向读一张按 (conversation_id,
        # created_at) 排的索引才是每段对话读一小段（约 2ms）。题上没记是谁、说话
        # 的人是哪一类（芝士还是人）在下面按题分，不再各问一趟。
        asked_places = (
            func.unnest(cls._places_param, cls._ats_param)
            .table_valued("place", "asked_at")
            .render_derived()
        )
        said_since = (
            select(Block.author.label("author"), func.max(Block.created_at).label("at"))
            .where(
                Block.conversation_id == asked_places.c.place,
                Block.created_at > asked_places.c.asked_at,
                Block.kind == BlockKind.message,
                participant_blocks(),
            )
            .group_by(Block.author)
            .lateral("said_since")
        )
        return select(
            asked_places.c.place, said_since.c.author, said_since.c.at
        ).select_from(asked_places.join(said_since, true()))

    async def _awaiting_an_answer(self, place_ids: list[uuid.UUID], with_blocks=False):
        if not place_ids:
            return {}
        selected = {}
        rows = []
        for place_id, meta, at, asker, block_id, content in (
            await self._session.execute(
                self._awaiting_statement("places", self._build_places_statement),
                {"places": list(place_ids)},
            )
        ).all():
            if place_id is None:
                continue
            meta = meta or {}
            if settled(meta):
                # Answered by anyone, or skipped by everyone it names
                # (`block.asked`).
                continue
            rows.append((place_id, meta, at, asker, block_id, content))
        if not rows:
            return {}
        # 没点按钮、直接打字回了一句，也是回应过了：题问出来之后，被问的那个人
        # （题上没记是谁，就任何一个人）在这里说过话，这道题就不再挂在他身上。
        # 这一趟怎么问、为什么这样问，在 `_build_spoke_statement` 上面。
        last_said: dict[uuid.UUID, dict[str, datetime]] = {}
        # 芝士问完，自己又在同一条线上接着说了一句 —— 那道题它自己已经绕过去了，
        # 不该继续挂在被问的人身上 (#2046：芝士问完没等回答就自己做完、又发了几句
        # 进展，房间却一直停在「待回答」，直到人真去点一下才灭)。人问的题不适用：
        # 人问完再补一句，不等于他不用答了。所以这里只认**提问那条消息的作者**，
        # 且他本人是芝士 —— 别的席位上有人说话，是那名参与者的发言，不是这道题的
        # 提问者收回了它。
        said_by_agent: dict[uuid.UUID, dict[str, datetime]] = {}
        for place_id, author, at in (
            await self._session.execute(
                self._awaiting_statement("spoke", self._build_spoke_statement),
                # 两张数组按行对齐：同一个 `rows` 顺序，错开一位就答错问题了。
                {"places": [row[0] for row in rows], "ats": [row[2] for row in rows]},
            )
        ).all():
            said_by = said_by_agent if looks_like_agent_handle(author) else last_said
            said_by.setdefault(place_id, {})[author] = at
        waiting: dict[uuid.UUID, Awaited] = {}
        contents: dict[uuid.UUID, str] = {}
        for place_id, meta, asked_at, asker, block_id, content in rows:
            awaited = Awaited.of(meta)
            # Someone who skipped it said he will not answer: what he says
            # afterwards is talk, not his answer.
            said = {
                who: at
                for who, at in last_said.get(place_id, {}).items()
                if who not in awaited.skipped
            }
            if awaited.asked:
                times = [said[h] for h in awaited.asked if h in said]
            else:
                times = list(said.values())
            if any(at > asked_at for at in times):
                continue
            if looks_like_agent_handle(asker):
                own = said_by_agent.get(place_id, {}).get(asker)
                if own is not None and own > asked_at:
                    continue
            waiting[place_id] = awaited
            selected[place_id] = block_id
            contents[place_id] = content or ""
        if with_blocks:
            return {
                place: (asked, selected[place], contents[place])
                for place, asked in waiting.items()
            }
        return waiting

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
        after that is talk, not his answer. It is the same reading as
        `_awaiting_an_answer`'s "spoke after it was asked".
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
            # 经了结的题，否则第二个人再点一下就又记一条。判据和下面那条路、和
            # `_awaiting_an_answer` 一致（`block.asked.settled`）。
            if settled(parent.meta) or not may_answer(parent.meta, reply.author):
                return []
            return [parent]
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
                or_(spoke_before.is_(None), Block.created_at > spoke_before)
                if first_word_only
                else true(),
            )
            .order_by(Block.created_at, Block.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # 了结了的不收（和 `_awaiting_an_answer` 的判据一致），他自己跳过的也不收：
        # 跳过之后他说的话是说话，不是回答。上面的 SQL 只是先筛一遍，谁能答由
        # `may_answer` 定，和点选项、跳过同一条。
        return [
            block
            for block in (await self._session.scalars(stmt)).all()
            if may_answer(block.meta, reply.author)
            and not settled(block.meta)
            and reply.author not in skipped_by(block.meta)
        ]
