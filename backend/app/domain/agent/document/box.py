"""Asking the AI teammate from a document: a person selects text (or nothing,
for the whole document), and asks it to change it or asks about it.

The question goes to a session of its own, like a comment thread's
(``thread``), keyed by the conversation the box holds: the person's
follow-ups in the same box (「再短一点」) go to the same session, and a new box
starts a new one. The answer is streamed back to the box, not posted anywhere;
what the session changed in the document comes back with it, so the box can
show it in place and undo it.

A shortcut in the box is sent as its id and written out here (``PRESETS``): the
words on the button are only its name. What every request has to respect —
change only what was selected, keep the formatting, add no facts — is in the
session's rules, once.

The box's conversation is the asker's: its record in Valkey (``_box_key``) says
whose and on which document, and only that person may go on with it, stop it,
or put its answer into a comment thread.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ForbiddenError, ValidationError
from app.core.redis import get_redis_client
from app.core.sentences import error_frame, exception_text, say
from app.domain.agent.chat import ChatService
from app.domain.agent.document import question, session
from app.domain.agent.document.question import Asked
from app.domain.agent.session_host.answer import Answer, Tool, Waiting, Words
from app.domain.agent.session_host.consumptions import Consumption, Consumptions
from app.domain.agent.session_host.contract import HostFull, Prompt, StartAbandoned
from app.domain.agent.session_host.host import SessionHost
from app.domain.living_doc import work_edits

logger = logging.getLogger(__name__)

#: How long a box's conversation can be gone on with after its last question.
BOX_TTL_S = 2 * 3600


@dataclass(frozen=True)
class Preset:
    #: "edit" changes the selection; "ask" only answers.
    kind: str
    #: "selection" or "document".
    scope: str
    instruction: str


PRESETS: dict[str, Preset] = {
    "polish": Preset(
        "edit",
        "selection",
        "把选中的文字改通顺：改正错别字、标点、病句和不通的语序，换掉生硬或重复的"
        "用词。意思、语气、篇幅都保持不变，不增删信息。专有名词、术语、数字、引文原样"
        "保留。原文已经通顺就不要改，直接说没有要改的地方。",
    ),
    "shorten": Preset(
        "edit",
        "selection",
        "在不丢信息的前提下删短选中的文字：去掉重复的话、空话套话、可有可无的修饰，"
        "把绕的句子改直。数字、条件、限定词（如“仅”“至少”“除……外”）一个都不能丢。"
        "目标大约是原来篇幅的一半到三分之二；已经很紧凑就只删真正多余的。",
    ),
    "list": Preset(
        "edit",
        "selection",
        "把选中的文字拆成条目：一条说一件事，各条句式一致，去掉拆开后多余的连接词。"
        "有先后顺序用编号列表，没有就用圆点列表。不合并、不丢掉任何一点。原文不适合拆"
        "（比如只有一件事）就不改，说明原因。",
    ),
    "table": Preset(
        "edit",
        "selection",
        "把选中的列表改成表格：从各条里找出共有的几个方面作为列，每条占一行；某条缺了"
        "某一方面就留空，不编。各条没有共同的方面就不改，说明原因。",
    ),
    "translate": Preset(
        "edit",
        "selection",
        "把选中的文字翻译成英文（选中的是英文时翻译成中文），替换原文。按这个领域的习惯"
        "用词，不逐字直译。专有名词有通用译法用通用译法，没有就保留原文。格式、链接、"
        "@ 提及原样保留。",
    ),
    "check": Preset(
        "ask",
        "selection",
        "检查选中的文字，列出确实有问题的地方：写错的事实或数字、前后矛盾、和文档其他"
        "部分或项目章程对不上的说法、含糊到会被理解错的句子。每条引用原文、说明问题；"
        "能对照到依据的写出依据在哪。不提风格偏好。没有问题就说没有。",
    ),
    "explain": Preset(
        "ask",
        "selection",
        "用平实的话说明选中的文字是什么意思；有术语就解释术语；文档或章程里有相关背景"
        "的，指出在哪。",
    ),
    "summarize": Preset(
        "ask",
        "document",
        "用几条列出全文的要点：定了什么、还没定什么、谁要做什么。只依据文档本身，不补充"
        "文档外的信息。",
    ),
    "check_all": Preset(
        "ask",
        "document",
        "检查全文，列出确实有问题的地方：写错的事实或数字、前后矛盾、和项目章程对不上的"
        "说法、和项目记忆或最近的对话对不上的过时说法、含糊到会被理解错的句子。每条引用"
        "原文、说明问题。不提风格偏好。没有问题就说没有。",
    ),
    "structure": Preset(
        "ask",
        "document",
        "看全文的分节和顺序是否方便读者找到东西，给出具体的调整建议（哪节挪到哪、哪些该"
        "合并或拆开）。结构已经合理就直说。只给建议，不改文档。",
    ),
}


@dataclass(frozen=True)
class Selection:
    """The Markdown of the blocks holding the selection, and where in it the
    selected text is."""

    block: str
    start: int
    end: int

    @property
    def text(self) -> str:
        return self.block[self.start : self.end]

    def marked(self) -> str:
        return (
            f"{self.block[: self.start]}<选中>{self.text}</选中>"
            f"{self.block[self.end :]}"
        )


# --- the box's conversation ----------------------------------------------------


def _box_key(conversation: uuid.UUID | str) -> str:
    return f"doc-agent:box:{conversation}"


@dataclass(frozen=True)
class Box:
    """Whose a box's conversation is, and what it last answered."""

    asker: str
    document_id: uuid.UUID
    answer: str = ""


async def box_of(redis: Redis, conversation: uuid.UUID) -> Box | None:
    raw = await redis.get(_box_key(conversation))
    if not raw:
        return None
    held = json.loads(raw)
    return Box(held["asker"], uuid.UUID(held["document"]), held.get("answer", ""))


async def _keep(redis: Redis, conversation: uuid.UUID, box: Box) -> None:
    await redis.set(
        _box_key(conversation),
        json.dumps(
            {
                "asker": box.asker,
                "document": str(box.document_id),
                "answer": box.answer,
            },
            ensure_ascii=False,
        ),
        ex=BOX_TTL_S,
    )


async def owned(
    redis: Redis, conversation: uuid.UUID, *, asker: str, document_id: uuid.UUID
) -> Box:
    """The box's conversation, when it is ``asker``'s on this document; one
    that is nobody's and one that is someone else's are the same refusal."""
    box = await box_of(redis, conversation)
    if box is None or box.asker != asker or box.document_id != document_id:
        raise ForbiddenError("This conversation is not yours")
    return box


async def stop(
    redis: Redis,
    sessions: SessionHost,
    *,
    project_id: uuid.UUID,
    conversation: uuid.UUID,
) -> None:
    """Stop what the box is waiting on: its turn, the session host, or the
    answer being written. What was written stays, marked as stopped."""
    await question.stop(
        redis, sessions, conversation, session.ref(project_id, conversation)
    )


# --- one question --------------------------------------------------------------


def _question(
    around: question.Surroundings,
    *,
    preset: Preset | None,
    text: str,
    selection: Selection | None,
    may_edit: bool,
) -> str:
    parts: list[str] = []
    if selection is not None:
        parts += [
            f"<选中的文字>\n{selection.text}\n</选中的文字>",
            *around.around(selection.marked(), "选中的文字所在的段落"),
        ]
    parts += [around.document(), *around.conversation()]
    asked = preset.instruction if preset is not None else f"提问人说：{text}"
    if not may_edit:
        rule = "这次只回答，不要改文档。"
    elif selection is not None:
        rule = (
            "要改就只改 <选中> 标出的文字；cheese_doc_edit 的 old 从文档原文照抄，"
            "不含 <选中> 标记。"
        )
    else:
        rule = ""
    parts.append(f"{asked}\n{rule}".strip())
    return "\n\n".join(parts)


async def ask(
    chat: ChatService,
    consumptions: Consumptions,
    redis: Redis,
    emit: Callable[[str, dict], Awaitable[None]],
    *,
    asked: Asked,
    asker: str,
    conversation: uuid.UUID,
    preset: str | None,
    text: str,
    selection: Selection | None,
    may_edit: bool,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> Consumption | None:
    """Ask one question of the box's conversation. Until it is asked, ``emit``
    is told how it goes: ``queued`` (waiting for a turn), ``working``, or the
    end of a question refused or stopped before it was asked (``done``,
    ``error``). Once asked it is a question the platform reads to its end
    (``Answers``), and the one returned."""
    factory = factory or chat.session_factory
    chosen = PRESETS.get(preset or "")
    allowed = may_edit and (chosen is None or chosen.kind == "edit")
    async with factory() as db:
        bound = await question.bind(db, asked)
    await _keep(redis, conversation, Box(asker, asked.document_id))
    work = uuid.uuid4()

    async def queued() -> None:
        await emit("queued", {})

    stopped = question.stopped(redis, conversation)

    slot = await question.take_turn(
        redis,
        asked.project_id,
        conversation,
        on_wait=queued,
        stopped=stopped,
    )
    if slot is None:
        if await stopped():
            # A stop is for the question it reached: the next one starts
            # unstopped.
            await question.unstop(redis, conversation)
            await emit("done", {"answer": "", "edits": [], "stopped": True})
        else:
            busy = say("docAgentBoxBusy", agent=bound.agent_name)
            await emit("error", error_frame(busy))
        return None
    await emit("working", {})
    try:
        async with factory() as db:
            await question.admit(db, asked.project_id, bound)
            around = await question.surroundings(db, asked, seat=bound.agent_handle)
    except ValidationError as exc:
        # Refused before anything was asked: the refusal is what the box says.
        await slot.release()
        await question.unstop(redis, conversation)
        await emit("error", error_frame(exception_text(exc)))
        return None
    except BaseException:
        await slot.release()
        raise
    prompt = _question(
        around, preset=chosen, text=text, selection=selection, may_edit=allowed
    )

    async def said() -> Prompt:
        # Minted once the session is there: its lifetime is the answer's.
        async with factory() as db:
            acting = await question.credential(
                db,
                asked=asked,
                agent=bound.agent_handle,
                asker=asker,
                work=work,
                may_edit=allowed,
            )
        return Prompt(work, prompt, acting=acting)

    return await consumptions.begin(
        kind=KIND,
        key=str(conversation),
        data={**asked.data(), "asker": asker, "agent": bound.agent_name},
        work_id=work,
        session=session.session_for(
            asked=asked,
            key=conversation,
            bound=bound,
            around=around,
            where="box",
        ),
        prompt=said,
        ceiling_s=question.ANSWER_S,
        slots=[slot],
    )


#: A document box's question, to the questions the platform reads to the end
#: (``session_host.consumptions``); its key is the box's conversation.
KIND = "doc-box"


class Answers:
    """What becomes of a box's answer: shown as it is written, kept as the
    box's last answer with what it changed, and paid for by the project."""

    def __init__(self, chat: ChatService, consumptions: Consumptions) -> None:
        self._chat = chat
        self._consumptions = consumptions

    async def stopped(self, consumption: Consumption) -> bool:
        redis = get_redis_client()
        return (
            redis is not None
            and await question.stopped(redis, uuid.UUID(consumption.key))()
        )

    async def took(
        self, consumption: Consumption, item: Waiting | Words | Tool
    ) -> None:
        if isinstance(item, Waiting):
            await self._consumptions.publish(consumption, "queued", {})
        elif isinstance(item, Words):
            await self._consumptions.publish(
                consumption, "delta", {"text": item.text, "at": item.at}
            )
        else:
            await self._consumptions.publish(consumption, "tool", {"name": item.name})

    async def ended(
        self,
        consumption: Consumption,
        answer: Answer,
        *,
        written: str,
        stopped: bool,
        failure: BaseException | None,
    ) -> list[tuple[str, dict]]:
        asked = Asked.of(consumption.data)
        conversation = uuid.UUID(consumption.key)
        agent = consumption.data["agent"]
        text, refused = answer.text.strip(), None
        if isinstance(failure, HostFull):
            refused = say("docAgentBoxBusy", agent=agent)
        elif isinstance(failure, StartAbandoned):
            pass
        elif failure is not None or answer.error:
            logger.warning(
                "doc agent box answer failed conversation=%s: %s",
                conversation,
                failure or answer.error,
            )
            refused = say("docAgentBoxFailed", agent=agent)
        redis = get_redis_client()
        edits = (
            await work_edits.take(redis, consumption.work_id)
            if redis is not None
            else []
        )
        if redis is not None:
            await question.unstop(redis, conversation)
        if stopped:
            # What it had written when it was stopped stays, marked as stopped.
            text, refused = written.strip(), None
        await self._chat.charge_turn_spend(
            asked.project_id, asked.room_id, consumption.work
        )
        if refused is not None and not edits:
            return [("error", error_frame(refused))]
        # What was changed was changed, answer or not: the box shows it and can
        # undo it.
        text = text if refused is None else ""
        if redis is not None:
            await _keep(
                redis,
                conversation,
                Box(consumption.data["asker"], asked.document_id, text),
            )
        return [("done", {"answer": text, "edits": edits, "stopped": stopped})]
