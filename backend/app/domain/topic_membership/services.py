"""Topic membership business logic (fusion-design §3).

The roster is a topic's group-room membership: who is in the room, their role,
and who @all/@here reaches. Roles are owner/admin/member (distinct from the
project's lead/member/mentor). 芝士 is a member too, handle `cheese`.

No auth layer exists yet (agent-as-user is P1, fusion-design §2): the acting
user's handle is passed in and authorized against their topic role — owner and
admin may manage the roster, plain members may not.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    looks_like_agent_handle,
    topic_agent_handle,
)
from app.domain.topic.models import TopicMembership, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

__all__ = ["CHEESE_HANDLE", "TopicMemberService"]

# Roles allowed to manage a topic's roster (add / remove / change roles).
_MANAGER_ROLES = frozenset({TopicRole.owner, TopicRole.admin})


class TopicMemberService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicMembershipRepository(session)
        self._topics = TopicRepository(session)

    async def _ensure_topic(self, topic_id: uuid.UUID) -> None:
        if await self._topics.get(topic_id) is None:
            raise NotFoundError("Topic not found")

    async def _require_manager(self, topic_id: uuid.UUID, actor: str) -> None:
        """Only an owner/admin of THIS topic may mutate its roster — unless the
        room has NO manager at all, in which case the project's owner/lead may
        step in.

        Without that escape hatch an ownerless topic is a dead end with no way
        out of the product: only an owner may appoint one, and there is no
        owner. That state is not hypothetical — 96 of this project's 149 topics
        were in it (see TopicService._resolve_owner for how they got there), and
        repairing them meant writing the database by hand. The hatch is
        deliberately narrow: it opens only while the room has nobody who can
        manage it, so a healthy room's owner is never overridden.
        """
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is not None and member.role in _MANAGER_ROLES:
            return
        if not await self._has_manager(topic_id) and await self._is_project_steward(
            topic_id, actor
        ):
            return
        raise ForbiddenError("只有话题的 owner / admin 能管理成员")

    async def _has_manager(self, topic_id: uuid.UUID) -> bool:
        return any(
            m.role in _MANAGER_ROLES for m in await self._repo.list_for_topic(topic_id)
        )

    async def _is_project_steward(self, topic_id: uuid.UUID, actor: str) -> bool:
        """Who may step into a room that has lost its owner.

        Normally the project's owner or a lead — they answer for the whole
        project, and so for a room inside it.

        But that alone leaves a project with NEITHER a dead end, and such a
        project is reachable: ``ProjectService.create`` accepts
        ``owner_handle=None`` and seeds no member rows, so the row can have a
        NULL owner and no lead at all. Its ownerless topics then have no way out
        of the product in either direction — only a topic manager may appoint
        one and there is none, only a steward may step in and there is none, and
        appointing a project owner needs a steward too. Measured on dev
        (2026-08-13): 5 of 18 active topics in `cheese 自建` sit with no owner
        while the project's own ``owner_handle`` is NULL; that project happens to
        have a lead, which is the only reason it is recoverable.

        So when a project has nobody in charge at all, any of its members may.
        The widening is deliberately the narrowest one that removes the dead
        end: it opens only while BOTH the room and the project have no manager,
        so a project with a lead never has its authority diluted.
        """
        from app.domain.membership.repositories import MemberRepository
        from app.domain.project.models import ProjectRole
        from app.domain.project.repositories import ProjectRepository

        topic = await self._topics.get(topic_id)
        if topic is None:
            return False
        project = await ProjectRepository(self._session).get(topic.project_id)
        if project is not None and project.owner_handle == actor:
            return True
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        if any(m.role == ProjectRole.lead for m in members if m.user_handle == actor):
            return True
        # Last resort: nobody is in charge of this project either.
        #
        # "Nobody" is strictly an ABSENT owner — NULL or empty. Not `anonymous`,
        # even though that is what an unidentified caller resolves to and what
        # `projects.py::_is_a_real_person` (advisorily) discounts: nothing
        # reserves that username, so a real account could hold it, and dissolving
        # a real owner's authority on a name is not a trade this may make. Same
        # reason the agent-handle SHAPE heuristic stays out of here.
        if project is None or project.owner_handle:
            return False
        if any(m.role == ProjectRole.lead for m in members):
            return False
        return any(m.user_handle == actor for m in members)

    async def seed(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        agent_handle: str | None = None,
    ) -> None:
        """Seed a newborn topic's roster: the creator becomes owner and 芝士
        joins as a member (fusion-design §3). Idempotent — re-seeding never
        duplicates a row. Called at topic-create time, outside the actor check
        (the platform, not a user, seeds).

        芝士 joins as THIS topic's 分身 (its own agent-user), not as the shared
        platform account — that seat is what makes the room's agent attributable
        and individually revocable."""
        if owner_handle and not self._is_agent_handle(owner_handle):
            await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        if agent_handle:
            await self.ensure_agent_seat(topic_id, agent_handle)
        else:
            await self.ensure_topic_agent_seat(topic_id)

    async def seed_root(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        """Seed a project's ROOT topic (总览/项目本体) roster: EVERY project
        member joins the room, so 总览 mirrors the whole project (fusion-design
        §3). The project owner is the topic owner; other members join as members;
        芝士 joins as a member. Idempotent — re-seeding never duplicates a row."""
        await self._seed_with_members(
            topic_id, owner_handle=owner_handle, member_handles=member_handles
        )

    async def seed_private(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str,
        peer_handle: str | None,
    ) -> None:
        """Seed exactly the two seats a private conversation contains.

        A member↔芝士 DM has the human owner plus this topic's agent seat. A
        human↔human DM has the canonical owner plus the peer and no agent.
        Idempotency also repairs private topics created before rosters existed.
        """
        await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        if peer_handle is None:
            await self.ensure_topic_agent_seat(topic_id)
        else:
            await self._ensure_member(topic_id, peer_handle, role=TopicRole.member)

    async def seed_split(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        """Seed a split-off sub-topic's roster: whoever the caller resolved as the
        person driving this work becomes owner (`TopicService.dispatch_task`
        walks that ladder — the splitter, else the human whose turn the split came
        out of, else inherited), and the parent topic's members (typically its
        human roster) join as plain members — otherwise a 分身-initiated split
        (owner_handle "cheese", skipped by seed()) leaves every human silently off
        the new topic's roster, the bug this exists to close. The parent's own
        owner arrives through that member list, so a room that changed hands keeps
        the original requester on the roster instead of dropping them. Role nuance
        (owner/admin on the parent) is deliberately NOT preserved: importing
        everyone as a plain member is simple and correct enough — the owner can
        promote people afterward if the child needs its own owner/admin split.
        Idempotent, same as seed()."""
        await self._seed_with_members(
            topic_id, owner_handle=owner_handle, member_handles=member_handles
        )

    async def _seed_with_members(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        await self.seed(topic_id, owner_handle=owner_handle)
        for handle in member_handles:
            if not handle or self._is_agent_handle(handle) or handle == owner_handle:
                continue
            await self._ensure_member(topic_id, handle, role=TopicRole.member)

    @staticmethod
    def _is_agent_handle(handle: str) -> bool:
        """Seeding-time check only: a 分身 never joins a room as a *human* member
        (it gets its own seat via :meth:`resolve_agent_handle`), and a room whose
        "owner" is 芝士 has no human owner. Both the shared platform handle and a
        per-topic 分身 handle count."""
        return looks_like_agent_handle(handle)

    async def _ensure_member(
        self, topic_id: uuid.UUID, handle: str, *, role: TopicRole
    ) -> None:
        if await self._repo.get(topic_id=topic_id, member_handle=handle) is None:
            await self._repo.add(topic_id=topic_id, member_handle=handle, role=role)

    async def list_for_topic(
        self, topic_id: uuid.UUID
    ) -> tuple[list[TopicMembership], int]:
        await self._ensure_topic(topic_id)
        return (
            await self._repo.list_for_topic(topic_id),
            await self._repo.count_for_topic(topic_id),
        )

    async def require_archive_manager(self, topic_id: uuid.UUID, actor: str) -> None:
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is None or member.role not in _MANAGER_ROLES:
            raise ForbiddenError("只有房间的 owner / admin 能归档或取消归档")

    async def managed_topic_ids(
        self, topic_ids: list[uuid.UUID], actor: str
    ) -> set[uuid.UUID]:
        return await self._repo.topic_ids_for_member(
            topic_ids, actor, roles=_MANAGER_ROLES
        )

    async def owner_of(self, topic_id: uuid.UUID) -> str | None:
        """The human this room belongs to — its ``owner`` member, or None.

        The roster is the ONLY place that answer is reliably recorded.
        ``Topic.created_by`` is not: a 分身 splitting a sub-topic creates it
        under its own ``cheese-<hex12>`` handle, so on every split topic
        ``created_by`` names a robot. Seeding already walked the ladder that
        finds the real human — :meth:`seed`/:meth:`seed_split` skip 芝士 as owner,
        and ``TopicService.dispatch_task`` falls back to the person driving
        the turn the split came out of, then the parent room's owner, then the
        project's — so this just reads what that ladder wrote.

        Cheap and unauthorized on purpose: attribution paths (who a PR and its
        commits belong to) call it on every merge, and they are read-only.
        """
        member = next(
            (
                m
                for m in await self._repo.list_for_topic(topic_id)
                if m.role == TopicRole.owner
            ),
            None,
        )
        return member.member_handle if member is not None else None

    async def topic_ids_for_member(
        self, topic_ids: list[uuid.UUID], member_handle: str
    ) -> set[uuid.UUID]:
        """Which of these topics this handle is in the roster of, in ONE query.

        A read, so it carries no roster-management authorization: the caller
        (与我的相关性 on the topic list) is asking about ITSELF, and every
        answer it gets back is about topics it was already allowed to list.
        """
        return await self._repo.topic_ids_for_member(topic_ids, member_handle)

    async def agent_handles(self, topic_id: uuid.UUID) -> list[str]:
        """Which of this topic's members are agents, in roster order.

        Derived from the execution binding, never a hard-coded handle check: a
        member is an agent iff it carries an ``AgentBinding``. Returns every
        such member, because a room may host more than one 芝士 — callers that
        need "the agent acting here" want :meth:`resolve_agent_handle`.
        """
        from app.domain.identity.repositories import AgentBindingRepository
        from app.domain.user.repositories import UserRepository

        members = await self._repo.list_for_topic(topic_id)
        if not members:
            return []
        by_handle = await UserRepository(self._session).get_by_handles(
            [m.member_handle for m in members]
        )
        agent_ids = await AgentBindingRepository(self._session).agent_user_ids(
            [u.id for u in by_handle.values()]
        )
        return [
            m.member_handle
            for m in members
            if (user := by_handle.get(m.member_handle)) is not None
            and user.id in agent_ids
        ]

    async def ensure_agent_seat(self, topic_id: uuid.UUID, handle: str) -> str:
        """Seat THIS agent in this room, and return the handle it acts under.

        The seat is the grant: an action is attributable to the agent that holds
        one, and de-authorizing it is a single row delete rather than waiting out
        a token's TTL. What the seat does not do is tell the room who its agent
        is — a room is a collaboration space and may seat several, so the caller
        names the agent it means.

        Idempotent, and cheap on the fast path (one indexed lookup)."""
        if await self._repo.get(topic_id=topic_id, member_handle=handle) is None:
            await self._repo.add(
                topic_id=topic_id, member_handle=handle, role=TopicRole.member
            )
        return handle

    async def ensure_topic_agent_seat(self, topic_id: uuid.UUID) -> str:
        """Seat the room-derived 分身. Reached only where no agent has been named
        yet — a room seeded before an agent could hold an identity of its own —
        and kept until every seat is an agent's own."""
        handle = topic_agent_handle(topic_id)
        from app.domain.identity.services import IdentityService

        await IdentityService(self._session).ensure_topic_agent_user(topic_id)
        return await self.ensure_agent_seat(topic_id, handle)

    async def migrate_shared_agent_seat(self, topic_id: uuid.UUID) -> None:
        """Retire a pre-分身独立身份 room's shared ``cheese`` seat in favour of its
        own 分身. Called when the room's agent is about to act, so rooms migrate
        themselves — no data migration, hence no alembic chain to fork.

        Deliberately narrow: it fires ONLY when the shared seat is actually there.
        A room whose agent seat was **revoked** (no agent at all), or replaced by
        another agent, is left exactly as it is — re-adding a seat here would
        silently undo a revocation, i.e. break the one capability this whole
        change exists to provide. Blocks already authored under the shared handle
        keep it; history is history.
        """
        legacy = await self._repo.get(topic_id=topic_id, member_handle=CHEESE_HANDLE)
        if legacy is None:
            return
        await self.ensure_topic_agent_seat(topic_id)
        await self._repo.delete(legacy)

    async def resolve_agent_handle(
        self, topic_id: uuid.UUID, *, room_id: uuid.UUID | None = None
    ) -> str:
        """The handle 芝士 acts under in this place — for authoring blocks and
        keying its memory. Read-only.

        The roster decides: a room hosting some other agent attributes to that
        one. With no agent seated at all, fall back to the handle this place's
        sandbox token names (``cheese-<place hex>``) — a turn still has to answer
        "who am I", and answering with the shared account would put the collapsed
        identity back into the audit trail.

        Pass ``room_id`` when ``topic_id`` is a THREAD's: the roster to read is
        the room's (threads do not have one), but the fallback has to stay the
        thread's own, because that is the handle its sandbox was started with.
        Collapsing the two would give one 分身 two names — one on the blocks it
        writes, another on the token it writes them with.
        """
        handles = await self.agent_handles(room_id or topic_id)
        return handles[0] if handles else topic_agent_handle(topic_id)

    async def add(
        self, *, topic_id: uuid.UUID, handle: str, role: TopicRole, actor: str
    ) -> TopicMembership:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        existing = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if existing is not None:
            raise ValidationError("该成员已在话题里")
        return await self._repo.add(topic_id=topic_id, member_handle=handle, role=role)

    async def update_role(
        self, *, topic_id: uuid.UUID, handle: str, role: TopicRole, actor: str
    ) -> TopicMembership:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError("成员不存在")
        # Demoting the last owner would orphan the room — block it.
        if (
            member.role == TopicRole.owner
            and role != TopicRole.owner
            and await self._repo.count_owners(topic_id) <= 1
        ):
            raise ValidationError("不能把最后一个 owner 降级")
        return await self._repo.update_role(member, role=role)

    async def remove(self, *, topic_id: uuid.UUID, handle: str, actor: str) -> None:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError("成员不存在")
        # Never remove the last owner — a topic must always have one.
        if (
            member.role == TopicRole.owner
            and await self._repo.count_owners(topic_id) <= 1
        ):
            raise ValidationError("不能移除最后一个 owner")
        await self._repo.delete(member)

    async def revoke_project_seats(
        self, *, project_id: uuid.UUID, member_handle: str
    ) -> list[uuid.UUID]:
        """把这个人从这个项目的每一间房里撤出去 —— 退项目 / 被移出项目走这里。

        返回**真的撤掉席位的房间**（``MemberService.leave`` 靠它区分「他在这份名
        册上只剩这些席位」和「他本来就和这个项目没关系」）。空列表 = 一个字节都没
        动：要么他没有席位，要么他唯一的席位在最后一个 owner 那条例外上。

        为什么项目级的退场必须走到话题这一层：项目成员身份是**进得来这个项目的全部
        话题**的凭据（``authorize_topic_access`` 认它），只删名册那一行、把话题席位
        留着，人还是每个房间都进得去 —— 退项目就只退了个名单。所以两条路（自己退、
        被 owner / lead 移出）共用这一份撤销。

        没有授权检查，因为**它不是一条被别人调用的用户动作**：调用方是项目级的退场
        动作，授权已经在那里做完了（``MemberService.leave`` 认本人，``remove`` 认
        owner / lead）。

        只管项目的话题树，不管私聊：私聊是两个人之间的一间房，不是项目发的通行证
        （``TopicRepository.list_for_project`` 本来就不含它），人离开项目不该把它
        带走。

        唯一的例外是**最后一个 owner**：撤掉他，这间房就没有人管得了 —— 无主房间在
        产品里是死路（``_require_manager`` 那个逃逸口正是为修这种房间存在的）。所以
        既不静默放行，也不替房间指定继任者：拒绝，并点名是哪间房，让人先把房间交出
        去再走。
        """
        topics = await self._topics.list_for_project(project_id)
        if not topics:
            return []
        titles = {t.id: t.title for t in topics}
        seats = await self._repo.topic_ids_for_member(list(titles), member_handle)
        if not seats:
            return []
        owners = await self._repo.owners_by_topic(sorted(seats))
        orphaned = sorted(
            titles[topic_id]
            for topic_id in seats
            if member_handle in owners.get(topic_id, []) and len(owners[topic_id]) <= 1
        )
        if orphaned:
            raise ValidationError(
                "你是话题「"
                + "」「".join(orphaned)
                + "」唯一的 owner，先把话题交给别人"
            )
        revoked: list[uuid.UUID] = []
        for topic_id in sorted(seats):
            seat = await self._repo.get(topic_id=topic_id, member_handle=member_handle)
            if seat is not None:
                await self._repo.delete(seat)
                revoked.append(topic_id)
        return revoked
