"""平台在一轮还跑着的时候，自己往这一轮里说的两句话；和做任务的会话还在跑时，
问它任务做到哪了的那一句（`StaleProgressReminder`）。

两句话问的是同一件事的两半：这一轮还在跑，而房间里看得见的东西停在原地。看得见的
有两样——话，和步骤清单——所以是两句，不是一句。分开，是因为它们能分开停：一轮可
以一直在说话而清单从开工起就是那一版（2026-10-11 报上来的正是这一种），反过来也
成立。`silence_reminder` 管前一样，`stale_checklist_reminder` 管后一样。

放进去的路和人的中途消息是同一条（`notify_running_session`）：会话开工时取的那份
快照，之后只有人打字或者平台叫它一声才能变。这里的两句就是「平台叫它一声」。

放在 `turn/steps` 而不是 `chat.py`：`chat.py` 压在自己的行数上限上（见
`.claude/rules/architecture.md` 的「Files have a size cap」）。这里只用到 `ChatService`
对外的几样东西，用 `TurnNotices` 写下来，所以不用 import 它（T2 也不许）。
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import Protocol

from sqlalchemy import Uuid, column, exists, select, table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.harness.prompt import (
    silence_reminder,
    stale_checklist_reminder,
    stale_progress_reminder,
)
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.block.repositories import BlockRepository
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.reads import conversation_progress
from app.domain.user.services import language_of

logger = logging.getLogger(__name__)


class TurnNotices(Protocol):
    """说这两句话要用到的入口，`ChatService` 正好都有这些。"""

    @property
    def live(self) -> LiveWork: ...

    @property
    def session_factory(self) -> async_sessionmaker: ...

    # Returns `bool | InputReconciliationPending` on ChatService; only whether the
    # live session took it matters here（`delivered is True`）。
    async def notify_running_session(
        self, topic_id: uuid.UUID, notice: str
    ) -> object: ...


def _still_running(chat: TurnNotices, state: HookWorkState) -> bool:
    """扫底这一趟里，这一轮可能已经收工了——那就没什么可提醒的了。"""
    return chat.live.hook_work.get(
        (state.topic_id, state.work_id)
    ) is state and state.work_id in chat.live.active_turn_ids.get(state.topic_id, ())


async def _asker_language(
    session: AsyncSession, reply_to: uuid.UUID | None
) -> str | None:
    """平台自己那句话按提问的人的语言说（`user.language`）——他就是还在等的那
    个人。查不到时是 None，说存下来的中文。"""
    asked = await BlockRepository(session).get(reply_to) if reply_to else None
    return await language_of(session, asked.author if asked is not None else None)


async def remind_silent_turns(chat: TurnNotices, now: datetime) -> int:
    """Queue an internal reminder while a room response is still running."""
    due = [
        state
        for state in chat.live.hook_work.values()
        # Background inspections have their own notification policy. Only work
        # answering a person owes a periodic chat update.
        if state.reply_to is not None
        and (
            now
            - max(
                state.last_chat_at or state.started_at,
                state.last_progress_reminder_at
                or (state.last_chat_at or state.started_at),
            )
        ).total_seconds()
        >= settings.chat_progress_reminder_after_s
        and state.work_id in chat.live.active_turn_ids.get(state.topic_id, ())
    ]

    async def remind(state: HookWorkState) -> bool:
        if not _still_running(chat, state):
            return False
        silent_for = now - (state.last_chat_at or state.started_at)
        minutes = int(silent_for.total_seconds() // 60)
        async with chat.session_factory() as session:
            language = await _asker_language(session, state.reply_to)
        try:
            # Repeats every `chat_progress_reminder_after_s` of continued
            # silence. A publication clears this and `last_chat_at` together,
            # so speaking is what stops the reminders; tool output and
            # duplicate send requests are not speaking.
            state.last_progress_reminder_at = now
            # 那句话里已经点了清单（`silence_reminder` 的末句），别让清单那条扫底
            # 在同一次沉默里又说一遍。
            state.last_checklist_reminder_at = now
            # A blocked terminal must not hold up reminders in other rooms.
            async with asyncio.timeout(5):
                delivered = await chat.notify_running_session(
                    state.topic_id, silence_reminder(minutes, language)
                )
            # THAT a reminder fired was already visible — the periodic loop logs
            # `chat progress reminder: <count>` on any cycle whose result is
            # worth reporting (app/domain/agent/periodic.py). What a count cannot
            # carry is which room, which turn, how long it had been dark, and
            # whether the transport took it, and those are the four things
            # needed to tell 「the agent was reminded and stayed quiet」 from
            # 「the reminder never reached it」.
            logger.info(
                "chat progress reminder topic=%s turn=%s silent_min=%d delivered=%s",
                state.topic_id,
                state.work_id,
                minutes,
                delivered,
            )
            return delivered is True
        except Exception:  # one room must not stop the sweep
            logger.exception("chat progress reminder failed (topic=%s)", state.topic_id)
            return False

    return sum(await asyncio.gather(*(remind(state) for state in due)))


async def remind_stale_checklists(chat: TurnNotices, now: datetime) -> int:
    """叫一轮把它自己那份停在原地的清单推到当下。

    只看这一轮自己写的那一版（`TopicProgress.turn_id`）：清单可能是上一轮留下的，
    而平台叫得动的只有此刻在跑的这一轮。清单自己的时刻在 `updated_at`——上一次写入
    的时刻；`last_checklist_reminder_at` 记的是上一次提醒的时刻，两样都要，才不会
    沉默十分钟就把同一句话说第二遍。

    阈值和沉默提醒共用一个：两个问题都是「这一轮里看得见的东西是不是停在原地」，
    一个配置项说一件事就够了。
    """
    due = [
        state
        for state in chat.live.hook_work.values()
        if (
            now - (state.last_checklist_reminder_at or state.started_at)
        ).total_seconds()
        >= settings.chat_progress_reminder_after_s
        and state.work_id in chat.live.active_turn_ids.get(state.topic_id, ())
    ]

    async def remind(state: HookWorkState) -> bool:
        if not _still_running(chat, state):
            return False
        async with chat.session_factory() as session:
            row = await conversation_progress(session, state.topic_id)
            language = await _asker_language(session, state.reply_to)
            # 这一版清单是这一轮写的吗？不是——它的作者不在跑了，平台叫不动。
            written_at = (
                row.updated_at
                if row is not None and row.turn_id == state.work_id
                else None
            )
        if written_at is None:
            return False
        stale_for = now - written_at
        if stale_for.total_seconds() < settings.chat_progress_reminder_after_s:
            return False
        minutes = int(stale_for.total_seconds() // 60)
        try:
            state.last_checklist_reminder_at = now
            async with asyncio.timeout(5):
                delivered = await chat.notify_running_session(
                    state.topic_id, stale_checklist_reminder(minutes, language)
                )
            logger.info(
                "checklist reminder topic=%s turn=%s stale_min=%d delivered=%s",
                state.topic_id,
                state.work_id,
                minutes,
                delivered,
            )
            return delivered is True
        except Exception:  # one room must not stop the sweep
            logger.exception("checklist reminder failed (topic=%s)", state.topic_id)
            return False

    return sum(await asyncio.gather(*(remind(state) for state in due)))


class ProgressNotices(Protocol):
    """问任务状态要用到的入口：一个会话工厂，和往在跑的会话里说一句话的那条路。"""

    @property
    def session_factory(self) -> async_sessionmaker: ...

    async def notify_running_session(
        self, topic_id: uuid.UUID, notice: str
    ) -> object: ...


#: The review domain's cards, read as a bare table: that domain imports this
#: one, so a card is read here by its row (as `room_task.closing` does).
_cards = table("accept_cards", column("task_id", Uuid), column("status"))
#: The statuses of a card that is still open (`review.archive.OPEN_CARD_STATUSES`,
#: which a unit test holds this copy to): while one is, the card says where the
#: task stands (等审, fixing its checks, resolving a conflict), not its teammate.
OPEN_CARD_STATUSES = ("pending", "pending_gate", "conflict")


class StaleProgressReminder:
    """做任务的会话还在跑，而它报的任务状态（`cheese_task_state`）停在原地超过
    `chat_progress_reminder_after_s`，就问它一句。

    判据都不看轮次：这个会话此刻在不在跑（``running`` 给出，会话协议声明的状态：
    哪些对话上有座位在跑），离它上一次报状态过了多久，以及此刻是不是它报的状态在说
    话 —— 任务开着、没有一张还开着的卡（卡在的时候由卡说，它报也会被拒绝）。一轮结束不是任何一件事的边界 ——
    在等构建的会话一轮结束了还在等。从没报过的，从这里第一次看见它在跑时算起。
    提醒过一次，下一次再从提醒那一刻算起，所以一直不报的会话每过一个阈值听到一次。

    记的两样（第一次看见它在跑、上一次提醒）只在这个进程里：换了进程最多早一个
    阈值多问一句，问的是一件看板上看得见的事，多问一句不伤人。
    """

    def __init__(
        self,
        chat: ProgressNotices,
        running: Callable[[AsyncSession], Awaitable[Collection[uuid.UUID]]],
    ) -> None:
        self._chat = chat
        self._running = running
        self._seen_running: dict[uuid.UUID, datetime] = {}
        self._reminded: dict[uuid.UUID, datetime] = {}

    async def __call__(self, now: datetime) -> int:
        async with self._chat.session_factory() as session:
            running = set(await self._running(session))
            # 不在跑了的那些，下一次在跑时从头算。
            for gone in set(self._seen_running) - running:
                self._seen_running.pop(gone, None)
                self._reminded.pop(gone, None)
            if not running:
                return 0
            for conversation in running:
                self._seen_running.setdefault(conversation, now)
            tasks = (
                await session.execute(
                    select(
                        Task.id,
                        Task.progress_set_at,
                        Task.owner_handle,
                    ).where(
                        Task.id.in_(running),
                        Task.status == TaskStatus.open,
                        # While a card is open it speaks for the task, and a
                        # report would be refused: nothing to ask for.
                        ~exists().where(
                            _cards.c.task_id == Task.id,
                            _cards.c.status.in_(OPEN_CARD_STATUSES),
                        ),
                    )
                )
            ).all()
            languages = {
                task.id: await language_of(session, task.owner_handle) for task in tasks
            }
        threshold = settings.chat_progress_reminder_after_s
        due: list[tuple[uuid.UUID, int | None]] = []
        for task in tasks:
            said_at = task.progress_set_at
            since = max(
                said_at or self._seen_running[task.id],
                self._reminded.get(task.id, said_at or self._seen_running[task.id]),
            )
            if (now - since).total_seconds() < threshold:
                continue
            minutes = (
                None if said_at is None else int((now - said_at).total_seconds() // 60)
            )
            due.append((task.id, minutes))

        async def remind(task_id: uuid.UUID, minutes: int | None) -> bool:
            self._reminded[task_id] = now
            try:
                async with asyncio.timeout(5):
                    delivered = await self._chat.notify_running_session(
                        task_id, stale_progress_reminder(minutes, languages[task_id])
                    )
                logger.info(
                    "task state reminder task=%s stale_min=%s delivered=%s",
                    task_id,
                    minutes,
                    delivered,
                )
                return delivered is True
            except Exception:  # one task must not stop the sweep
                logger.exception("task state reminder failed (task=%s)", task_id)
                return False

        return sum(await asyncio.gather(*(remind(t, m) for t, m in due)))
