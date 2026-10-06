"""An agent's question: an ordinary message in the room that carries quick replies.

A question is a `kind=message` block written under the asking agent's seat,
with `meta.options` (what a person can click), `meta.asked` (who it waits on;
None when nobody in particular) and `meta.answer_log` (every reply that has
answered it so far: an option it offered, or a note in the person's own
words). Nothing about it holds a turn open: the agent posts it and ends its
turn, and the reply a person sends — a click or a typed message — is an
ordinary message that starts the agent's next one
(`agent.announce.answer_questions`).

Callers authorize the seat and commit; nothing here publishes or delivers.
"""

from dataclasses import dataclass

from sqlalchemy import select

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository

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
