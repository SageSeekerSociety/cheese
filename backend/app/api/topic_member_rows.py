"""A room's roster as its page reads it.

`roster_rows` is the one function that builds those rows: `GET /topics/{id}/members`
returns them, and the `members` of the room snapshot a subscription opens with
(`app.api.room_snapshot`) embeds the same ones, so the roster a page draws from
the snapshot and the one it re-reads from the route cannot disagree.

It lives under `app.api`, not in `routes/topic_members.py`, because one of the
two askers is not a route: a helper left there makes `room_snapshot` a route
that reaches a route, which is what C4 (`routes-independent`) forbids.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.adapters import project_team
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.identity.repositories import AgentBindingRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.repositories import UserProfileRepository, UserRepository
from app.domain.user.services import remarks_by_handles


async def roster_rows(
    db: AsyncSession, topic, *, viewer: str | None = None
) -> list[dict]:
    """A room's roster as `GET /topics/{id}/members` lists it; also the
    `members` of the room snapshot a subscription opens with (`room_snapshot`).
    The caller has authorised the reader."""
    seats = await TopicMemberService(db).seats(topic, team=project_team)
    # Attach display names and avatars so the UI can draw the roster without a
    # second round-trip.
    users = UserRepository(db)
    profiles = UserProfileRepository(db)
    bindings = AgentBindingRepository(db)
    # Resolve handles → users once, then derive is-agent from the binding (never
    # a hard-coded handle check): a member is an agent iff it carries a binding.
    handles = [m.member_handle for m in seats]
    rows = await users.get_by_handles(handles)
    remarks = await remarks_by_handles(db, viewer, handles)
    user_ids = [u.id for u in rows.values()]
    agent_ids = await bindings.agent_user_ids(user_ids)
    # The human-readable display name lives on the profile (nickname); the core
    # User row only carries the handle (username). Fall back to the handle.
    faces = await profiles.nickname_and_avatar_by_user_id(user_ids)
    # An agent seat's profile nickname is a constant fixed when the seat's user
    # row was created, so the name has to come from the agent. Each seat carries
    # its OWN agent's: a room may seat several, and one name for all of them
    # showed two teammates as the same person — the same defect the mention
    # roster had. A seat still under the room-derived handle belongs to the
    # agent the room points at, which is what `fallback` is.
    instances = {
        agent_instance_handle(instance.id): instance
        for instance in await AgentInstanceService(db).list_for_project(
            topic.project_id
        )
    }
    fallback = None
    items = []
    for m in seats:
        d = m.model_dump(mode="json")
        user = rows.get(m.member_handle)
        nickname, avatar_id = (
            faces.get(user.id, (None, None)) if user is not None else (None, None)
        )
        # Agent members wear an Agent badge — derived from the execution binding.
        is_agent = user is not None and user.id in agent_ids
        d["agent"] = is_agent
        if is_agent:
            instance = instances.get(m.member_handle)
            if instance is not None:
                agent = AgentInstanceService.resolved(instance)
            else:
                if fallback is None:
                    fallback = await AgentInstanceService(db).for_topic_by_id(topic.id)
                agent = fallback
            d["name"] = agent.display_name
            d["name_source"] = agent.name_source.value
            # The teammate's own handle (`cheese`), which the project roster
            # shows under its name; `member_handle` is its seat (`cheese-<hex>`),
            # an internal id nobody types.
            d["instance_handle"] = agent.handle
        else:
            d["name"] = nickname if nickname else m.member_handle
        # Absent = this person never picked an avatar; the UI draws its coloured
        # initial rather than the one face everybody else who never picked has.
        d["avatar_id"] = avatar_id
        d["remark"] = remarks.get(m.member_handle)
        items.append(d)
    return items
