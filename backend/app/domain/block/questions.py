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
    DECIDED,
    EXPIRED,
    SKIPPED,
    WITHDRAWN,
    answerable,
    answered,
    closed_by_the_asker,
    may_answer,
    skipped_by,
    skipped_by_all,
)
from app.domain.block.indexed_rows import QUESTION_ROWS
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType

#: Codex's `request_user_input` caps one call at three questions of two or three
#: options each; a longer list reads as a form, which is what this replaced.
MAX_QUESTIONS = 3

#: 题签上那个短标签的长度。Claude Code 的 `header` 也是 12 字；它画在组卡片的
#: 题签栏里，单题的卡片不画。
MAX_HEADER = 12


def required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(say("askFieldRequired", field=field))
    return value.strip()


@dataclass(frozen=True)
class Question:
    content: str
    options: list[dict]
    #: 题签上的短标签。空 = 没有短标签，组卡片用题号顶上。
    header: str = ""
    #: 这道题可以选多项；默认单选。
    multi_select: bool = False

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
        header = value.get("header", "")
        if not isinstance(header, str):
            raise ValidationError(say("askFieldString", field="header"))
        header = header.strip()
        if len(header) > MAX_HEADER:
            raise ValidationError(say("askHeaderTooLong", limit=MAX_HEADER))
        multi_select = value.get("multi_select", False)
        if not isinstance(multi_select, bool):
            raise ValidationError(say("askFieldBoolean", field="multi_select"))
        return cls(content, options, header, multi_select)


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
    deadline: datetime | None = None,
) -> list[Block]:
    """Write one message per question in the conversation; flush, never commit.

    ``asked`` is the people each question asks (empty: nobody in particular);
    ``asked_all`` says they are everyone in the channel.

    ``deadline`` is when the question stops waiting for an answer if nobody
    gives one — ``None`` means it waits indefinitely (the default an asking
    agent gets from `agent.ask`, and what questions written before this
    existed carry). Stored on the question as ``meta.deadline``; the sweep
    that acts on it is `expire_question`.

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
                    **({"header": question.header} if question.header else {}),
                    # 只写「能多选」这一种；缺省就是单选，老题读到的也是缺省。
                    **({"multi_select": True} if question.multi_select else {}),
                    **({"ask_request": request_id} if request_id else {}),
                    # 没人答就到点失效；不写就是一直等（老题也是这个意思）。
                    **(
                        {"deadline": deadline.isoformat()}
                        if deadline is not None
                        else {}
                    ),
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


def skip_question(question: Block, *, by: str) -> bool:
    """``by`` 跳过这道题：他不回答它。返回这一下是不是让题了结了（它点名的每个人
    都跳过了，`block.asked.skipped_by_all`）—— 了结了，提问的那位才该知道。

    跳过是对这道题的一个回应，所以和回答记在同一份 ``answer_log`` 上，一条
    ``kind=skipped``、不带选项也不带话、记着是谁的条目。它只把题从他手上拿走：题问
    了几个人，别人照旧等着回答。撤销跳过就是撤回这一条（`withdraw_answer`），和撤回
    答案是同一个动作。

    只有能回答这道题的人跳得过（`block.asked.may_answer`，点选项、打字回答也是这
    一条）；已经有人回答的题不再收，同一个人也不跳
    两次。不 flush：调用方和别的写一起落库。
    """
    meta = dict(question.meta or {})
    if not meta.get("options"):
        raise UnprocessableEntityError(say("askNotAQuestion"))
    if closed_by_the_asker(meta):
        raise ConflictError(say("askAlreadyClosed"))
    if not may_answer(meta, by):
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
    """``handle`` 在这道题上答过话没有。交付有了结果的那一条（``decided``）不算：
    那是卡上的决定，不是他在题上说的话，撤不掉。"""
    return any(
        (entry or {}).get("by") == handle and (entry or {}).get("kind") != DECIDED
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
            if (entry or {}).get("by") != by or (entry or {}).get("kind") == DECIDED
        ],
    }
    await session.flush()
    return question


def _closed_entry(kind: str, *, by: str, at: datetime | None = None) -> dict:
    """一条「题不再等人、但没人答」的记录，和跳过的条目同一个形状。"""
    return {
        "kind": kind,
        "option": None,
        "note": None,
        "by": by,
        "at": (at or datetime.now(UTC)).isoformat(),
        "reply_id": None,
    }


async def withdraw_question(session, *, question: Block, by: str) -> Block:
    """``by`` 撤回自己问出去的这道题（他是题块作者）。

    只有作者撤得动，而且只撤没人答过的：有人答了就被拒 —— 那条答案是答的人
    的，他要撤走的是他自己答的那条（`withdraw_answer`）。撤了跳过的人不算答过，题照
    样收得回来。

    记一条 ``kind=withdrawn`` 在 ``answer_log`` 上（和跳过、回答同一份），题因此
    `block.asked.settled`，离开「待你回答」与频道黄灯。**不是回答**：
    `answered` 仍为假，题卡画「芝士撤回了这道题」而不是「谁答了什么」。题那条消息、
    谁说过的话，一个字不动。

    叫醒谁、房间看到什么，由调用方负责。flush，不 commit。
    """
    meta = dict(question.meta or {})
    if not meta.get("options"):
        raise UnprocessableEntityError(say("askNotAQuestion"))
    if meta.get("answer_log") and answered(meta):
        raise ConflictError(say("askAlreadyAnswered"))
    if question.author != by:
        raise ForbiddenError(say("askWithdrawNotYours"))
    if closed_by_the_asker(meta):
        raise ConflictError(say("askAlreadyClosed"))
    question.meta = {
        **meta,
        "answer_log": [
            *(meta.get("answer_log") or []),
            _closed_entry(WITHDRAWN, by=by),
        ],
    }
    await session.flush()
    return question


async def expire_question(
    session, *, question: Block, at: datetime | None = None
) -> Block:
    """到点了还没人答：把这道题收掉（`block.asked.EXPIRED`）。

    和撤回同一套效果——不再等人、离开列表与黄灯、题卡收起选项并说明——只是理由
    是时钟走到了 ``meta.deadline``。**不封口**：`block.asked.answerable` 对过期的题
    仍为真，题卡留着输入框，之后有人写了照样记在题上（那一下会把提问的那位叫起来，
    走的是回答那条路，不由这里负责）。

    每次扫描只对「还没结算、已过 deadline」的题调一次；已经收过的题由调用方筛掉
    （`settled` 为真）。flush，不 commit。
    """
    meta = dict(question.meta or {})
    if closed_by_the_asker(meta) or answered(meta) or skipped_by_all(meta):
        return question
    question.meta = {
        **meta,
        "answer_log": [
            *(meta.get("answer_log") or []),
            _closed_entry(EXPIRED, by=question.author, at=at),
        ],
    }
    await session.flush()
    return question


async def settle_decision(
    session, *, question_ids, card_id, chosen: str | None, by: str
) -> list[Block]:
    """The delivery ``card_id`` was decided: of the messages ``question_ids``
    names, the questions whose buttons decide it (`agent.ask.ask_to_decide`)
    stop waiting, and say how it went.

    ``question_ids`` are the events whose notices the decision settled
    (`delivery.ledger.settle_card`): a question's notice carries its card and
    its event is the question itself, so no other read finds them, and a card
    nobody asked about costs nothing here. A button does nothing but call the
    card's own route, so the decision is made, and recorded here, wherever it
    is made: a click on one of these buttons, the card's own button, a merge
    on the forge, the task closing. ``chosen`` is the button kind it amounts to
    (``accept`` / ``close``). The question is closed with ``decided``, an entry
    that is not an answer and that nobody withdraws: the decision is the
    card's, and taking it off the question would offer buttons for a delivery
    that is already decided. It names the button the decision amounts to when
    the question offered one (``option``), and nothing when it did not (a
    return, a void, a close it never offered). Returns the questions it
    changed, for the caller to show once committed. Flush, not commit.
    """
    if not question_ids:
        return []
    rows = await session.scalars(
        select(Block).where(QUESTION_ROWS, Block.id.in_(question_ids))
    )
    changed = []
    now = datetime.now(UTC)
    for question in rows:
        meta = dict(question.meta or {})
        options = meta.get("options") or []
        actions = [(o or {}).get("action") or {} for o in options]
        if not any(a.get("card") == str(card_id) for a in actions):
            continue
        if not answerable(meta):
            continue
        offered = next(
            (
                o["text"]
                for o, a in zip(options, actions, strict=True)
                if a.get("kind") == chosen
            ),
            None,
        )
        entry = {**_closed_entry(DECIDED, by=by, at=now), "option": offered}
        question.meta = {**meta, "answer_log": [*(meta.get("answer_log") or []), entry]}
        changed.append(question)
    if changed:
        await session.flush()
    return changed
