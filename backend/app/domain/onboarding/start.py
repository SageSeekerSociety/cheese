"""上手三步: what a person has already done in a project, from what the server
recorded rather than what a page guessed.

The new-user guide (`frontend/src/views/welcome/`) ends in the project's 综合,
where a panel walks them through three things until they are done:

1. **asked** — they handed 芝士 something: a turn ran on a message of theirs.
   A message sent with plain Enter goes to the members only and runs nothing.
2. **answered** — an AI teammate replied in a conversation they asked in.
3. **accepted** — a delivery was accepted into the project. Accepting merges the
   change into the project's repository, so the merge time on the accept card
   (`AcceptCard.pr_merged_at`, stamped by every merge path) is the fact; the
   result is then in the 资料库.

And one fact about the person rather than the project: **learned** — they have
accepted a delivery themselves somewhere. Someone who has is past the first
steps, so the panel is not shown to them in any project.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.conversation.models import Conversation
from app.domain.identity.handles import agent_handle_column
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.topic.models import Topic


@dataclass(frozen=True, slots=True)
class StartSteps:
    asked: bool
    answered: bool
    accepted: bool
    learned: bool


async def start_steps(
    session: AsyncSession, project_id: uuid.UUID, handle: str
) -> StartSteps:
    """The three facts for ``handle`` in this project. Only conversations they
    asked in are read, so the answer says nothing about rooms they cannot see."""
    in_project = select(Conversation.id).where(Conversation.project_id == project_id)
    asked_in = select(AgentTurn.conversation_id).where(
        AgentTurn.author == handle, AgentTurn.conversation_id.in_(in_project)
    )
    replied = exists().where(
        Block.conversation_id.in_(asked_in),
        Block.kind.in_((BlockKind.message, BlockKind.attachment)),
        Block.author_type == AuthorType.participant,
        agent_handle_column(Block.author),
    )
    merged = exists().where(
        AcceptCard.topic_id == Topic.id,
        Topic.project_id == project_id,
        AcceptCard.pr_merged_at.is_not(None),
    )
    learned = exists().where(
        AcceptCard.decided_by == handle, AcceptCard.status == AcceptStatus.accepted
    )
    row = (
        await session.execute(select(exists(asked_in), replied, merged, learned))
    ).one()
    return StartSteps(
        asked=bool(row[0]),
        answered=bool(row[1]),
        accepted=bool(row[2]),
        learned=bool(row[3]),
    )
