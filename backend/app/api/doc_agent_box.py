"""Asking the room's AI teammate from the document: a person selects text (or
nothing, for the whole document), and asks it to change it or asks about it.

The question goes to a session of its own, like a comment thread's
(``doc_agent``), keyed by the conversation the box holds: the person's
follow-ups in the same box (「再短一点」) go to the same session, and a new box
starts a new one. The answer is streamed back to the box, not posted anywhere;
what the session changed in the document comes back with it, so the box can
show it in place and undo it.

A shortcut in the box is sent as its id and written out here (``PRESETS``): the
words on the button are only its name. What every request has to respect —
change only what was selected, keep the formatting, add no facts — is in the
session's rules, once.

The box's conversation is the asker's: its record in Valkey (``_box_key``) says
whose and in which room, and only that person may go on with it, stop it, or
put its answer into a comment thread.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import doc_agent
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import error_frame, exception_text, say
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.pi import document
from app.domain.agent.harness.pi.handless import (
    Answered,
    HandlessSessions,
    HostFull,
    Looking,
    Said,
)

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


def _stop_key(conversation: uuid.UUID | str) -> str:
    return f"doc-agent:stop:{conversation}"


@dataclass(frozen=True)
class Box:
    """Whose a box's conversation is, and what it last answered."""

    asker: str
    room_id: uuid.UUID
    answer: str = ""


async def box_of(redis: Redis, conversation: uuid.UUID) -> Box | None:
    raw = await redis.get(_box_key(conversation))
    if not raw:
        return None
    held = json.loads(raw)
    return Box(held["asker"], uuid.UUID(held["room"]), held.get("answer", ""))


async def _keep(redis: Redis, conversation: uuid.UUID, box: Box) -> None:
    await redis.set(
        _box_key(conversation),
        json.dumps(
            {"asker": box.asker, "room": str(box.room_id), "answer": box.answer},
            ensure_ascii=False,
        ),
        ex=BOX_TTL_S,
    )


async def owned(
    redis: Redis, conversation: uuid.UUID, *, asker: str, room_id: uuid.UUID
) -> Box:
    """The box's conversation, when it is ``asker``'s in this room; one that is
    nobody's and one that is someone else's are the same refusal."""
    box = await box_of(redis, conversation)
    if box is None or box.asker != asker or box.room_id != room_id:
        raise ForbiddenError("This conversation is not yours")
    return box


async def stop(
    redis: Redis,
    sessions: HandlessSessions,
    *,
    project_id: uuid.UUID,
    conversation: uuid.UUID,
) -> None:
    """Stop what the box is waiting on: its turn, or the answer being written."""
    await redis.set(_stop_key(conversation), "1", ex=int(doc_agent.WAIT_S))
    if await doc_agent.asking(redis, conversation) is not None:
        await sessions.abort(document.state_dir(project_id, conversation))


# --- one question --------------------------------------------------------------


def _question(
    around: doc_agent.Surroundings,
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
            "要改就只改 <选中> 标出的文字；edit_document 的 old 从文档原文照抄，"
            "不含 <选中> 标记。"
        )
    else:
        rule = ""
    parts.append(f"{asked}\n{rule}".strip())
    return "\n\n".join(parts)


async def ask(
    chat: ChatService,
    sessions: HandlessSessions,
    redis: Redis,
    emit: Callable[[str, dict], Awaitable[None]],
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    asker: str,
    conversation: uuid.UUID,
    preset: str | None,
    text: str,
    selection: Selection | None,
    may_edit: bool,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Answer one question of the box's conversation, telling ``emit`` as it
    goes: ``queued`` (waiting for a turn), ``working``, ``delta`` (text),
    ``tool``, then ``done`` (the answer and what was changed) or ``error``."""
    factory = factory or chat.session_factory
    chosen = PRESETS.get(preset or "")
    allowed = may_edit and (chosen is None or chosen.kind == "edit")
    async with factory() as db:
        bound = await doc_agent.bind(db, room_id)
    await _keep(redis, conversation, Box(asker, room_id))
    await redis.delete(_stop_key(conversation))
    work = uuid.uuid4()

    async def queued() -> None:
        await emit("queued", {})

    async def stopped() -> bool:
        return bool(await redis.exists(_stop_key(conversation)))

    slot = await doc_agent.take_turn(
        redis,
        project_id,
        conversation,
        doc_agent.Asking(
            asker, bound.agent_handle, room_id, may_edit=allowed, work=str(work)
        ),
        on_wait=queued,
        stopped=stopped,
    )
    if slot is None:
        if await stopped():
            await emit("done", {"answer": "", "edits": [], "stopped": True})
        else:
            busy = say("docAgentBoxBusy", agent=bound.agent_name)
            await emit("error", error_frame(busy))
        return
    await emit("working", {})
    answer, refused, spent = "", None, False
    try:
        async with factory() as db:
            await doc_agent.admit(db, project_id, bound)
            around = await doc_agent.surroundings(
                db, project_id=project_id, room_id=room_id, seat=bound.agent_handle
            )
        question = _question(
            around, preset=chosen, text=text, selection=selection, may_edit=allowed
        )
        launch = doc_agent.launch_for(
            project_id=project_id,
            room_id=room_id,
            key=conversation,
            bound=bound,
            around=around,
            where="box",
        )
        spent = True
        async for event in sessions.ask(
            launch, work, question, ceiling_s=doc_agent.ANSWER_S
        ):
            if isinstance(event, Said):
                await emit("delta", {"text": event.text})
            elif isinstance(event, Looking):
                await emit("tool", {"name": event.tool})
            elif isinstance(event, Answered):
                answer = event.text.strip()
                if event.error:
                    logger.warning(
                        "doc agent box answer failed conversation=%s: %s",
                        conversation,
                        event.error,
                    )
                    refused = say("docAgentBoxFailed", agent=bound.agent_name)
    except ValidationError as exc:
        # Refused before anything was asked: the refusal is what the box says.
        refused = exception_text(exc)
    except HostFull:
        refused = say("docAgentBoxBusy", agent=bound.agent_name)
    except Exception:  # noqa: BLE001 — the box is told; the log keeps why
        logger.warning(
            "doc agent box failed conversation=%s", conversation, exc_info=True
        )
        refused = say("docAgentBoxFailed", agent=bound.agent_name)
    finally:
        await slot.release()
        edits = await doc_agent.edits_of(redis, work)
    was_stopped = await stopped()
    if refused is None or edits or was_stopped:
        # What was changed was changed, answer or not: the box shows it and can
        # undo it.
        answer = answer if refused is None else ""
        await emit("done", {"answer": answer, "edits": edits, "stopped": was_stopped})
        await _keep(redis, conversation, Box(asker, room_id, answer))
    else:
        await emit("error", error_frame(refused))
    if spent:
        await chat.charge_turn_spend(project_id, room_id, work)
