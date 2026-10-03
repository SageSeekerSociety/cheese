"""一间干过活的房间还没有实况文档时，平台提醒一次在这里干活的队友补上。

提示词里「维护文档」说得再清楚，建不建第一版仍取决于坐进房间的是哪个模型：本
项目 2026-09 前后 30 天，Claude 系的队友在二十几间房里建过文档，Kimi / MiMo 一
篇都没建过。文档是给没参与讨论的人看的，它有没有不该看模型的脾气，所以一轮结束
时平台自己看一眼——

* **是一间工作话题**：`topic` 类、进行中，而且是按房间的规矩来的一间。项目根话题
  是 `root` 类，总览另有一套；私聊不是这里要补的那间房，因为提示词根本不会给私聊
  「本话题还没有实况文档」那一段（见下）。
* **干过活**：房间里 agent 已经调过 :data:`MIN_TOOL_EVENTS` 次以上工具、发过至少一
  条消息。寒暄、一句话就答完的问题到不了这条线。
* **文档还空着**：房间自己那份文档块不存在，或只有空白。
* **没提醒过**：房间里还没有一条 :data:`EVENT_DOC_MISSING` 事件。提醒只有一次——
  队友看了提醒仍不写，再催就是打扰；那条事件本身就是给人看的线索。

四条都成立，就点名刚才在这里干活的那个队友（名册上的 agent 席位里最近一个动过手
的；找不到就回落到房间的默认席位），用一条平台事件起一轮让它补第一版。

**「是不是工作房间」不由这里回答。** 提醒要跟提示词说同一句话：`agent/chat.py`
给不给「本话题还没有实况文档」那一段，问的是 `_assemble_turn` 的 `needs_place`，
而那是 `_is_dm`（`is_private` 全仓唯一的读点）推出来的两个答案之一。本模块再问一遍
那个布尔，就是同一件事多一份会漂移的声明（结论 19、ARCH §9.1 判据②），所以答案
经 ``chat_service`` 带进来（`ChatService.room_is_a_work_room`，`nudge` 经它取）。

和 :mod:`app.domain.topic.naming` 一样全程安静失败：它是锦上添花，不能让结束的那
一轮因为它出错。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.platform_notices import (
    EVENT_DOC_MISSING,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.block.models import Block, BlockKind
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
#: (room_id, 点名的 agent 席位, 给 agent 的提示词, 房间里那一行, 事件 meta)
Submit = Callable[[uuid.UUID, str, str, str, dict], Awaitable[None] | None]
#: 这间房按不按房间的规矩来（实况文档、租地点）。由调用方从 `is_private` 唯一的
#: 读点（`agent/chat.py` 的 `_is_dm`）推出来带进来，见模块开头。
WorkRoom = Callable[[Topic], bool]

#: 「干过活」的门槛：agent 在这间房里调过的工具次数。一次寒暄通常是零到两次。
MIN_TOOL_EVENTS = 5
#: 一轮刚结束时，它最后那几次写入（包括也许正是 `cheese_doc_set`）可能还没落库。
SETTLE_S = 8.0

EVENT_LINE = say("docMissing")
#: 只说「现在建」，不复述怎么写：怎么写是系统提示词里「当前话题的实况文档」那一节
#: （`harness/prompt.py` 的 `DOC_FORM`），这一轮的提示词里本来就有。这里再抄一份
#: 模板，两份迟早不一样，而这条提醒是用户消息，模型会照它写。
PROMPT = (
    "本话题已经有了实质进展，但实况文档还是空的。\n"
    "请现在先 `cheese_doc_get`，再用 `cheese_doc_set` 建第一版，按系统提示词里"
    "「当前话题的实况文档」一节写。\n"
    "写完不必在聊天里另外宣布。这是平台对本话题唯一一次这样的提醒。"
)


def _default_factory() -> SessionFactory:
    from app.core.db import async_session_factory

    return async_session_factory


async def _reminded(session: AsyncSession, room_id: uuid.UUID) -> bool:
    stmt = (
        select(Block.id)
        .where(
            Block.topic_id == room_id,
            Block.kind == BlockKind.event,
            Block.meta["event_type"].as_string() == EVENT_DOC_MISSING,
        )
        .limit(1)
    )
    return (await session.scalar(stmt)) is not None


async def _doc_is_empty(session: AsyncSession, room_id: uuid.UUID) -> bool:
    # 走本领域的 service 读它，不是直接摸 block 的 repository：那条边绕过了
    # 「哪些行算这间房的、线程的那份不算」这层判断
    # （tests/unit/test_domain_import_guard.py）。
    #
    # 就地 import 而不是写在上头：`topic.services` 往上 import 到
    # `notification.services`，那一头又 import `agent.runtime`，而 runtime import
    # 本模块 —— 模块级的这条边会绕回自己，`import app.api.deps` 当场就炸。
    from app.domain.topic.services import TopicService

    doc = await TopicService(session).doc_of_room(room_id)
    return doc is None or not (doc.content or "").strip()


async def _worked(session: AsyncSession, room_id: uuid.UUID, agents: list[str]) -> bool:
    tool = Block.meta["tool"].as_string()
    stmt = select(
        func.count().filter(tool.is_not(None)),
        func.count().filter(Block.kind == BlockKind.message),
    ).where(Block.topic_id == room_id, Block.author.in_(agents))
    tools, messages = (await session.execute(stmt)).one()
    return int(tools or 0) >= MIN_TOOL_EVENTS and int(messages or 0) >= 1


async def _acting_agent(
    session: AsyncSession, room_id: uuid.UUID, agents: list[str]
) -> str | None:
    """名册上最近一个在这间房里动过手的 agent 席位。"""
    stmt = (
        select(Block.author)
        .where(Block.topic_id == room_id, Block.author.in_(agents))
        .order_by(Block.created_at.desc())
        .limit(1)
    )
    latest = await session.scalar(stmt)
    if latest:
        return latest
    return await TopicMemberService(session).addressable_agent_handle(room_id)


async def check(
    room_id: uuid.UUID,
    *,
    submit: Submit,
    is_a_work_room: WorkRoom,
    session_factory: SessionFactory | None = None,
) -> bool:
    """看一眼 ``room_id``；该提醒就提醒，返回是否提醒了。

    ``is_a_work_room`` 由调用方带进来：全仓只有一个地方读 ``is_private``
    （``agent/chat.py`` 的 ``_is_dm``），这里要的正是它推出来的那个答案。
    """
    redis = get_redis_client()
    lock = f"doc-nudge:lock:{room_id}"
    if redis is not None:
        try:
            # 两个收尾点（会话自报结束 / 调度器收尾）撞在同一刻时只让一个往下走。
            if not await redis.set(lock, "1", nx=True, ex=120):
                return False
        except Exception:  # noqa: BLE001 — 够不着 Valkey 就靠下面的事件检查
            logger.info("doc nudge could not reach valkey", exc_info=True)
    factory = session_factory or _default_factory()
    async with factory() as session:
        room = await session.get(Topic, room_id)
        if (
            room is None
            or room.kind != TopicKind.topic
            or room.status != TopicStatus.active
            or not is_a_work_room(room)
        ):
            return False
        if not await _doc_is_empty(session, room_id):
            return False
        if await _reminded(session, room_id):
            return False
        agents = await TopicMemberService(session).agent_handles(room_id)
        if not agents or not await _worked(session, room_id, agents):
            return False
        seat = await _acting_agent(session, room_id, agents)
    if seat is None:
        return False
    meta = notice(EVENT_DOC_MISSING, severity=SEVERITY_INFO, who=WHO_CHEESE)
    result = submit(room_id, seat, PROMPT, EVENT_LINE, meta)
    if asyncio.iscoroutine(result):
        await result
    logger.info("doc nudge sent to %s in room %s", seat, room_id)
    return True


def runner_submit(chat_service) -> Submit:
    """真正起一轮的那个 `submit`：交给 agent 调度器，房间里落一条平台事件。"""

    def _submit(
        room_id: uuid.UUID, seat: str, prompt: str, line: str, meta: dict
    ) -> None:
        from app.api.deps import get_work_runner
        from app.domain.agent.runtime import addressed_to_agent

        get_work_runner().submit(
            chat_service,
            room_id,
            author="system",
            content=prompt,
            addressed=addressed_to_agent(seat),
            nudge_event=line,
            nudge_meta=meta,
        )

    return _submit


_tasks: set[asyncio.Task] = set()


def nudge(room_id: uuid.UUID, chat_service, *, settle_s: float = SETTLE_S) -> None:
    """一轮在 ``room_id`` 结束了。Fire and forget，调用方不等、也不会因它出错。

    「是不是工作房间」问的是 ``chat_service.room_is_a_work_room``（`agent/chat.py`
    上那个静态方法，见模块开头），**在下面那个任务里取，不在调用点上取**：轮末的
    收尾点紧接着要摘这轮的存活标记（`runtime.py` 的 ``self._live.pop``），那儿多抛
    一句出去，这一轮就永远是「在跑」。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run_quietly(room_id, chat_service, settle_s))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _run_quietly(room_id: uuid.UUID, chat_service, settle_s: float) -> None:
    # `session_factory` 也是这么取的：手上这个 `chat_service` 是鸭子类型的对象
    # （`runtime.py` 不 import `chat.py`，测试里还有替身），字段不在就是没有这回事。
    is_a_work_room: WorkRoom | None = getattr(chat_service, "room_is_a_work_room", None)
    if is_a_work_room is None:
        return
    try:
        await asyncio.sleep(settle_s)
        await check(
            room_id,
            submit=runner_submit(chat_service),
            is_a_work_room=is_a_work_room,
            session_factory=getattr(chat_service, "session_factory", None),
        )
    except Exception:  # noqa: BLE001 — 提醒失败不打扰任何人
        logger.warning("doc nudge failed for %s", room_id, exc_info=True)
