"""An agent's question: an ordinary message in the room that carries quick replies.

A question is a `kind=message` block written under the asking agent's seat,
with `meta.options` (what a person can click), `meta.asked` (who it waits on;
None when nobody in particular) and `meta.answer_log` (every response it has
had so far: an option it offered, a note in the person's own words, or a skip
— the person it waits on saying he will not answer it). Nothing about it holds
a turn open: the agent posts it and ends its
turn, and the reply a person sends — a click or a typed message — is an
ordinary message that starts the agent's next one
(`agent.announce.answer_questions`).

Callers authorize the seat and commit; nothing here publishes or delivers.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import ConflictError, ForbiddenError, ValidationError
from app.core.sentences import say
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import names_a_person

#: The `kind` of the `answer_log` entry a skip leaves: the person it waits on
#: answered that he will not answer it. It is an entry of the same record as an
#: answer, not a meta key of its own, because it does what an answer does to
#: the question — the question stops waiting, one response closes it, and its
#: author takes it back the way he takes back an answer (`withdraw_answer`).
SKIPPED = "skipped"

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
    session, *, project_id, conversation_id, seat, questions, asked, request_id=None
) -> list[Block]:
    """Write one message per question in the conversation; flush, never commit.

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
                    "asked": asked,
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
    判据同一个读法：题上记着问的是谁（`meta.asked`），就只等他；没记着谁（平台发起
    的那一轮里问的），谁回都算。回答的永远是人：队友不答队友的题（人的消息才走
    `announce.answer_questions`）。
    """
    asked = (question.meta or {}).get("asked")
    return names_a_person(handle) and (asked is None or asked == handle)


def skip_question(question: Block, *, by: str) -> Block:
    """``by`` 跳过这道题：不回答它。

    跳过是对这道题的一个回应，所以和回答记在同一份 ``answer_log`` 上，一条
    ``kind=skipped``、不带选项也不带话的条目 —— 于是凡是读「这道题答过没有」的地
    方（待回答清单、「一道题只收一个回答」、通知结不结）都不用另学一个标记。撤销跳
    过就是撤回这一条（`withdraw_answer`），和撤回答案是同一个动作。

    只有这道题在等的人跳得过（`waits_on`）；已经有回应的题不收第二个，和回答一样。
    不 flush：调用方和别的写一起落库。
    """
    meta = dict(question.meta or {})
    if not meta.get("options"):
        raise ValidationError(say("askNotAQuestion"))
    if not waits_on(question, by):
        raise ForbiddenError(say("askSkipNotYours"))
    if meta.get("answer_log") or meta.get("answered"):
        raise ConflictError(say("askAlreadyAnswered"))
    question.meta = {
        **meta,
        "answer_log": [
            {
                "kind": SKIPPED,
                "option": None,
                "note": None,
                "by": by,
                "at": datetime.now(UTC).isoformat(),
                "reply_id": None,
            }
        ],
    }
    return question


def skipped(entry: dict) -> bool:
    """这一条 ``answer_log`` 是不是一次跳过，而不是一个答案。"""
    return (entry or {}).get("kind") == SKIPPED


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
