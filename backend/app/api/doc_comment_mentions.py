"""A document comment that names the room's agent hands it to that agent.

Comments are passive: recording one starts nothing. Only a comment (or a reply
in a comment thread) that @-mentions an agent seated in the room starts a turn
of that agent, as the commenter's — so what it changes in the document is
recorded as done at their request. The agent answers in the thread
(``cheese_doc_comment_reply``), not in the chat.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.block.notice_text import say
from app.domain.delivery.mention import mentioned_handles
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.room_task.place import Place
from app.domain.topic_membership.services import TopicMemberService


async def mentioned_seat(
    db: AsyncSession, place: Place, actor: Actor, content: str
) -> str | None:
    """The room's agent this comment names, when a person wrote it."""
    seats = await TopicMemberService(db).agent_handles(place.room_id)
    named = [handle for handle in mentioned_handles(content) if handle in seats]
    if not named or await IdentityService(db).is_agent(actor.handle):
        return None
    return named[0]


def hand_to_agent(
    chat: ChatService,
    runner: AgentWorkRunner,
    *,
    place: Place,
    actor: Actor,
    seat: str,
    thread_id: uuid.UUID,
    content: str,
    quote: str | None,
) -> None:
    """Start ``seat``'s turn on this comment, authored by the commenter. Call
    after the comment is committed: the turn reads the thread."""
    where = f"「{quote[:200]}」处的" if quote else ""
    runner.submit(
        chat,
        place.room_id,
        author=actor.handle,
        content=(
            f"<@{actor.handle}> 在实况文档{where}评论里点了你的名"
            f"（评论线程 `{thread_id}`）：\n\n---\n{content}\n---\n\n"
            "在这个评论线程里回答，用 cheese_doc_comment_reply，不要回到聊天里。"
            "要你改文档就用 cheese_doc_edit 直接改，改完在线程里说一句改了什么。"
        ),
        addressed=addressed_to_agent(seat),
        nudge_event=say(
            "docCommentMentioned", person=f"<@{actor.handle}>", agent=f"<@{seat}>"
        ),
        provision_actor=actor,
    )
