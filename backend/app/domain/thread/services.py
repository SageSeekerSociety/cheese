"""Business logic & authorization for threads (chat groups) and their membership.

One set of services behind three front doors (humans over REST, agents over the
tool door, the orchestrator). The actor is always injected at the trust boundary;
a valid session is necessary but never sufficient — every op authorizes against the
actor's real thread role. Human-vs-agent is never branched on for business rules;
it only decides presentation (Member dicts) and whether adding a member needs the
agent owner's consent (an execution-binding fact, resolved via ``agent.identity``).
"""

from typing import TYPE_CHECKING, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub
from app.agent.identity import (
    agent_owner,
    build_member_dicts,
    resolve_agent_identities,
)
from app.agent.models import AgentScreenRow
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.device.models import DeviceRow
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import publish_notification_event
from app.domain.thread.models import (
    ApplicationStatus,
    ApplicationType,
    Thread,
    ThreadMemberRole,
    ThreadMembershipApplication,
)
from app.domain.thread.repositories import (
    ThreadApplicationRepository,
    ThreadMembershipRepository,
    ThreadRepository,
)
from app.domain.user.models import User, UserProfile

if TYPE_CHECKING:
    from app.agent.orchestrator import AgentService, ThreadAgentPolicy


# -- attention policy encoding -------------------------------------------------
# Per-thread override stored in ThreadMembership.attention_policy_override as a small
# string: "ALL" | "INTERVAL:<n>" | "MENTION". A null override means MENTION for an
# agent (the safe default: only wake it when someone @'s it).
_ATTENTION_MODES = ("ALL", "INTERVAL", "MENTION")

# Quoted-preview excerpt length. Pure truncation of the referenced block's raw text —
# never NL/semantic parsing (万物皆块: the preview is structural, not interpreted).
_QUOTE_EXCERPT_LEN = 140


def _quote_excerpt(text: str) -> str:
    text = text.strip()
    if len(text) > _QUOTE_EXCERPT_LEN:
        return text[:_QUOTE_EXCERPT_LEN] + "…"
    return text


def parse_attention_policy(raw: str | None) -> tuple[str, int | None]:
    """Decode a stored override into ``(mode, interval_minutes)``. Unknown / null →
    ``("MENTION", None)``."""
    if raw:
        head, _, tail = raw.partition(":")
        head = head.strip().upper()
        if head == "INTERVAL":
            try:
                return ("INTERVAL", max(1, int(tail)))
            except ValueError:
                return ("INTERVAL", 1)
        if head in ("ALL", "MENTION"):
            return (head, None)
    return ("MENTION", None)


def format_attention_policy(mode: str, interval_minutes: int | None) -> str:
    """Encode ``(mode, interval_minutes)`` into the stored string. Raises
    ``BadRequestError`` on an invalid mode / missing interval."""
    mode = mode.strip().upper()
    if mode not in _ATTENTION_MODES:
        raise BadRequestError("mode must be ALL, INTERVAL or MENTION")
    if mode == "INTERVAL":
        if interval_minutes is None or interval_minutes < 1:
            raise BadRequestError("INTERVAL requires interval_minutes >= 1")
        return f"INTERVAL:{int(interval_minutes)}"
    return mode


class _Forwarder(Protocol):
    async def forward_message_to_thread_agents(
        self,
        *,
        thread_id: int,
        block_id: int,
        text: str,
        speaker: str,
        agent_policies: "list[ThreadAgentPolicy]",
        mentioned_ids: set[int],
    ) -> int: ...


class ThreadService:
    """Threads, messages and (via the nested membership service) members."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hub: DeviceHub,
        agent_service: "AgentService | _Forwarder",
    ) -> None:
        self._sf = session_factory
        self._hub = hub
        self._agents = agent_service

    # -- helpers -----------------------------------------------------------

    async def _require_member(self, session: AsyncSession, thread_id: int, actor_id: int) -> Thread:
        thread = await ThreadRepository(session).get(thread_id)
        if thread is None:
            raise NotFoundError("Unknown thread")
        if not await ThreadMembershipRepository(session).is_member(thread_id, actor_id):
            raise ForbiddenError("must be a member of the thread")
        return thread

    async def _require_admin(self, session: AsyncSession, thread_id: int, actor_id: int) -> Thread:
        thread = await ThreadRepository(session).get(thread_id)
        if thread is None:
            raise NotFoundError("Unknown thread")
        role = await ThreadMembershipRepository(session).role_of(thread_id, actor_id)
        if role is None or role < ThreadMemberRole.ADMIN:
            raise ForbiddenError("must be a thread admin or owner")
        return thread

    async def _require_owner(self, session: AsyncSession, thread_id: int, actor_id: int) -> Thread:
        thread = await ThreadRepository(session).get(thread_id)
        if thread is None:
            raise NotFoundError("Unknown thread")
        role = await ThreadMembershipRepository(session).role_of(thread_id, actor_id)
        if role != ThreadMemberRole.OWNER:
            raise ForbiddenError("must be the thread owner")
        return thread

    async def _thread_json(self, session: AsyncSession, thread: Thread) -> dict[str, object]:
        members = ThreadMembershipRepository(session)
        blocks = BlockRepository(session)
        last = await blocks.latest(thread.id)
        last_json: dict[str, object] | None = None
        if last is not None:
            last_json = {
                "text": last.content,
                "ts": last.created_at.isoformat(),
                "author_id": last.author_id,
            }
        return {
            "id": thread.id,
            "kind": int(thread.kind),
            "title": thread.title,
            "project_id": thread.project_id,
            "member_count": await members.member_count(thread.id),
            "last_message": last_json,
        }

    async def _message_json(
        self, session: AsyncSession, block: Block
    ) -> dict[str, object]:
        author = (await build_member_dicts(session, self._hub, [block.author_id]))[0]
        author.pop("role", None)
        deleted = block.deleted_at is not None
        result: dict[str, object] = {
            "id": block.id,
            "thread_id": block.thread_id,
            "author_id": block.author_id,
            "author": author,
            # A deleted message is a tombstone: it still appears in listings so clients
            # update in place, but its content is blanked (and it drops its quoted preview).
            "text": "" if deleted else block.content,
            "ts": block.created_at.isoformat(),
            "reply_to_id": block.reply_to_id,
            "deleted": deleted,
            "pinned": block.pinned_at is not None,
        }
        if not deleted and block.reply_to_id is not None:
            result["quoted"] = await self._quoted_preview(
                session, block.thread_id, block.reply_to_id
            )
        return result

    async def _quoted_preview(
        self, session: AsyncSession, thread_id: int | None, reply_to_id: int
    ) -> dict[str, object]:
        """Small structural preview of the block a message replies to: author + a
        truncated excerpt, or ``deleted`` when it is missing / in another thread."""
        ref = (
            await BlockRepository(session).get_message_in_thread(thread_id, reply_to_id)
            if thread_id is not None
            else None
        )
        if ref is None:
            return {"id": reply_to_id, "author_id": None, "excerpt": None, "deleted": True}
        return {
            "id": ref.id,
            "author_id": ref.author_id,
            "excerpt": _quote_excerpt(ref.content),
            "deleted": False,
        }

    # -- threads -----------------------------------------------------------

    async def create_thread(
        self, actor_id: int, title: str, member_ids: list[int] | None = None
    ) -> dict[str, object]:
        member_ids = member_ids or []
        async with self._sf() as session:
            threads = ThreadRepository(session)
            memberships = ThreadMembershipRepository(session)
            thread = await threads.create(title=title, created_by=actor_id)
            await memberships.add(thread.id, actor_id, ThreadMemberRole.OWNER)
            agents = await resolve_agent_identities(
                session, self._hub, [m for m in member_ids if m != actor_id]
            )
            for uid in member_ids:
                if uid == actor_id:
                    continue
                if uid in agents:
                    await self._invite_agent(session, thread, uid, actor_id, ThreadMemberRole.MEMBER)
                else:
                    await memberships.add(thread.id, uid, ThreadMemberRole.MEMBER)
            result = await self._thread_json(session, thread)
            await session.commit()
        return {"thread": result}

    async def list_threads(self, actor_id: int) -> dict[str, object]:
        async with self._sf() as session:
            threads = await ThreadRepository(session).threads_for_user(actor_id)
            return {"threads": [await self._thread_json(session, t) for t in threads]}

    async def get_thread(self, actor_id: int, thread_id: int) -> dict[str, object]:
        async with self._sf() as session:
            thread = await self._require_member(session, thread_id, actor_id)
            members = await self._members_json(session, thread_id)
            return {
                "thread": await self._thread_json(session, thread),
                "members": members,
            }

    async def rename(self, actor_id: int, thread_id: int, title: str) -> dict[str, object]:
        async with self._sf() as session:
            thread = await self._require_admin(session, thread_id, actor_id)
            await ThreadRepository(session).rename(thread, title)
            result = await self._thread_json(session, thread)
            await session.commit()
        return {"thread": result}

    # -- messages ----------------------------------------------------------

    async def list_messages(
        self, actor_id: int, thread_id: int, after: int = 0
    ) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            blocks = await BlockRepository(session).messages_since(thread_id, after)
            return {"messages": [await self._message_json(session, b) for b in blocks]}

    async def post_message(
        self,
        actor_id: int,
        thread_id: int,
        text: str,
        mention_user_ids: list[int] | None = None,
        reply_to_id: int | None = None,
    ) -> dict[str, object]:
        # Deferred import avoids a module-load cycle (orchestrator ⇄ thread service).
        from app.agent.orchestrator import ThreadAgentPolicy

        async with self._sf() as session:
            thread = await self._require_member(session, thread_id, actor_id)
            block = await BlockRepository(session).add_message(
                thread_id=thread_id,
                project_id=thread.project_id,
                author_id=actor_id,
                text=text,
                reply_to_id=reply_to_id,
            )
            await ThreadRepository(session).touch(thread_id)

            rows = await ThreadMembershipRepository(session).members(thread_id)
            member_dicts = await build_member_dicts(
                session, self._hub, [r.user_id for r in rows]
            )
            agents = await resolve_agent_identities(
                session, self._hub, [r.user_id for r in rows]
            )

            # Mentions: explicit ids from the client, augmented by a fallback scan of the
            # text for `@<nickname>` against member nicknames.
            mentioned: set[int] = set(mention_user_ids or [])
            for m in member_dicts:
                nickname = str(m.get("nickname", ""))
                if nickname and f"@{nickname}" in text:
                    uid = m.get("user_id")
                    if isinstance(uid, int):
                        mentioned.add(uid)

            policies: list[ThreadAgentPolicy] = []
            for r in rows:
                # Never forward a message back to its own author — otherwise an agent that
                # posts (via post-note, which now runs this same pipeline) gets woken by its
                # own message, which at "立即" attention is an infinite self-notification loop.
                if r.user_id in agents and r.user_id != actor_id:
                    mode, interval = parse_attention_policy(r.attention_policy_override)
                    policies.append(
                        ThreadAgentPolicy(
                            user_id=r.user_id,
                            mode=mode,
                            interval_minutes=interval,
                            role=int(r.role),
                        )
                    )

            speaker_name = next(
                (str(m.get("nickname")) for m in member_dicts if m.get("user_id") == actor_id),
                f"user#{actor_id}",
            )
            message = await self._message_json(session, block)
            await session.commit()

        reached = 0
        if policies:
            reached = await self._agents.forward_message_to_thread_agents(
                thread_id=thread_id,
                block_id=block.id,
                text=text,
                speaker=speaker_name,
                agent_policies=policies,
                mentioned_ids=mentioned,
            )
        return {"message": message, "forwarded_to_agents": reached}

    async def mark_read(
        self, actor_id: int, thread_id: int, last_read_id: int
    ) -> dict[str, object]:
        """Advance the caller's read high-water mark in a thread (Feishu-style 已阅).
        Monotonic; the caller must be a member."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            watermark = await ThreadMembershipRepository(session).set_read_watermark(
                thread_id, actor_id, last_read_id
            )
            await session.commit()
        return {"ok": True, "last_read_block_id": watermark or 0}

    # -- message actions (飞书式: 删除 / 置顶 / 转发) ------------------------

    async def _require_message(
        self, session: AsyncSession, thread_id: int, block_id: int
    ) -> Block:
        block = await BlockRepository(session).get_message_in_thread(thread_id, block_id)
        if block is None:
            raise NotFoundError("Unknown message")
        return block

    async def delete_message(
        self, actor_id: int, thread_id: int, block_id: int
    ) -> dict[str, object]:
        """删除 (soft delete): tombstone a message. Allowed if the actor is the author,
        or a thread ADMIN/OWNER. The row is kept so replies/quotes degrade gracefully."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            block = await self._require_message(session, thread_id, block_id)
            if block.author_id != actor_id:
                role = await ThreadMembershipRepository(session).role_of(thread_id, actor_id)
                if role is None or role < ThreadMemberRole.ADMIN:
                    raise ForbiddenError("only the author or a thread admin may delete a message")
            await BlockRepository(session).set_deleted(block, deleted=True)
            message = await self._message_json(session, block)
            await session.commit()
        return {"message": message}

    async def pin_message(
        self, actor_id: int, thread_id: int, block_id: int
    ) -> dict[str, object]:
        """置顶: pin a message. Any member may pin."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            block = await self._require_message(session, thread_id, block_id)
            await BlockRepository(session).set_pinned(block, pinned=True)
            message = await self._message_json(session, block)
            await session.commit()
        return {"message": message}

    async def unpin_message(
        self, actor_id: int, thread_id: int, block_id: int
    ) -> dict[str, object]:
        """取消置顶: unpin a message. Any member may unpin."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            block = await self._require_message(session, thread_id, block_id)
            await BlockRepository(session).set_pinned(block, pinned=False)
            message = await self._message_json(session, block)
            await session.commit()
        return {"message": message}

    async def list_pins(self, actor_id: int, thread_id: int) -> dict[str, object]:
        """List a thread's pinned messages, most-recently-pinned first. Member-only."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            blocks = await BlockRepository(session).list_pinned(thread_id)
            return {"messages": [await self._message_json(session, b) for b in blocks]}

    async def forward_message(
        self, actor_id: int, from_thread_id: int, block_id: int, to_thread_id: int
    ) -> dict[str, object]:
        """转发: copy a message into another thread. The actor must be a member of BOTH
        threads. A new message authored by the actor is created in the target thread,
        copying the source text, with provenance recorded in ``refs``."""
        async with self._sf() as session:
            await self._require_member(session, from_thread_id, actor_id)
            target = await self._require_member(session, to_thread_id, actor_id)
            source = await self._require_message(session, from_thread_id, block_id)
            ref = f"forwarded_from:{from_thread_id}:{block_id}"
            block = await BlockRepository(session).add_message(
                thread_id=to_thread_id,
                project_id=target.project_id,
                author_id=actor_id,
                text=source.content,
                refs=[ref],
            )
            await ThreadRepository(session).touch(to_thread_id)
            message = await self._message_json(session, block)
            await session.commit()
        return {"message": message}

    # -- members -----------------------------------------------------------

    async def members(self, actor_id: int, thread_id: int) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            return {"members": await self._members_json(session, thread_id)}

    async def _members_json(
        self, session: AsyncSession, thread_id: int
    ) -> list[dict[str, object]]:
        rows = await ThreadMembershipRepository(session).members(thread_id)
        roles = {r.user_id: int(r.role) for r in rows}
        watermarks = {r.user_id: int(r.last_read_block_id or 0) for r in rows}
        return await build_member_dicts(
            session,
            self._hub,
            [r.user_id for r in rows],
            roles=roles,
            read_watermarks=watermarks,
        )

    async def add_member(
        self, actor_id: int, thread_id: int, user_id: int, role: int = ThreadMemberRole.MEMBER
    ) -> dict[str, object]:
        target_role = ThreadMemberRole(role)
        async with self._sf() as session:
            thread = await self._require_admin(session, thread_id, actor_id)
            agents = await resolve_agent_identities(session, self._hub, [user_id])
            if user_id in agents:
                # Adding an agent needs its owner's consent — EXCEPT when the inviter is
                # that owner (no point asking yourself); then add it directly.
                owner = await agent_owner(session, user_id)
                if owner != actor_id:
                    app = await self._invite_agent(session, thread, user_id, actor_id, target_role)
                    result: dict[str, object] = {"pending": True, "application_id": app.id}
                    await session.commit()
                    return result
            member = await self._add_member_row(session, thread_id, user_id, target_role)
            await session.commit()
            return {"added": True, "member": member}

    async def _add_member_row(
        self, session: AsyncSession, thread_id: int, user_id: int, role: ThreadMemberRole
    ) -> dict[str, object]:
        await ThreadMembershipRepository(session).add(thread_id, user_id, role)
        members = await build_member_dicts(
            session, self._hub, [user_id], roles={user_id: int(role)}
        )
        return members[0]

    async def _invite_agent(
        self,
        session: AsyncSession,
        thread: Thread,
        agent_user_id: int,
        initiator_id: int,
        role: ThreadMemberRole,
    ) -> ThreadMembershipApplication:
        owner = await agent_owner(session, agent_user_id)
        app = await ThreadApplicationRepository(session).create(
            thread_id=thread.id,
            user_id=agent_user_id,
            initiator_id=initiator_id,
            approver_id=owner,
            type_=ApplicationType.INVITATION,
            role=role,
        )
        if owner is not None:
            await publish_notification_event(
                session,
                recipient_ids=[owner],
                type_=NotificationType.TEAM_INVITATION,
                payload={
                    "kind": "thread_invite",
                    "application_id": app.id,
                    "thread_id": thread.id,
                    "thread_title": thread.title,
                    "agent_user_id": agent_user_id,
                    "initiator_id": initiator_id,
                },
                actor_id=initiator_id,
            )
        return app

    async def remove_member(
        self, actor_id: int, thread_id: int, user_id: int
    ) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_admin(session, thread_id, actor_id)
            memberships = ThreadMembershipRepository(session)
            role = await memberships.role_of(thread_id, user_id)
            if role == ThreadMemberRole.OWNER:
                raise ForbiddenError("cannot remove the thread owner")
            await memberships.remove(thread_id, user_id)
            await session.commit()
        return {"removed": True}

    async def change_role(
        self, actor_id: int, thread_id: int, user_id: int, role: int
    ) -> dict[str, object]:
        """Promote/demote a member between MEMBER and ADMIN. Owner-only; the owner's
        own role can't be changed, and ownership can't be granted here."""
        if role not in (ThreadMemberRole.MEMBER, ThreadMemberRole.ADMIN):
            raise BadRequestError("role must be 0 (member) or 1 (admin)")
        async with self._sf() as session:
            await self._require_owner(session, thread_id, actor_id)
            memberships = ThreadMembershipRepository(session)
            current = await memberships.role_of(thread_id, user_id)
            if current is None:
                raise NotFoundError("target is not a member of this thread")
            if current == ThreadMemberRole.OWNER:
                raise ForbiddenError("cannot change the owner's role")
            await memberships.update_role(thread_id, user_id, ThreadMemberRole(role))
            member = (
                await build_member_dicts(
                    session, self._hub, [user_id], roles={user_id: role}
                )
            )[0]
            await session.commit()
        return {"member": member}

    async def dissolve(self, actor_id: int, thread_id: int) -> dict[str, object]:
        """Dissolve a thread — owner-only. Soft-deletes the thread, every membership,
        and cancels any still-pending applications."""
        async with self._sf() as session:
            thread = await self._require_owner(session, thread_id, actor_id)
            await ThreadApplicationRepository(session).cancel_pending_for_thread(
                thread_id, processed_by=actor_id
            )
            await ThreadMembershipRepository(session).remove_all(thread_id)
            await ThreadRepository(session).soft_delete(thread)
            await session.commit()
        return {"deleted": True}

    # -- attention policy --------------------------------------------------

    async def get_attention(self, actor_id: int, thread_id: int) -> dict[str, object]:
        """List the thread's agent members with their per-thread attention policy.
        Any member may read it."""
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            rows = await ThreadMembershipRepository(session).members(thread_id)
            agents = await resolve_agent_identities(
                session, self._hub, [r.user_id for r in rows]
            )
            dicts = {
                d["user_id"]: d
                for d in await build_member_dicts(
                    session, self._hub, [r.user_id for r in rows if r.user_id in agents]
                )
            }
            out: list[dict[str, object]] = []
            for r in rows:
                if r.user_id not in agents:
                    continue
                mode, interval = parse_attention_policy(r.attention_policy_override)
                d = dicts.get(r.user_id, {})
                entry: dict[str, object] = {
                    "user_id": r.user_id,
                    "nickname": d.get("nickname"),
                    "avatar_id": d.get("avatar_id"),
                    "mode": mode,
                }
                if interval is not None:
                    entry["interval_minutes"] = interval
                out.append(entry)
            return {"agents": out}

    async def set_attention(
        self,
        actor_id: int,
        thread_id: int,
        agent_user_id: int,
        mode: str,
        interval_minutes: int | None,
    ) -> dict[str, object]:
        """Set an agent member's per-thread attention policy. Any member may set it
        (consistent with 'any member may message/operate the agent')."""
        value = format_attention_policy(mode, interval_minutes)
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            memberships = ThreadMembershipRepository(session)
            if not await memberships.is_member(thread_id, agent_user_id):
                raise NotFoundError("target is not a member of this thread")
            agents = await resolve_agent_identities(session, self._hub, [agent_user_id])
            if agent_user_id not in agents:
                raise BadRequestError("target is not an agent")
            await memberships.set_attention(thread_id, agent_user_id, value)
            parsed_mode, parsed_interval = parse_attention_policy(value)
            d = (await build_member_dicts(session, self._hub, [agent_user_id]))[0]
            await session.commit()
        agent: dict[str, object] = {
            "user_id": agent_user_id,
            "nickname": d.get("nickname"),
            "avatar_id": d.get("avatar_id"),
            "mode": parsed_mode,
        }
        if parsed_interval is not None:
            agent["interval_minutes"] = parsed_interval
        return {"agent": agent}

    async def candidates(
        self, actor_id: int, thread_id: int, q: str
    ) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            member_ids = {
                r.user_id for r in await ThreadMembershipRepository(session).members(thread_id)
            }
            candidate_ids: set[int] = set()
            human_ids: set[int] = set()

            # Human candidates: fuzzy search across ALL users by nickname OR username.
            if q.strip():
                needle_like = f"%{q.strip()}%"
                rows = (
                    await session.execute(
                        select(UserProfile.user_id)
                        .join(User, User.id == UserProfile.user_id)
                        .where(
                            or_(
                                UserProfile.nickname.ilike(needle_like),
                                User.username.ilike(needle_like),
                            ),
                            UserProfile.deleted_at.is_(None),
                        )
                        .limit(20)
                    )
                ).scalars()
                human_ids.update(rows)
                candidate_ids.update(human_ids)

            # Agent candidates: agents running on the actor's own devices.
            device_ids = {
                d
                for d in (
                    await session.execute(
                        select(DeviceRow.device_id).where(DeviceRow.owner_user_id == actor_id)
                    )
                ).scalars()
            }
            agent_rows = (
                await session.execute(select(AgentScreenRow.agent_user_id, AgentScreenRow.device_id))
            ).all()
            for agent_user_id, device_id in agent_rows:
                if device_id in device_ids:
                    candidate_ids.add(agent_user_id)

            candidate_ids -= member_ids
            candidate_ids.discard(actor_id)
            refs = await build_member_dicts(session, self._hub, sorted(candidate_ids))
            if q.strip():
                # Humans are already q-filtered by the DB (nickname OR username); only
                # narrow the agent candidates by nickname so the search box stays focused.
                needle = q.strip().lower()
                refs = [
                    r
                    for r in refs
                    if r.get("user_id") in human_ids
                    or needle in str(r.get("nickname", "")).lower()
                ]
            for ref in refs:
                ref.pop("role", None)
            return {"candidates": refs}

    # -- applications ------------------------------------------------------

    async def _application_json(
        self, session: AsyncSession, app: ThreadMembershipApplication
    ) -> dict[str, object]:
        thread = await ThreadRepository(session).get(app.thread_id)
        user = (await build_member_dicts(session, self._hub, [app.user_id]))[0]
        user.pop("role", None)
        return {
            "id": app.id,
            "thread_id": app.thread_id,
            "thread_title": thread.title if thread is not None else None,
            "user_id": app.user_id,
            "user": user,
            "initiator_id": app.initiator_id,
            "type": app.type,
            "status": app.status,
            "role": int(app.role),
            "message": app.message,
            "created_at": app.created_at.isoformat(),
        }

    async def list_applications(self, actor_id: int) -> dict[str, object]:
        async with self._sf() as session:
            apps = await ThreadApplicationRepository(session).pending_for_approver(actor_id)
            return {"applications": [await self._application_json(session, a) for a in apps]}

    async def list_thread_applications(
        self, actor_id: int, thread_id: int
    ) -> dict[str, object]:
        """PENDING invites/requests for THIS thread — owner/admin only. Distinct from
        ``list_applications`` (the approvals *I* must act on)."""
        async with self._sf() as session:
            await self._require_admin(session, thread_id, actor_id)
            apps = await ThreadApplicationRepository(session).pending_for_thread(thread_id)
            return {"applications": [await self._application_json(session, a) for a in apps]}

    async def cancel_application(
        self, actor_id: int, thread_id: int, app_id: int
    ) -> dict[str, object]:
        """Owner/admin withdraws a pending invite/request of this thread."""
        async with self._sf() as session:
            await self._require_admin(session, thread_id, actor_id)
            repo = ThreadApplicationRepository(session)
            app = await repo.get(app_id)
            if app is None or app.thread_id != thread_id:
                raise NotFoundError("Unknown application")
            if app.status == ApplicationStatus.PENDING.value:
                await repo.set_status(app, ApplicationStatus.CANCELED, processed_by=actor_id)
            await session.commit()
        return {"canceled": True}

    async def approve_application(self, actor_id: int, app_id: int) -> dict[str, object]:
        async with self._sf() as session:
            repo = ThreadApplicationRepository(session)
            app = await repo.get(app_id)
            if app is None:
                raise NotFoundError("Unknown application")
            if app.approver_id != actor_id:
                raise ForbiddenError("only the approver may act on this application")
            if app.status == ApplicationStatus.PENDING.value:
                await ThreadMembershipRepository(session).add(
                    app.thread_id, app.user_id, ThreadMemberRole(app.role)
                )
                await repo.set_status(app, ApplicationStatus.APPROVED, processed_by=actor_id)
            result = await self._application_json(session, app)
            await session.commit()
        return {"application": result}

    async def reject_application(self, actor_id: int, app_id: int) -> dict[str, object]:
        async with self._sf() as session:
            repo = ThreadApplicationRepository(session)
            app = await repo.get(app_id)
            if app is None:
                raise NotFoundError("Unknown application")
            if app.approver_id != actor_id:
                raise ForbiddenError("only the approver may act on this application")
            if app.status == ApplicationStatus.PENDING.value:
                await repo.set_status(app, ApplicationStatus.REJECTED, processed_by=actor_id)
            result = await self._application_json(session, app)
            await session.commit()
        return {"application": result}


# Retained name for symmetry with the contract's naming (ThreadMembershipService);
# the membership operations live on ThreadService above so they share its helpers.
ThreadMembershipService = ThreadService
