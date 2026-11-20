"""答一整组题：人攒好答案一次交，房间里只多一条消息。

一次 `cheese_ask` 问的 1–3 道题是一件事（`block.questions`），它们共用一条通知
（`meta.notice_id`）。单答一条的老路还在：点一个选项就是把它的文字作为对那道题的
回复发出去，走普通发消息那扇门。这里补的是另一条路——**整组一次交**：几道题一起
答完，只留下一句话。

## 为什么落成一条消息

芝士读的是消息，不是屏幕上的卡片（卡片是前端画的）。所以答案必须写进那条消息的
正文：只写「已回答 3 个问题」，提问的那位只拿到「有人答了」，不知道答的是什么。

## 一道题的收尾和单答那条路完全一样

每条答案都追加进那道题的 `answer_log`，和 `announce.answer_questions` 写进去的
是同一种条目（`kind` 是 `option` 或 `note`）。哪几道题算答了、谁能答、跳过的人不
算——判据全在 `block.asked`，这里一个字都不另立。

整组一次交还多一件单答那条路没有的事：**提交那一条消息同时答掉组里每一道题**。
单答时一条消息只答它回复的那道题（`BlockRepository.questions_a_reply_answers`）；
这里的消息不回复任何题，它自己就是那几道题的答案。所以由调用方把答了哪几道题
告诉 `announce.answer_questions` 之外的收尾端（`services` 里那一步）。

flush，不 commit。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.block.asked import SKIPPED, may_answer, skipped_by
from app.domain.block.models import Block

#: 组里最多几道题，和 `questions.MAX_QUESTIONS` 同一条线。
MAX_QUESTIONS = 3


@dataclass(frozen=True)
class Answer:
    """一个人对组里一道题的回答：选了哪些选项，或者自己写的话。

    两者只会有一个：``options`` 非空就是选项（多选题可能不止一个），否则 ``note``
    是他自己写的那句话。两个都空表示这题没答——整组提交允许有题不答。
    """

    options: tuple[str, ...] = ()
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.options and not self.note

    @classmethod
    def parse(cls, value):
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValidationError(say("askAnswerObject"))
        raw_options = value.get("options", [])
        if not isinstance(raw_options, list) or not all(
            isinstance(o, str) for o in raw_options
        ):
            raise ValidationError(say("askAnswerOptionsList"))
        note = value.get("note", "")
        if not isinstance(note, str):
            raise ValidationError(say("askFieldString", field="note"))
        return cls(tuple(o.strip() for o in raw_options if o.strip()), note.strip())


def answers_from(body) -> dict[uuid.UUID, Answer]:
    """``body["answers"]``：题 id 到回答。认不出 id 的直接拒绝，不猜。"""
    raw = body.get("answers")
    if not isinstance(raw, dict) or not raw:
        raise ValidationError(say("askAnswersRequired"))
    if len(raw) > MAX_QUESTIONS:
        raise ValidationError(say("askAnswersCount", limit=MAX_QUESTIONS))
    out: dict[uuid.UUID, Answer] = {}
    for key, value in raw.items():
        try:
            question_id = uuid.UUID(str(key))
        except ValueError as exc:
            raise ValidationError(say("askAnswerQuestionId")) from exc
        out[question_id] = Answer.parse(value)
    return out


async def group_of(session, question: Block) -> list[Block]:
    """``question`` 所在的那一组：和它同一条通知的所有题，按时间排。

    组编号就是 `meta.notice_id`（`block.questions.post_questions` 落的，指向这一组
    的第一道题）。没有它的题自成一组。
    """
    meta = question.meta or {}
    notice = meta.get("notice_id")
    if not notice:
        return [question]
    rows = list(
        await session.scalars(
            select(Block)
            .where(
                Block.conversation_id == question.conversation_id,
                # `meta` 是 json 不是 jsonb：读成字符串比。
                Block.meta["notice_id"].as_string() == str(notice),
            )
            .order_by(Block.created_at, Block.id)
            .execution_options(populate_existing=True)
        )
    )
    return rows or [question]


def record_answers(
    questions: list[Block], answers: dict[uuid.UUID, Answer], *, by: str, at: datetime
) -> list[Block]:
    """把 ``answers`` 记到 ``questions`` 上，返回真正被改了的那些题。

    每条都是 `answer_log` 里的一条：选了选项是 ``kind=option``（多选题的 ``option``
    存逗号连起来的一串，和单答那条路的形状一致），自己写的是 ``kind=note``。没答的
    题（``Answer.empty``）一个字不改，它照旧等回答。

    谁答得了由 `block.asked.may_answer` 定，和点选项、跳过同一条；别人已经答过的题
    不再收（`block.asked.settled`），他跳过的那题也不收。**但答过的人可以改自己的
    答案**：「改答案」就是把这份草稿再交一次，所以 ``by`` 自己留下的那些条目先撤掉
    再写新的——一道题仍然只有一个人的回答，别人抢不走，答的人改得动。
    任何一条不合规都当场拒绝，整组一条不落——半组答案比没有答案更难收拾。
    """
    touched: list[Block] = []
    for question in questions:
        answer = answers.get(question.id)
        if answer is None or answer.empty:
            continue
        meta = question.meta or {}
        if not meta.get("options"):
            raise UnprocessableEntityError(say("askNotAQuestion"))
        if not may_answer(meta, by):
            raise ForbiddenError(say("askSkipNotYours"))
        if by in skipped_by(meta):
            raise ConflictError(say("askAlreadySkipped"))
        log = list(meta.get("answer_log") or [])
        others = [e for e in log if (e or {}).get("by") != by]
        if _answered_by_anyone(others):
            raise ConflictError(say("askAlreadyAnswered"))
        picked = _offered(meta)
        chosen = [picked.get(option, option) for option in answer.options]
        entry = {
            "kind": "option" if chosen else "note",
            "option": "、".join(chosen) if chosen else None,
            "note": None if chosen else answer.note,
            "by": by,
            "at": at.isoformat(),
            "reply_id": None,
        }
        # 自己那条不算：先拿掉，再按这次的答案写一条。别人的一条都不动。
        question.meta = {**meta, "answer_log": [*others, entry]}
        touched.append(question)
    if not touched:
        raise ValidationError(say("askAnswersEmpty"))
    return touched


def _answered_by_anyone(entries) -> bool:
    """这些条目里有没有一条是真的回答（跳过不算）。

    和 `block.asked.answered` 是同一条判据，只是按「除了我以外的人」来看：自己的
    那几条先被拿掉了，所以这里判的是「别人答过没有」。
    """
    return any((entry or {}).get("kind") != SKIPPED for entry in entries)


def _offered(meta: dict) -> dict[str, str]:
    """这道题的选项：点哪一个就记哪几个字（去掉模型加的「 (Recommended)」）。

    和 `announce._offered` 是同一条读法；两边各留一份是因为一个在「一条消息答了
    哪道题」那条路上，一个在整组提交这条路上，读的是同一份 `meta["options"]`。
    """
    offered: dict[str, str] = {}
    for option in meta.get("options") or []:
        text = option.get("text") if isinstance(option, dict) else option
        if isinstance(text, str):
            offered[text] = text
            offered.setdefault(text.removesuffix(" (Recommended)"), text)
    return offered


def summary_line(questions: list[Block], answers: dict[uuid.UUID, Answer]) -> str:
    """提交那一条消息的正文：一句话 + 逐题的答案。

    提问的那位读的就是它，所以逐题写全：题签（没有就用题号）+ 选了什么 / 写了什么，
    没答的写「未作答」。

    这是**人说的话**，不是平台的通知，所以不走 `say()`：它就是这个人交上去的正文，
    和他在输入框里打出来的一样，不存在「按读者语言再渲染一次」这回事。
    """
    answered_count = sum(
        1 for q in questions if (a := answers.get(q.id)) is not None and not a.empty
    )
    lines = [f"已回答上面的 {answered_count} 个问题"]
    for index, question in enumerate(questions):
        label = _label(question, index)
        lines.append(f"- {label}：{_said(answers.get(question.id))}")
    return "\n".join(lines)


def _label(question: Block, index: int) -> str:
    header = (question.meta or {}).get("header") or f"第 {index + 1} 题"
    return str(header)


def _said(answer: Answer | None) -> str:
    if answer is None or answer.empty:
        return "未作答"
    if answer.options:
        return "、".join(answer.options)
    return answer.note


__all__ = [
    "MAX_QUESTIONS",
    "Answer",
    "answers_from",
    "group_of",
    "record_answers",
    "summary_line",
]
