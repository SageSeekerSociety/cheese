"""平台在会话还在干活时，自己往会话里说的两句话；和做任务的会话还在跑时，问它任务
做到哪了的那一句（`StaleProgressReminder`）。

两句话问的是两件事。`QuietSessionReminders.silence` 问话：有人对它说了话，它读到
以后一句没回。回过一句它就安心干活，不再催它说话：干活的过程在现场看得到，做到哪了
看清单和任务状态，对话里只说要人出手的事、大家该知道的变化和做完的结果。
`QuietSessionReminders.checklist` 问清单：会话在干活，清单却停在原地。它们能分开停：
会话可以回过话而清单从开工起就是那一版（2026-10-11 报上来的正是这一种），反过来也成立。

判据都是平台核得了的事，不看轮次：会话此刻在不在干活（这台进程持有的座位、它的
runner 说在干活，`ComputePool.working_conversations`），它为手上这段工作读进去的输入
（`native_inputs` 的回执，`_read_at`）带着谁的话，它之后说没说过话（房间里它署名的消息），清单
是不是它这一段工作写的（`TopicProgress.turn_id` 是会话手上那段工作）。

**两句都不在「这一步已经不归它管」的时候说。** 它所在的对话上有一张还开着的验收卡
时，做到哪一步由卡说（`_spoken_for`，和 `StaleProgressReminder` 同一个集合），平台不
再去问队友：卡递出去、等验收人看的那个窗口，正是这两句最不该敲的时候，而每敲一次都
是一遍真实的模型调用。

放进去的路和人的中途消息是同一条（`notify_running_session`）：会话开工时取的那份
快照，之后只有人打字或者平台叫它一声才能变。这里的两句就是「平台叫它一声」。

放在 `turn/steps` 而不是 `chat.py`：`chat.py` 压在自己的行数上限上（见
`.claude/rules/architecture.md` 的「Files have a size cap」）。这里只用到 `ChatService`
对外的几样东西，用 `SessionNotices` 写下来，所以不用 import 它（T2 也不许）。
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Uuid, and_, case, column, exists, func, or_, select, table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.harness import HARNESSES
from app.domain.agent.harness.prompt import (
    silence_reminder,
    stale_checklist_reminder,
    stale_progress_reminder,
)
from app.domain.agent.session_state import reports_lifecycle
from app.domain.block.authorship import participant_blocks
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import NativeInput
from app.domain.identity.handles import names_a_person
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.reads import conversation_progress
from app.domain.user.services import language_of

logger = logging.getLogger(__name__)


class Working(Protocol):
    """这台进程持有的、runner 说在干活的会话（`ComputePool`）。"""

    def working_conversations(self) -> set[uuid.UUID]: ...

    def working_seats(self, topic_id: uuid.UUID) -> set[tuple[str, str]]: ...

    def work_in_flight(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> uuid.UUID | None: ...


class SessionNotices(Protocol):
    """说这两句话要用到的入口，`ChatService` 正好都有这些。"""

    @property
    def session_factory(self) -> async_sessionmaker: ...

    # Returns `bool | InputReconciliationPending` on ChatService; only whether the
    # live session took it matters here（`delivered is True`）。
    async def notify_running_session(
        self, topic_id: uuid.UUID, notice: str, *, recipient_seat: str | None = None
    ) -> object: ...


@dataclass(frozen=True)
class _Asked:
    """会话读进去的、一个人说的最近那句话：谁说的，什么时候读到的。"""

    author: str
    read_at: datetime


def _read_at():
    """When the session read an input: its echo for a harness whose runner
    reports reads (`Harness.reports_lifecycle`), and the runner taking it for
    one whose acceptance is the read (`Driver.receipt_on_accept`)."""
    reporting = [name for name in HARNESSES if reports_lifecycle(name)]
    return case(
        (NativeInput.harness.in_(reporting), NativeInput.echoed_at),
        else_=NativeInput.accepted_at,
    )


async def _asked(
    session: AsyncSession,
    conversation: uuid.UUID,
    acting: str,
    work: uuid.UUID | None,
) -> _Asked | None:
    """``acting`` 那一席的会话为手上这段工作（``work``）读进去的最近一句人话，
    没有就是 None：这段工作是平台的提示、别的队友的话叫起来的，就没有谁在等它
    回话——之前哪段工作里回过的人，也不在等这一段。一份输入算进哪段工作，看它被
    读进的那段（``execution_work_id``，runner 读到时报的），没报就是它自己开的
    那段（``work_id``）。"""
    if work is None:
        return None
    read_at = _read_at()
    reads = (
        await session.execute(
            select(
                read_at,
                NativeInput.block_ids,
                NativeInput.held_block_ids,
                NativeInput.seen_block_ids,
            )
            .where(
                NativeInput.conversation_id == conversation,
                NativeInput.recipient_handle == acting,
                func.coalesce(NativeInput.execution_work_id, NativeInput.work_id)
                == work,
                read_at.is_not(None),
            )
            .order_by(read_at.desc())
        )
    ).all()
    for read, *batches in reads:
        ids = {uuid.UUID(block) for batch in batches for block in batch or []}
        if not ids:
            continue
        authors = await session.scalars(
            select(Block.author).where(
                Block.id.in_(ids),
                Block.kind == BlockKind.message,
                participant_blocks(),
            )
        )
        person = next((a for a in authors if names_a_person(a)), None)
        if person is not None:
            return _Asked(person, read)
    return None


async def _said_since(
    session: AsyncSession, conversation: uuid.UUID, acting: str, since: datetime
) -> bool:
    """``acting`` 在 ``since`` 之后有没有在这里说过话：别的队友说话不算它说了。"""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Block.conversation_id == conversation,
                    Block.kind == BlockKind.message,
                    participant_blocks(),
                    Block.author == acting,
                    Block.created_at > since,
                )
            )
        )
    )


#: A working seat: the conversation, the seat's own handle, the handle it acts
#: under (and signs its messages with).
Seat = tuple[uuid.UUID, str, str]


#: The review domain's cards, read as a bare table: that domain imports this
#: one, so a card is read here by its row (as `room_task.closing` does).
_cards = table(
    "accept_cards",
    column("topic_id", Uuid),
    column("task_id", Uuid),
    column("status"),
)
#: The statuses of a card that is still open (`review.archive.OPEN_CARD_STATUSES`,
#: which a unit test holds this copy to): while one is, the card says where the
#: task stands (等审, fixing its checks, resolving a conflict), not its teammate.
OPEN_CARD_STATUSES = ("pending", "pending_gate", "conflict")


async def _spoken_for(
    session: AsyncSession, conversations: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """这些对话里，哪几个的下一步已经交给一张还开着的验收卡在说。

    两个来源同一个答案：一条活自己的卡（`task_id` 就是这段对话），和一个房间
    自己那张（`task_id` 为空、`topic_id` 是这段对话）。卡开着的时候，做到哪一步
    由卡说；`StaleProgressReminder` 对任务问的也是这一件事，集合是同一个
    `OPEN_CARD_STATUSES`。
    """
    if not conversations:
        return set()
    wanted = set(conversations)
    rows = await session.execute(
        select(_cards.c.task_id, _cards.c.topic_id).where(
            _cards.c.status.in_(OPEN_CARD_STATUSES),
            or_(
                _cards.c.task_id.in_(wanted),
                and_(_cards.c.task_id.is_(None), _cards.c.topic_id.in_(wanted)),
            ),
        )
    )
    return {task_id or topic_id for task_id, topic_id in rows}


class QuietSessionReminders:
    """会话在干活，有人的话没回、或者清单停在原地，叫它一声。

    按座位算（会话所在的对话、它署名用的那一席）：一间房里几位队友并行在干，各
    有各的沉默，别人说话不算它说了。两样记在这台进程里（上一次为话提醒、上一次为
    清单提醒），只记在干活的座位上：不在干活了就忘掉，下一次从头算。换了进程最多
    早一个阈值多问一句。

    一处不说：**这一步还轮到它动**（`_spoken_for`：卡开着就由卡说）。"""

    def __init__(self, chat: SessionNotices, working: Working) -> None:
        self._chat = chat
        self._working = working
        self._silence_reminded: dict[Seat, datetime] = {}
        self._checklist_reminded: dict[Seat, datetime] = {}

    def _at_work(self) -> set[Seat]:
        seats = {
            (conversation, seat, acting)
            for conversation in self._working.working_conversations()
            for seat, acting in self._working.working_seats(conversation)
        }
        for reminded in (self._silence_reminded, self._checklist_reminded):
            for gone in set(reminded) - seats:
                reminded.pop(gone, None)
        return seats

    async def _tell(self, seat: Seat, text: str, what: str, minutes: int) -> bool:
        conversation, _seat, acting = seat
        try:
            # A blocked terminal must not hold up reminders in other rooms.
            async with asyncio.timeout(5):
                delivered = await self._chat.notify_running_session(
                    conversation, text, recipient_seat=acting
                )
            # Which room, how long it had been still, and whether the session
            # took it: what tells 「the agent was reminded and stayed quiet」
            # from 「the reminder never reached it」.
            logger.info(
                "%s topic=%s seat=%s quiet_min=%d delivered=%s",
                what,
                conversation,
                acting,
                minutes,
                delivered,
            )
            return delivered is True
        except Exception:  # one room must not stop the sweep
            logger.exception("%s failed (topic=%s)", what, conversation)
            return False

    async def silence(self, now: datetime) -> int:
        """读到一个人的话之后一句都没回的座位，每过 `chat_progress_reminder_after_s`
        听到一次。回过以后不再叫它：之后它在干活时不出声是该有的样子。读进去的不是
        人话的会话（平台叫它做的检查、别的队友的话）有它自己的通知规矩，这里不管。"""
        threshold = settings.chat_progress_reminder_after_s
        seats = self._at_work()
        if not seats:
            return 0
        due: list[tuple[Seat, int, str | None]] = []
        async with self._chat.session_factory() as session:
            spoken_for = await _spoken_for(
                session, {conversation for conversation, _s, _a in seats}
            )
            for seat in seats:
                conversation, handle, acting = seat
                if conversation in spoken_for:
                    continue
                work = self._working.work_in_flight(conversation, handle)
                asked = await _asked(session, conversation, acting, work)
                if asked is None:
                    continue
                if await _said_since(session, conversation, acting, asked.read_at):
                    continue
                since = max(
                    asked.read_at, self._silence_reminded.get(seat, asked.read_at)
                )
                if (now - since).total_seconds() < threshold:
                    continue
                minutes = int((now - asked.read_at).total_seconds() // 60)
                due.append((seat, minutes, await language_of(session, asked.author)))

        async def remind(seat: Seat, minutes: int, language: str | None) -> bool:
            # 那句话里已经点了清单（`silence_reminder` 的末句），别让清单那条在
            # 同一次沉默里又说一遍。
            self._silence_reminded[seat] = now
            self._checklist_reminded[seat] = now
            return await self._tell(
                seat,
                silence_reminder(minutes, language),
                "chat progress reminder",
                minutes,
            )

        return sum(await asyncio.gather(*(remind(*item) for item in due)))

    async def checklist(self, now: datetime) -> int:
        """叫会话把它自己这一段工作写的、停在原地的清单推到当下。

        只看会话手上这段工作写的那一版（`TopicProgress.turn_id`）：清单可能是之前
        留下的，而平台叫得动的只有此刻在干活的会话。阈值和沉默提醒共用一个：两个问
        题都是「看得见的东西是不是停在原地」。"""
        threshold = settings.chat_progress_reminder_after_s
        seats = self._at_work()
        if not seats:
            return 0
        due: list[tuple[Seat, int, str | None]] = []
        async with self._chat.session_factory() as session:
            spoken_for = await _spoken_for(
                session, {conversation for conversation, _s, _a in seats}
            )
            for seat in seats:
                conversation, handle, acting = seat
                if conversation in spoken_for:
                    continue
                row = await conversation_progress(session, conversation)
                if row is None or row.turn_id is None:
                    continue
                work = self._working.work_in_flight(conversation, handle)
                if row.turn_id != work:
                    continue
                written_at = row.updated_at
                since = max(written_at, self._checklist_reminded.get(seat, written_at))
                if (now - since).total_seconds() < threshold:
                    continue
                asked = await _asked(session, conversation, acting, work)
                due.append(
                    (
                        seat,
                        int((now - written_at).total_seconds() // 60),
                        await language_of(session, asked.author if asked else None),
                    )
                )

        async def remind(seat: Seat, minutes: int, language: str | None) -> bool:
            self._checklist_reminded[seat] = now
            return await self._tell(
                seat,
                stale_checklist_reminder(minutes, language),
                "checklist reminder",
                minutes,
            )

        return sum(await asyncio.gather(*(remind(*item) for item in due)))


class ProgressNotices(Protocol):
    """问任务状态要用到的入口：一个会话工厂，和往在跑的会话里说一句话的那条路。"""

    @property
    def session_factory(self) -> async_sessionmaker: ...

    async def notify_running_session(
        self, topic_id: uuid.UUID, notice: str
    ) -> object: ...


class StaleProgressReminder:
    """做任务的会话还在跑，而它报的任务状态（`cheese_task(action=state)`）停在原地超过
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
