"""An agent's question: an ordinary message in the room that carries quick replies.

A question is a `kind=message` block written under the asking agent's seat,
with `meta.options` (what a person can click), `meta.asked` (the people it
asks, chosen by the agent — empty when nobody in particular),
`meta.asked_all` (asked of everyone in the channel) and `meta.answer_log`
(every response it has had so far: an option it offered, a note in the
person's own words, or one person's skip). How those read — whom it still waits
on, whether it is settled — is `block.asked`. Nothing about it holds a turn
open: the agent posts it and ends its turn, and the reply a person sends — a
click or a typed message — is an ordinary message that starts the agent's next
one (`agent.announce.answer_questions`).

Callers authorize the seat and commit; nothing here publishes or delivers.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.block.asked import (
    SKIPPED,
    answered,
    asked_of,
    skipped_by,
    skipped_by_all,
)
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import names_a_person

#: Codex's `request_user_input` caps one call at three questions of two or three
#: options each; a longer list reads as a form, which is what this replaced.
MAX_QUESTIONS = 3


def required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(say("askFieldRequired", field=field))
    return value.strip()


@dataclass(frozen=True)
class Question:
    content: str
    options: list[dict]

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValidationError(say("askQuestionObject"))
        content = required_text(value.get("question"), "question")
        raw = value.get("options")
        if not isinstance(raw, list) or not 2 <= len(raw) <= 3:
            raise ValidationError(say("askOptionsTwoToThree"))
        options = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValidationError(say("askOptionObject"))
            option = {"text": required_text(item.get("text"), "text")}
            if "explain" in item:
                if not isinstance(item["explain"], str):
                    raise ValidationError(say("askFieldString", field="explain"))
                if item["explain"].strip():
                    option["explain"] = item["explain"].strip()
            options.append(option)
        if len({item["text"] for item in options}) != len(options):
            raise ValidationError(say("optionsDistinct"))
        return cls(content, options)


def parse_questions(body) -> list[Question]:
    raw = body.get("questions")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_QUESTIONS:
        raise ValidationError(say("askQuestionsCount", limit=MAX_QUESTIONS))
    return [Question.parse(item) for item in raw]


async def post_questions(
    session,
    *,
    project_id,
    conversation_id,
    seat,
    questions,
    asked: tuple[str, ...],
    asked_all: bool = False,
    request_id=None,
) -> list[Block]:
    """Write one message per question in the conversation; flush, never commit.

    ``asked`` is the people each question asks (empty: nobody in particular);
    ``asked_all`` says they are everyone in the channel.

    ``request_id`` makes a retry safe, as it does for `chat_send`: the questions
    a call with the same id already wrote are returned instead of asked twice.
    """
    if request_id is not None:
        existing = list(
            await session.scalars(
                select(Block)
                .where(
                    Block.conversation_id == conversation_id,
                    Block.author == seat,
                    Block.meta["ask_request"].as_string() == request_id,
                )
                .order_by(Block.created_at, Block.id)
            )
        )
        if existing:
            return existing
    blocks = BlockRepository(session)
    rows = []
    for question in questions:
        rows.append(
            await blocks.add(
                project_id=project_id,
                conversation_id=conversation_id,
                author=seat,
                author_type=AuthorType.participant,
                content=question.content,
                kind=BlockKind.message,
                meta={
                    "options": question.options,
                    "asked": list(asked),
                    "asked_all": asked_all,
                    "answer_log": [],
                    **({"ask_request": request_id} if request_id else {}),
                },
                # The agent's own words: it waits for a person to answer them,
                # not for itself to read them back as input.
                own_output=True,
            )
        )
    # The call's one notice is keyed by its first question; each question
    # names it, so the notice is settled once all of them are answered.
    for row in rows:
        row.meta = {**(row.meta or {}), "notice_id": str(rows[0].id)}
    return rows


def waits_on(question: Block, handle: str) -> bool:
    """这道题等不等 ``handle`` 的回答 —— 也就是他能不能答它、能不能跳过它。

    和 `BlockRepository.questions_a_reply_answers` 认一句打字回复是不是答案的那条
    判据同一个读法：题上记着问的是谁（`meta.asked`），就等他们；一个人也没记（平台
    发起的那一轮里问的），谁回都算。回答的永远是人：队友不答队友的题（人的消息才走
    `announce.answer_questions`）。
    """
    asked = asked_of(question.meta)
    return names_a_person(handle) and (not asked or handle in asked)


def skip_question(question: Block, *, by: str) -> bool:
    """``by`` 跳过这道题：他不回答它。返回这一下是不是让题了结了（它点名的每个人
    都跳过了，`block.asked.skipped_by_all`）—— 了结了，提问的那位才该知道。

    跳过是对这道题的一个回应，所以和回答记在同一份 ``answer_log`` 上，一条
    ``kind=skipped``、不带选项也不带话、记着是谁的条目。它只把题从他手上拿走：题问
    了几个人，别人照旧等着回答。撤销跳过就是撤回这一条（`withdraw_answer`），和撤回
    答案是同一个动作。

    只有这道题在等的人跳得过（`waits_on`）；已经有人回答的题不再收，同一个人也不跳
    两次。不 flush：调用方和别的写一起落库。
    """
    meta = dict(question.meta or {})
    if not meta.get("options"):
        raise UnprocessableEntityError(say("askNotAQuestion"))
    if not waits_on(question, by):
        raise ForbiddenError(say("askSkipNotYours"))
    if answered(meta):
        raise ConflictError(say("askAlreadyAnswered"))
    if by in skipped_by(meta):
        raise ConflictError(say("askAlreadySkipped"))
    question.meta = {
        **meta,
        "answer_log": [
            *(meta.get("answer_log") or []),
            {
                "kind": SKIPPED,
                "option": None,
                "note": None,
                "by": by,
                "at": datetime.now(UTC).isoformat(),
                "reply_id": None,
            },
        ],
    }
    return skipped_by_all(question.meta)


def answered_by(question: Block, handle: str) -> bool:
    """``handle`` 在这道题上答过话没有。"""
    return any(
        (entry or {}).get("by") == handle
        for entry in ((question.meta or {}).get("answer_log") or [])
    )


async def withdraw_answer(session, *, question: Block, by: str) -> Block:
    """把 ``by`` 自己那条答复从题上撤掉，让这道题回到「还没人答」。撤销一次跳过也是
    这一步：跳过记在同一份 ``answer_log`` 上（`skip_question`）。

    「只有回答的人可以撤回」落在这一行：撤掉的是 ``answer_log`` 里 ``by`` 等于他的
    条目，别人的一条不动。没答过的人来撤什么也撤不掉，直接拒掉 —— 否则这里就是任何
    人抹掉别人答案的入口。

    撤的是这道题上的记录，不是他说的那句话：那条消息还在房间里，内容不改。撤到没有
    人答过之后，选项重新出现，题也重新算作在等回答（`_awaiting_an_answer` 的判据同
    此，读的是同一个 ``answer_log``）。叫醒谁、房间看到什么，由调用方负责。

    flush，不 commit。
    """
    if not answered_by(question, by):
        raise ForbiddenError(say("askNothingToWithdraw"))
    meta = dict(question.meta or {})
    question.meta = {
        **meta,
        "answer_log": [
            entry
            for entry in (meta.get("answer_log") or [])
            if (entry or {}).get("by") != by
        ],
    }
    await session.flush()
    return question
