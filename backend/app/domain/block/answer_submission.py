"""Locked single-question settlement; group settlement lives in ask_groups."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.block.answers import Answer
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut


@dataclass(frozen=True)
class AnswerSubmission:
    project_id: uuid.UUID
    topic_id: uuid.UUID
    asked_by: str
    entry: dict
    updated: dict
    replay: bool


async def submit_answer(
    session, *, block_id, author: str, body: dict
) -> AnswerSubmission:
    """Apply a locked answer and return pure values plus a BlockOut JSON snapshot.

    Caller authenticates and authorizes membership before calling, even on replay.
    Caller owns the transaction and must record the wake and delivery before commit.
    No commit, broadcast or delivery I/O occurs here.
    """
    answer = Answer.parse(body)
    if author == "anonymous":
        raise ValidationError(say("answerSignIn"))
    block = await session.scalar(
        select(Block)
        .where(Block.id == block_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    meta = dict(block.meta or {})
    if meta.get("ask_group"):
        raise ValidationError(say("askGroupAtomicOnly"))
    entry, replay = answer.apply(meta, author)
    if not replay:
        block.meta = meta
    return AnswerSubmission(
        block.project_id,
        block.topic_id,
        block.author,
        entry,
        BlockOut.model_validate(block).model_dump(mode="json"),
        replay,
    )


async def add_answer_wake(session, *, project_id, topic_id, author, content, meta):
    """Flush one participant answer wake, returning BlockOut JSON; never commit.

    The authenticated answer route owns authorization and the surrounding answer
    plus delivery transaction. This entry owns only the timeline write.
    """
    wake = await BlockRepository(session).add(
        project_id=project_id,
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.participant,
        content=content,
        kind=BlockKind.message,
        meta=meta,
    )
    return BlockOut.model_validate(wake).model_dump(mode="json")
