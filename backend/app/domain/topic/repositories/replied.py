"""有人回复了我、我还没处理（侧栏那盏黄灯）。

从 `repositories.py` 拆出来的一段。判据和「角标未读数」共用一份，但这条查询是
整目录里最贵的一条，所以它按看的人把语句建一次留着（见
`_replied_to_me_statement`），和角标那几份每次现拼的语句分开住。

`TopicRepository.replied_to_me` 是转发。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import (
    Select,
    Uuid,
    and_,
    bindparam,
    func,
    or_,
    select,
)
from sqlalchemy.orm import aliased

from app.domain.event_author import AuthorType
from app.domain.topic.models import TopicReadState
from app.domain.topic.repositories.statements import (
    KIND_MESSAGE,
    _blocks,
    _mentions,
    _threads,
)

if TYPE_CHECKING:
    from app.domain.topic import repositories as pkg


#: 「有人回复了我、我还没处理」（侧栏那盏黄灯）那条查询的两份清单参数。房间按百计，所以
#: 走数组参数；`expanding` 让它在 SQL 里照样摊成 `IN ($1, $2, …)` —— 和原先直接
#: `in_(room_ids)` 生成的文本逐字节相同，执行计划不变。
_replied_rooms = bindparam("replied_rooms", type_=Uuid, expanding=True)
#: 这些房间里的支线 id，不是房间 id 本身（两者都是 uuid，但判据完全不同）。
#: `_replied_to_me_statement` 的说明写了为什么必须是支线 id。
_replied_inner = bindparam("replied_inner", type_=Uuid, expanding=True)


#: 一间房有哪些支线。`replied_to_me` 先跑这一条（`ix_threads_room_id` 上的索引扫，
#: 0.018 ms），把结果当上面第二份清单交给主查询。
_room_thread_ids = select(_threads.c.id).where(_threads.c.room_id.in_(_replied_rooms))


@lru_cache(maxsize=64)
def _replied_to_me_statement(user_handle: str) -> Select:
    """`TopicRepository.replied_to_me` 的那条查询，按看的人建一次留着。

    每个请求都来拼一遍这个多连接、带 `exists()` 的语句，量出来光构造本身就约
    4.6ms（2026-10-14，48 万区块的库，一次频道详情）。房间里**谁在看**烧进了
    「提到我」那条 JSONB 判据（`_mentions`），所以缓存的键是人；房间本身只以参数
    进来。语句是只读的，一份拿给并发用安全（`block/repositories.py` 的
    `_awaiting_statements` 是同一个写法）。

    两份清单参数：`replied_rooms` 是房间本身，`replied_inner` 是这些房间里的支线
    id（调用方先用 `_room_thread_ids` 读出来）。第二份传支线 id 而不是房间 id，
    是这条语句唯一的形状要求 —— 见下面那个 OR 的注释。
    """
    # 「回复我发的消息」：另说的一条块指着我写的某一块。别名和 `unread_counts`
    # 里 `took_part` 那一处同一个写法 —— exists() 要关联到外层那张表，就不能
    # 用同一张。
    mine = _blocks.alias()
    answers_mine = (
        select(mine.c.id)
        .where(mine.c.id == _blocks.c.reply_to, mine.c.author == user_handle)
        .exists()
    )
    # 同一个人在两处已读：这间房自己那一位（主线取它），和这条块所在那条线的
    # 那一位（支线里的块取它，主线上的块两处是同一位）。
    room_state = aliased(TopicReadState)
    line_state = aliased(TopicReadState)

    def read_state_of(state, conversation):
        return and_(
            state.topic_id == conversation,
            state.user_handle == user_handle,
        )

    def past(state):
        """这条块在那一位后面吗。没有那一行 = 那一条线从没打开过，全都还没读。"""
        return or_(state.last_read_seq.is_(None), _blocks.c.seq > state.last_read_seq)

    def opened_before(state):
        """这个人上次打开这个频道，是在这条块落地之前吗（从没打开过 = 是）。

        已读位那行的时间戳就是「上次打开」：`mark_read` 每回把它推到现在
        （`TopicReadRepository.mark_read_many` 的 upsert）。
        """
        return or_(state.updated_at.is_(None), state.updated_at < _blocks.c.created_at)

    return (
        select(_blocks.c.conversation_id, _threads.c.room_id)
        .outerjoin(_threads, _threads.c.id == _blocks.c.conversation_id)
        .outerjoin(
            room_state,
            read_state_of(
                room_state, func.coalesce(_threads.c.room_id, _blocks.c.conversation_id)
            ),
        )
        .outerjoin(line_state, read_state_of(line_state, _blocks.c.conversation_id))
        .where(
            # 两个分支都约束 `blocks.conversation_id`（`blocks` 上每个索引的首列），
            # 规划器才能把房间收窄完再去碰 `blocks`。原来第二支写的是外连接进来的
            # `threads.room_id IN (…)`，它要等连接之后才算得出来，整个 OR 于是只能
            # 全表扫：48 万区块的库里，一个只有 51 个块的房间也要 136 ms；改成
            # 支线 id 清单是 0.130 ms（`docs/evidence/`
            # `channel-page-20-concurrency-2026-10-15.md` §2，七种写法的执行计划
            # 在 `explain-channel-detail.txt`）。
            or_(
                _blocks.c.conversation_id.in_(_replied_rooms),
                _blocks.c.conversation_id.in_(_replied_inner),
            ),
            _blocks.c.kind == KIND_MESSAGE,
            _blocks.c.author_type == AuthorType.participant,
            _blocks.c.author != user_handle,
            # 两处都还没读过才算「我还没处理」（见上）：这条块所在的那条线
            # 没读过，这个频道也没在它落地之后打开过。
            past(line_state),
            opened_before(room_state),
            or_(_mentions(user_handle), answers_mine),
        )
    )


async def replied_to_me(
    repo: pkg.TopicRepository,
    room_ids: Collection[uuid.UUID],
    user_handle: str,
) -> set[uuid.UUID]:
    """Which of these channels hold an answer to this person that they have
    not opened the channel since.

    An answer TO them is a message from somebody else that either replies to
    a block they wrote (`reply_to`) or names them (`refs`) — the two cases
    the person named. AI teammates count, the platform's own events do not
    (`author_type`).

    HANDLED is having read the answer, and reading it is opening the line it
    landed on. Opening the CHANNEL counts even when the answer landed in a
    支线 under it ("打开这个频道就算处理了" — what the person said
    counts): the read-state row remembers when the channel was last opened,
    and that moment being after the answer landed is enough. Reading that
    支线 in its own pane counts too, and is what its own cursor moves.
    Either one is enough, so "not handled" is neither — and the two are read
    differently because they are different kinds of number: a cursor counts
    one conversation's blocks, and 支线 number theirs separately, so the
    channel's cursor says nothing about a 支线's block. Only the moment can
    be compared across the two.

    The channel's own line and its 支线 count. The work dispatched under it
    does not: a task has a row of its own, and whether it waits on somebody
    is that row's own fact.
    """
    if not room_ids:
        return set()
    # 语句按看的人建一次留着，两份清单每次当参数给（`_replied_to_me_statement`）。
    rooms = list(room_ids)
    # 这些房间的支线 id 先单独读一趟（`ix_threads_room_id`，0.018 ms），主查询
    # 才能拿它去收窄 `blocks`。多这一条查询，换掉一次 48 万行的整表扫。
    thread_ids = list(
        (await repo._session.scalars(_room_thread_ids, {"replied_rooms": rooms})).all()
    )
    return {
        room or conversation
        for conversation, room in (
            await repo._session.execute(
                _replied_to_me_statement(user_handle),
                {"replied_rooms": rooms, "replied_inner": thread_ids},
            )
        ).all()
    }
