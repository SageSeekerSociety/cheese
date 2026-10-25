"""The shapes a turn is prepared into, and the reads that fill them.

``TurnPreparation.prepare`` reads the room, the recipient, memory and the
machine in one transaction and hands the result on as ``TurnContext``; the data
shapes it fills, the protocols it asks the service's host for, and the reads
that touch no live state sit here, so ``preparation`` reads as the assembly
alone. ``preparation`` imports every name below and re-exports it, so a caller
that already named ``preparation`` for one of them still resolves.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.prompt import TOPIC_DOC_CHAR_BUDGET, fit_doc_to_budget
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.contract import Image
from app.domain.agent.turn.intake.rooms import _is_dm
from app.domain.agent_instance.services import ResolvedAgent
from app.domain.memory.files_store import MemoryIndex
from app.domain.memory.scopes import MemoryScope
from app.domain.room_task.canvas_text import living_doc_canvases
from app.domain.room_task.place import Place, doc_text_of
from app.domain.task.claims import StageProgress
from app.domain.task.teaching import TeachingContext
from app.domain.topic.models import Topic
from app.domain.topic.reads import open_rooms
from app.domain.topic_membership.services import TopicMemberService

#: At least as long as the note `fit_doc_to_budget` adds to a document it cut.
_FIT_NOTE_CHARS = 300


async def living_doc_text(
    session: AsyncSession, place: Place, *, needs_place: bool
) -> str | None:
    """The living document as the system prompt carries it (`doc_text_of`),
    and after it the canvases it holds, as text: the two together fit the
    document's budget, the document giving way."""
    text = await doc_text_of(session, place, needs_place=needs_place)

    async def readable(rooms: set[uuid.UUID]) -> set[uuid.UUID]:
        # A task of a channel only some may read is named on a card only when
        # it is this conversation's own channel.
        return await open_rooms(session, rooms) | ({place.room_id} & rooms)

    canvases = await living_doc_canvases(session, place, readable) if text else ""
    if not canvases or text is None:
        return text
    # The prompt fits the two again as one text; the room a compressed
    # document's note takes is left, so that second fit has nothing to cut.
    room = TOPIC_DOC_CHAR_BUDGET - len(canvases) - _FIT_NOTE_CHARS
    return (
        fit_doc_to_budget(
            text, room, full_read_hint="用 `cheese_doc(action=get)` 读全文"
        )
        + canvases
    )


def carried_skills(
    agent: ResolvedAgent, topic: Topic, course: Sequence[str] = ()
) -> list[str]:
    """这一轮谁带哪些技能进提示词：队友自己带的（userskill），没带就回落到这间房
    点亮的；再加上项目所出自的题目给的（``course``，`Teaching.skills`）。

    userskill 存在 `agent_instances.configuration.skills`，出厂时从 preset 抄进来
    （`agent_instance/services.initial_configuration`），改它要 manager
    （`projects_agents._changes_an_advanced_setting`）。技能跟着人走：学术研究的
    队友不该继承全栈工程队友的构建约定，所以**带了自己的就不再叠加房间的**——两份
    都进提示词，只会让模型在互相冲突的规矩里挑一份。

    题目给的那几个（``course``）是出题人对项目里**每一个**队友说的，叠在上面这份之上，
    不顶替它：后来加进项目的队友、自己带了技能的队友、回落到房间的队友都一样带上，
    每一轮现读、叠在最后、去重。

    都空时返回 `[]`，而 `carried_bodies(session, topic, [])` 是空串，于是正文与
    这层不存在时逐字节相同（和 `_TurnContext.teaching` 为 None 是同一个理由）。
    """
    carried = [
        name
        for name in (agent.configuration or {}).get("skills") or []
        if isinstance(name, str)
    ]
    return list(dict.fromkeys([*(carried or topic.skills or []), *course]))


def _proposal_frames(landed: dict | None) -> list[dict]:
    """撞上档位策略的那一轮怎么收场：房间里刚落下的那条提议，然后 done。

    没有 error 帧——这一轮没有发生，但也没有出错，下一步在提议收件人手上（结论
    40）。`landed` 是 `None` 时这条提议之前就提过了，房间里不再多一句一样的话。
    """
    frames: list[dict] = []
    if landed is not None:
        frames.append({"type": "event_block", "block": landed})
    frames.append({"type": "done"})
    return frames


@dataclass(frozen=True, slots=True)
class Launch:
    """What a seat's session is started with, read without opening a turn
    (``TurnPreparation.launch_inputs``): the same inputs a turn hands its session,
    so a session started from them is the one the next turn would start."""

    session: SessionRef
    runtime: RoomSessions
    system_prompt: str
    resume_token: str | None
    model: str | None
    env: dict[str, str] | None
    needs_place: bool


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Everything one turn needs to run, read once before anything runs it.

    A turn is assembled and then executed, and the two halves want opposite
    things from a database session: assembling is a dozen reads that belong in
    one transaction, executing is minutes of streaming that must hold none. This
    is what crosses between them — so a backend that runs a turn some other way
    receives THIS, rather than a session and instructions on what to read.
    """

    # Who is here and what they are working under.
    project_id: uuid.UUID
    # Where: the room, and the task or 支线 when the conversation is one of
    # theirs.
    room_id: uuid.UUID
    inner_id: uuid.UUID | None
    acting_agent: str
    agent: ResolvedAgent
    agent_pool: tuple[MemoryScope, str] | None
    role: str | None
    roster: list[dict]
    untitled: bool
    # 本周教学范围 (#8d772257). None for every project that is not a course —
    # and None is what keeps the prompt byte-identical to what it was before
    # this key existed, which is the property the non-course tests pin.
    teaching: TeachingContext | None
    # 项目所出自的题目的阶段，和这个项目在每个阶段上的状态
    # (`task.claims.stages_for_project`)。题目不分阶段、或项目不出自题目时是空的。
    stages: list[StageProgress]
    # 从题目来的项目里「题目」那一段（`task.package.section_for_project`）：第几版、
    # 材料在哪、这一版改了什么。别的项目是 None，开场里没有这一段。
    task_package: str | None
    # 这一轮带的技能正文：队友自己的 userskill，没带就回落到这间房点亮的
    # （`carried_skills` 定名单，`carried_bodies` 取正文 —— 平台发货的那几个平台
    # 手里就有，机器上的那几条按索引现读回来）。空 = 只用平台自己的那份指引。
    skill_bodies: str

    # What this turn was given, and what it is being asked about.
    prompt_text: str
    pending_ids: list[uuid.UUID]
    # The platform notices this prompt carries. Stamped consumed alongside the
    # human blocks and by the same turn — a notice this turn actually read must
    # not be read again — but kept separate up to that point, because every
    # other question asked of `pending_ids` is about who spoke.
    notice_ids: list[uuid.UUID]
    # Where this seat's reading stands once this turn finishes cleanly: the
    # timeline position of the newest block the prompt carried. None when it
    # carried none. A turn that fails leaves it alone, exactly as it leaves
    # `consumed_turn` alone.
    read_through: tuple[datetime, uuid.UUID] | None
    turn_images: list[Image]
    replay_notice: str | None
    resume_session_id: str | None

    # What it should know: the doc, the memories, the checklist it left behind,
    # the cards waiting on it, and which段 of the flow this topic is in.
    doc_text: str | None
    # 注入用的项目目标（`project/overview.py`），和芝士读写它时要用的编号。
    overview_doc_text: str | None
    overview_doc_id: uuid.UUID | None
    # 这一轮注入的 L1 记忆索引（team 一份 + 本轮发言人各一份）。正文不在里面：
    # 每条记忆的正文在会话目录 `.cheese/memory/` 下，agent 自己去读（见
    # `memory/instructions.py`）。None = 「这一轮没走注入那条路」。
    memory: MemoryIndex | None
    prior_progress: list[dict]
    #: 这件任务上一次的进度上报是「卡住了」（`Task.progress_state`）。有人说话、
    #: 这一轮开了，平台就在输入前面问它一句还卡不卡（`blocked_report_reminder`）：
    #: 「卡住了」通常是它在轮次末尾报的，等有人回话时上一轮早就结束了，而那条
    #: 只在会话还在跑时才发得出来的提醒正好够不着（`StaleProgressReminder`）。
    blocked_report: bool
    # Chat messages already in the room, apart from the ones this turn delivers.
    earlier_messages: int
    # For a 支线: the message it hangs under and what the main line said just
    # before it; and the channel's tasks still open. None elsewhere.
    thread_context: str | None
    thread_tasks: str | None
    topic_refs: list[dict]
    topic_refs_for_prompt: list[dict]
    # 这个项目交出去过的东西 —— 下一次交付要从这几个名字里挑一个。空着是「还没交出
    # 去过东西」，None 是「这间房间不交付」（私聊）。
    artifacts: list[dict] | None

    # Which machine, and whether it reports its own liveness (which decides who
    # owns this turn's clock; see the `turn_ceiling` frame).
    provider: RoomSessions
    # 这一轮跑在哪个骨架上，解析过一次的那个答案（结论 28）。会话行的键里有它，
    # 所以执行那一半必须读这里，不能自己再解析一次。
    harness: str
    # 这一轮要不要一双手 (结论 19，不变量 I2)。解析的产物，不是房间的属性：同一
    # 条会话可以这一轮只聊天、下一轮动文件，而租手发生在解析之后。
    needs_place: bool
    # A 支线's work is not kept: it may read, run and try things on its own
    # machine, and nothing it does there reaches the project. What changes the
    # project is done in a task.
    keeps_nothing: bool
    # Whose word a task's session acts on and where it changes the project
    # (`task_section`); None outside a task. It changes when a 支线 becomes
    # the task or the task changes hands, and is told again then.
    task_rules: str | None


@dataclass(frozen=True, slots=True)
class TurnBail:
    """The turn ended while it was still being assembled, and these are the
    frames that say so. Not an error: nobody was waiting on an answer, or the
    machine is still being built."""

    frames: list[dict]


class _Machines(Protocol):
    """What a turn asks of the service's compute pool (``agent.compute``)."""

    def choose(
        self, project_settings: Mapping[str, Any] | None
    ) -> tuple[str, RoomSessions | None]: ...

    def owned(self, harness: str, *, on_account: bool) -> RoomSessions | None: ...

    async def activate(self, session: SessionRef, runtime: RoomSessions) -> None: ...


class _MemoryBooks(Protocol):
    """What a turn tells the service's memory ledger (``agent.memory_ledger``)."""

    def remember_turn(
        self,
        room_id: uuid.UUID,
        seat: tuple[uuid.UUID, str],
        *,
        acting: str,
        speakers: tuple[str, ...],
    ) -> None: ...


class ModelOptions(Protocol):
    async def __call__(
        self,
        project_id: uuid.UUID,
        provider: RoomSessions | None,
        topic_id: uuid.UUID | None = None,
        *,
        agent: ResolvedAgent | None = None,
        acting_agent: str | None = None,
    ) -> tuple[dict, str]: ...


async def human_private_owner(session: AsyncSession, topic: Topic) -> str | None:
    if not _is_dm(topic):
        return None
    seats = await TopicMemberService(session).private_seats(topic.id)
    return seats[0] if seats is not None else None
