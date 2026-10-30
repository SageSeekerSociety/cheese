"""Seat personal agents named in a message and ask their owners privately."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.ask import ask
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.own import (
    owned_by_seat,
)
from app.domain.agent_instance.own.admission import why_not
from app.domain.identity.handles import AGENT_HANDLE_PREFIX, agent_instance_handle
from app.domain.notice_meta import EVENT_TURN_FAILED, SEVERITY_INFO, WHO_HUMAN, notice
from app.domain.room_task.place import Place
from app.domain.topic.models import is_private_chat
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User


async def seat_if_named(
    session: AsyncSession, project, topic, content: str, author: str
) -> None:
    """Seat named project teammates using team connections.

    A private channel seats only a personal agent whose owner is in that
    channel: whoever is seated reads what is said there, so the roster is the
    boundary. Their generated seat handles use the agent namespace. Human
    mentions need no connection lookup; actual seats are still checked against
    project rows.
    """
    members = TopicMemberService(session)
    for seat in await named_own_seats(session, topic, content):
        already = seat in await members.agent_handles(topic.id)
        await members.ensure_agent_seat(topic.id, seat)
        if not already:
            await ask_owner_to_join(session, topic, seat)


async def named_own_seats(session: AsyncSession, topic, content: str) -> list[str]:
    """The personal agents' seats ``content`` names that a message in ``topic``
    seats (`seat_if_named`). Reads only: the composer's preview of who a
    message is for asks it without seating anyone."""
    if not content or f"<@{AGENT_HANDLE_PREFIX}" not in content:
        return []
    owned = await session.scalars(
        select(AgentInstance.id)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(
            AgentInstance.project_id == topic.project_id,
            # One its owner removed from the project is in no room of it.
            AgentInstance.is_active.is_(True),
        )
    )
    handles = {own: agent_instance_handle(own) for own in owned.all()}
    seats = {own: seat for own, seat in handles.items() if f"<@{seat}>" in content}
    members = TopicMemberService(session)
    if topic.members_only:
        # 私密频道只有名册上的人看得到。别人的私人 Claude Code 不在名册上，点名会让
        # 它入座、读到这里的内容，等于把频道外的人拉进来。所以只认主人自己也在频道
        # 里的那几位；主人不在，点名和没点一样。
        people = set(await members.people_handles(topic.id))
        rows = await session.execute(
            select(OwnAgent.instance_id, User.username)
            .join(User, User.id == OwnAgent.owner_user_id)
            .where(OwnAgent.instance_id.in_(list(seats)))
        )
        owners = {instance: username for instance, username in rows.all()}
        seats = {own: seat for own, seat in seats.items() if owners.get(own) in people}
    return list(seats.values())


async def ask_owner_to_join(session: AsyncSession, room, seat: str) -> None:
    """Ask in the owner's DM when a personal agent gains a room seat."""
    if (
        is_private_chat(room)
        or await owned_by_seat(session, room.project_id, seat) is None
    ):
        return
    await ask(
        session,
        place=Place(room=room),
        seat=seat,
        body={
            "to_owner": True,
            "questions": [
                {
                    "question": str(
                        say(
                            "ownAgentRoomConsent", room=room.title, room_id=str(room.id)
                        )
                    ),
                    "options": [
                        {"text": str(say("ownAgentRoomYes"))},
                        {"text": str(say("ownAgentRoomNo"))},
                    ],
                }
            ],
        },
    )


async def refused(session: AsyncSession, project, recipient: dict):
    """Refuse an unavailable connection using the existing project rules."""
    if not recipient.get("mentioned") or not recipient.get("instance_id"):
        return None
    reason = await why_not(
        session, uuid.UUID(recipient["instance_id"]), project.settings
    )
    if reason is not None:
        recipient["mentioned"] = False
    return reason


async def say_refused(session: AsyncSession, place, block, reason) -> None:
    if reason is not None:
        await announce(
            session,
            place_id=place.room_id,
            task_id=place.inner_id,
            content=reason,
            meta=notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN),
            turn_id=block.turn_id,
        )
