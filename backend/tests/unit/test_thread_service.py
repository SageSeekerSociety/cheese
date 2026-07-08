"""Unit tests for the chat ``ThreadService`` (behavioural, no database).

The service builds its repositories internally from a ``session_factory`` and leans
on ``app.agent.identity`` helpers to decide agent-ness / owner resolution. We keep
the real service and replace only its collaborators: an in-memory ``_Store`` behind
fake repositories, and stubs for the identity helpers + notification publisher. This
lets us assert *actual behaviour* — authorization outcomes, the OWNER seeding, and
the agent consent flow — without touching a DB.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.identity import AgentIdentity
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.thread.models import (
    ApplicationStatus,
    ApplicationType,
    ThreadKind,
    ThreadMemberRole,
)
from app.domain.thread.services import ThreadService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# In-memory store + fake repositories
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    """Backing state shared by the fake repositories within one service call."""

    threads: dict[int, SimpleNamespace] = field(default_factory=dict)
    memberships: list[SimpleNamespace] = field(default_factory=list)
    applications: list[SimpleNamespace] = field(default_factory=list)
    blocks: list[SimpleNamespace] = field(default_factory=list)
    # user_id -> owner_user_id. A user_id present here is treated as an agent.
    agents: dict[int, int | None] = field(default_factory=dict)
    _thread_seq: int = 100
    _member_seq: int = 1000
    _app_seq: int = 5000
    _block_seq: int = 8000

    def next_block_id(self) -> int:
        self._block_seq += 1
        return self._block_seq

    def add_block(
        self, *, thread_id: int, author_id: int, content: str, reply_to_id: int | None = None
    ) -> SimpleNamespace:
        block = SimpleNamespace(
            id=self.next_block_id(),
            thread_id=thread_id,
            author_id=author_id,
            content=content,
            reply_to_id=reply_to_id,
            refs=None,
            deleted_at=None,
            pinned_at=None,
            kind=0,
            created_at=_NOW,
        )
        self.blocks.append(block)
        return block

    def next_thread_id(self) -> int:
        self._thread_seq += 1
        return self._thread_seq

    def next_member_id(self) -> int:
        self._member_seq += 1
        return self._member_seq

    def next_app_id(self) -> int:
        self._app_seq += 1
        return self._app_seq

    def add_thread(self, thread_id: int, *, created_by: int = 1) -> SimpleNamespace:
        thread = SimpleNamespace(
            id=thread_id,
            project_id=None,
            kind=ThreadKind.GENERAL,
            title=f"thread{thread_id}",
            created_by=created_by,
            deleted_at=None,
        )
        self.threads[thread_id] = thread
        return thread

    def add_membership(self, thread_id: int, user_id: int, role: int) -> SimpleNamespace:
        m = SimpleNamespace(
            id=self.next_member_id(),
            thread_id=thread_id,
            user_id=user_id,
            role=int(role),
            attention_policy_override=None,
            deleted_at=None,
        )
        self.memberships.append(m)
        return m


class _FakeThreadRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    async def get(self, thread_id: int) -> SimpleNamespace | None:
        thread = self._s.threads.get(thread_id)
        if thread is None or thread.deleted_at is not None:
            return None
        return thread

    async def create(
        self,
        *,
        title: str | None,
        created_by: int,
        kind: ThreadKind = ThreadKind.GENERAL,
        project_id: int | None = None,
    ) -> SimpleNamespace:
        thread = self._s.add_thread(self._s.next_thread_id(), created_by=created_by)
        thread.title = title
        thread.kind = kind
        thread.project_id = project_id
        return thread

    async def rename(self, thread: SimpleNamespace, title: str) -> SimpleNamespace:
        thread.title = title
        return thread

    async def soft_delete(self, thread: SimpleNamespace) -> None:
        thread.deleted_at = _NOW

    async def touch(self, thread_id: int) -> None:
        return None


class _FakeMembershipRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def _live(self, thread_id: int, user_id: int) -> SimpleNamespace | None:
        return next(
            (
                m
                for m in self._s.memberships
                if m.thread_id == thread_id and m.user_id == user_id and m.deleted_at is None
            ),
            None,
        )

    async def add(
        self, thread_id: int, user_id: int, role: ThreadMemberRole = ThreadMemberRole.MEMBER
    ) -> SimpleNamespace:
        existing = next(
            (m for m in self._s.memberships if m.thread_id == thread_id and m.user_id == user_id),
            None,
        )
        if existing is not None:
            existing.deleted_at = None
            existing.role = int(role)
            return existing
        return self._s.add_membership(thread_id, user_id, int(role))

    async def remove(self, thread_id: int, user_id: int) -> bool:
        m = self._live(thread_id, user_id)
        if m is None:
            return False
        m.deleted_at = _NOW
        return True

    async def members(self, thread_id: int) -> list[SimpleNamespace]:
        return [
            m for m in self._s.memberships if m.thread_id == thread_id and m.deleted_at is None
        ]

    async def member_count(self, thread_id: int) -> int:
        return len(await self.members(thread_id))

    async def role_of(self, thread_id: int, user_id: int) -> int | None:
        m = self._live(thread_id, user_id)
        return m.role if m is not None else None

    async def is_member(self, thread_id: int, user_id: int) -> bool:
        return self._live(thread_id, user_id) is not None

    async def update_role(
        self, thread_id: int, user_id: int, role: ThreadMemberRole
    ) -> SimpleNamespace | None:
        m = self._live(thread_id, user_id)
        if m is None:
            return None
        m.role = int(role)
        return m

    async def remove_all(self, thread_id: int) -> None:
        for m in await self.members(thread_id):
            m.deleted_at = _NOW

    async def set_attention(
        self, thread_id: int, user_id: int, value: str | None
    ) -> SimpleNamespace | None:
        m = self._live(thread_id, user_id)
        if m is None:
            return None
        m.attention_policy_override = value
        return m


class _FakeAppRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    async def create(
        self,
        *,
        thread_id: int,
        user_id: int,
        initiator_id: int,
        approver_id: int | None,
        type_: ApplicationType,
        role: ThreadMemberRole = ThreadMemberRole.MEMBER,
        message: str | None = None,
    ) -> SimpleNamespace:
        app = SimpleNamespace(
            id=self._s.next_app_id(),
            thread_id=thread_id,
            user_id=user_id,
            initiator_id=initiator_id,
            approver_id=approver_id,
            type=type_.value,
            status=ApplicationStatus.PENDING.value,
            role=int(role),
            message=message,
            processed_by_id=None,
            processed_at=None,
        )
        self._s.applications.append(app)
        return app

    async def get(self, app_id: int) -> SimpleNamespace | None:
        return next((a for a in self._s.applications if a.id == app_id), None)

    async def pending_for_thread(self, thread_id: int) -> list[SimpleNamespace]:
        return [
            a
            for a in self._s.applications
            if a.thread_id == thread_id and a.status == ApplicationStatus.PENDING.value
        ]

    async def cancel_pending_for_thread(self, thread_id: int, *, processed_by: int) -> None:
        for a in await self.pending_for_thread(thread_id):
            a.status = ApplicationStatus.CANCELED.value
            a.processed_by_id = processed_by

    async def pending_for_approver(self, user_id: int) -> list[SimpleNamespace]:
        return [
            a
            for a in self._s.applications
            if a.approver_id == user_id and a.status == ApplicationStatus.PENDING.value
        ]

    async def set_status(
        self, app: SimpleNamespace, status: ApplicationStatus, *, processed_by: int
    ) -> SimpleNamespace:
        app.status = status.value
        app.processed_by_id = processed_by
        return app


class _FakeBlockRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    async def latest(self, thread_id: int) -> None:
        return None

    async def add_message(
        self,
        *,
        thread_id: int,
        project_id: int | None,
        author_id: int,
        text: str,
        reply_to_id: int | None = None,
        refs: list | None = None,
    ) -> SimpleNamespace:
        block = self._s.add_block(
            thread_id=thread_id, author_id=author_id, content=text, reply_to_id=reply_to_id
        )
        block.refs = refs
        return block

    async def get_message_in_thread(
        self, thread_id: int | None, block_id: int
    ) -> SimpleNamespace | None:
        return next(
            (
                b
                for b in self._s.blocks
                if b.id == block_id and b.thread_id == thread_id and b.kind == 0
            ),
            None,
        )

    async def messages_since(self, thread_id: int, after_id: int = 0) -> list[SimpleNamespace]:
        return [
            b
            for b in self._s.blocks
            if b.thread_id == thread_id and b.kind == 0 and b.id > after_id
        ]

    async def set_deleted(
        self, block: SimpleNamespace, *, deleted: bool = True
    ) -> SimpleNamespace:
        block.deleted_at = _NOW if deleted else None
        return block

    async def set_pinned(
        self, block: SimpleNamespace, *, pinned: bool = True
    ) -> SimpleNamespace:
        block.pinned_at = _NOW if pinned else None
        return block

    async def list_pinned(self, thread_id: int) -> list[SimpleNamespace]:
        return [
            b
            for b in self._s.blocks
            if b.thread_id == thread_id and b.kind == 0 and b.pinned_at is not None
        ]


# ---------------------------------------------------------------------------
# Service harness
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    svc: ThreadService
    store: _Store
    session: MagicMock
    publish: AsyncMock


def _session_factory(session: MagicMock) -> Callable[[], object]:
    class _SessionCtx:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    return lambda: _SessionCtx()


@pytest.fixture
def ctx() -> Iterator[_Ctx]:
    store = _Store()
    session = MagicMock()
    session.commit = AsyncMock()
    svc = ThreadService(_session_factory(session), MagicMock(), AsyncMock())  # type: ignore[arg-type]

    async def _resolve(
        _session: object, _hub: object, user_ids: list[int]
    ) -> dict[int, AgentIdentity]:
        return {uid: AgentIdentity(user_id=uid) for uid in set(user_ids) if uid in store.agents}

    async def _owner(_session: object, agent_user_id: int) -> int | None:
        return store.agents.get(agent_user_id)

    async def _members(
        _session: object,
        _hub: object,
        user_ids: list[int],
        *,
        roles: dict[int, int] | None = None,
    ) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for uid in user_ids:
            member: dict[str, object] = {
                "user_id": uid,
                "nickname": f"user{uid}",
                "avatar_id": None,
                "is_agent": uid in store.agents,
            }
            if roles is not None and uid in roles:
                member["role"] = roles[uid]
            out.append(member)
        return out

    publish = AsyncMock()
    mod = "app.domain.thread.services"
    with ExitStack() as es:
        es.enter_context(patch(f"{mod}.ThreadRepository", lambda _s: _FakeThreadRepo(store)))
        es.enter_context(
            patch(f"{mod}.ThreadMembershipRepository", lambda _s: _FakeMembershipRepo(store))
        )
        es.enter_context(
            patch(f"{mod}.ThreadApplicationRepository", lambda _s: _FakeAppRepo(store))
        )
        es.enter_context(patch(f"{mod}.BlockRepository", lambda _s: _FakeBlockRepo(store)))
        es.enter_context(patch(f"{mod}.resolve_agent_identities", _resolve))
        es.enter_context(patch(f"{mod}.agent_owner", _owner))
        es.enter_context(patch(f"{mod}.build_member_dicts", _members))
        es.enter_context(patch(f"{mod}.publish_notification_event", publish))
        yield _Ctx(svc=svc, store=store, session=session, publish=publish)


# ---------------------------------------------------------------------------
# create_thread
# ---------------------------------------------------------------------------


class TestCreateThread:
    async def test_creator_becomes_owner(self, ctx: _Ctx) -> None:
        result = await ctx.svc.create_thread(actor_id=1, title="hi")

        thread = result["thread"]
        assert isinstance(thread, dict)
        thread_id = thread["id"]
        assert isinstance(thread_id, int)
        owner = next(m for m in ctx.store.memberships if m.user_id == 1)
        assert owner.thread_id == thread_id
        assert owner.role == ThreadMemberRole.OWNER

    async def test_returns_thread_dict_with_title(self, ctx: _Ctx) -> None:
        result = await ctx.svc.create_thread(actor_id=1, title="Team chat")

        thread = result["thread"]
        assert isinstance(thread, dict)
        assert thread["title"] == "Team chat"
        # creator counts as the single member
        assert thread["member_count"] == 1

    async def test_human_member_ids_added_directly(self, ctx: _Ctx) -> None:
        result = await ctx.svc.create_thread(actor_id=1, title="hi", member_ids=[2, 3])

        thread = result["thread"]
        assert isinstance(thread, dict)
        assert thread["member_count"] == 3
        roles = {m.user_id: m.role for m in ctx.store.memberships}
        assert roles[2] == ThreadMemberRole.MEMBER
        assert roles[3] == ThreadMemberRole.MEMBER

    async def test_agent_member_id_needs_consent_not_added(self, ctx: _Ctx) -> None:
        # user 9 is an agent owned by someone other than the creator
        ctx.store.agents[9] = 77
        result = await ctx.svc.create_thread(actor_id=1, title="hi", member_ids=[9])

        thread = result["thread"]
        assert isinstance(thread, dict)
        # only the creator is an actual member; the agent got an invitation instead
        assert thread["member_count"] == 1
        assert not any(m.user_id == 9 for m in ctx.store.memberships)
        assert len(ctx.store.applications) == 1
        assert ctx.store.applications[0].user_id == 9


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


class TestRename:
    async def test_owner_can_rename(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        result = await ctx.svc.rename(actor_id=1, thread_id=1, title="New")

        thread = result["thread"]
        assert isinstance(thread, dict)
        assert thread["title"] == "New"

    async def test_admin_can_rename(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)

        result = await ctx.svc.rename(actor_id=5, thread_id=1, title="New")

        thread = result["thread"]
        assert isinstance(thread, dict)
        assert thread["title"] == "New"

    async def test_plain_member_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.rename(actor_id=5, thread_id=1, title="New")

    async def test_non_member_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)

        with pytest.raises(ForbiddenError):
            await ctx.svc.rename(actor_id=99, thread_id=1, title="New")

    async def test_unknown_thread_raises_not_found(self, ctx: _Ctx) -> None:
        with pytest.raises(NotFoundError):
            await ctx.svc.rename(actor_id=1, thread_id=404, title="New")


# ---------------------------------------------------------------------------
# dissolve
# ---------------------------------------------------------------------------


class TestDissolve:
    async def test_owner_can_dissolve(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        ctx.store.add_membership(1, 2, ThreadMemberRole.MEMBER)

        result = await ctx.svc.dissolve(actor_id=1, thread_id=1)

        assert result == {"deleted": True}
        assert ctx.store.threads[1].deleted_at is not None
        assert all(m.deleted_at is not None for m in ctx.store.memberships)

    async def test_admin_cannot_dissolve(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)

        with pytest.raises(ForbiddenError):
            await ctx.svc.dissolve(actor_id=5, thread_id=1)
        assert ctx.store.threads[1].deleted_at is None

    async def test_member_cannot_dissolve(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.dissolve(actor_id=5, thread_id=1)


# ---------------------------------------------------------------------------
# add_member — humans
# ---------------------------------------------------------------------------


class TestAddMemberHuman:
    async def test_admin_adds_human_as_member(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)

        result = await ctx.svc.add_member(actor_id=5, thread_id=1, user_id=42)

        assert result["added"] is True
        added = next(m for m in ctx.store.memberships if m.user_id == 42)
        assert added.role == ThreadMemberRole.MEMBER

    async def test_owner_adds_human(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        result = await ctx.svc.add_member(actor_id=1, thread_id=1, user_id=42)

        assert result["added"] is True

    async def test_plain_member_cannot_add(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.add_member(actor_id=5, thread_id=1, user_id=42)


# ---------------------------------------------------------------------------
# add_member — agent consent flow
# ---------------------------------------------------------------------------


class TestAddMemberAgentConsent:
    async def test_agent_owned_by_other_mints_invitation(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.OWNER)
        # user 9 is an agent owned by user 77 (someone other than the inviter)
        ctx.store.agents[9] = 77

        result = await ctx.svc.add_member(actor_id=5, thread_id=1, user_id=9)

        assert result["pending"] is True
        assert "application_id" in result
        # NOT added as a member
        assert not any(m.user_id == 9 for m in ctx.store.memberships)
        # An INVITATION application was minted, addressed to the agent's owner
        assert len(ctx.store.applications) == 1
        app = ctx.store.applications[0]
        assert app.type == ApplicationType.INVITATION.value
        assert app.approver_id == 77
        # the owner was notified
        ctx.publish.assert_awaited_once()

    async def test_inviter_is_agent_owner_auto_adds(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.OWNER)
        # user 9 is an agent whose owner IS the inviter (user 5)
        ctx.store.agents[9] = 5

        result = await ctx.svc.add_member(actor_id=5, thread_id=1, user_id=9)

        assert result["added"] is True
        assert any(m.user_id == 9 and m.deleted_at is None for m in ctx.store.memberships)
        # no application, no consent notification
        assert ctx.store.applications == []
        ctx.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# remove_member (kick)
# ---------------------------------------------------------------------------


class TestRemoveMember:
    async def test_admin_can_kick_member(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)
        ctx.store.add_membership(1, 42, ThreadMemberRole.MEMBER)

        result = await ctx.svc.remove_member(actor_id=5, thread_id=1, user_id=42)

        assert result == {"removed": True}
        kicked = next(m for m in ctx.store.memberships if m.user_id == 42)
        assert kicked.deleted_at is not None

    async def test_owner_can_kick_member(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        ctx.store.add_membership(1, 42, ThreadMemberRole.MEMBER)

        await ctx.svc.remove_member(actor_id=1, thread_id=1, user_id=42)

        kicked = next(m for m in ctx.store.memberships if m.user_id == 42)
        assert kicked.deleted_at is not None

    async def test_member_cannot_kick_others(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        ctx.store.add_membership(1, 42, ThreadMemberRole.MEMBER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.remove_member(actor_id=5, thread_id=1, user_id=42)
        assert next(m for m in ctx.store.memberships if m.user_id == 42).deleted_at is None

    async def test_cannot_kick_owner(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.remove_member(actor_id=5, thread_id=1, user_id=1)


# ---------------------------------------------------------------------------
# change_role
# ---------------------------------------------------------------------------


class TestChangeRole:
    async def test_owner_can_promote_member_to_admin(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        ctx.store.add_membership(1, 42, ThreadMemberRole.MEMBER)

        result = await ctx.svc.change_role(
            actor_id=1, thread_id=1, user_id=42, role=ThreadMemberRole.ADMIN
        )

        member = result["member"]
        assert isinstance(member, dict)
        assert member["role"] == ThreadMemberRole.ADMIN
        target = next(m for m in ctx.store.memberships if m.user_id == 42)
        assert target.role == ThreadMemberRole.ADMIN

    async def test_owner_can_demote_admin_to_member(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        ctx.store.add_membership(1, 42, ThreadMemberRole.ADMIN)

        await ctx.svc.change_role(
            actor_id=1, thread_id=1, user_id=42, role=ThreadMemberRole.MEMBER
        )

        target = next(m for m in ctx.store.memberships if m.user_id == 42)
        assert target.role == ThreadMemberRole.MEMBER

    async def test_admin_cannot_change_roles(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)
        ctx.store.add_membership(1, 42, ThreadMemberRole.MEMBER)

        with pytest.raises(ForbiddenError):
            await ctx.svc.change_role(
                actor_id=5, thread_id=1, user_id=42, role=ThreadMemberRole.ADMIN
            )

    async def test_invalid_role_rejected(self, ctx: _Ctx) -> None:
        with pytest.raises(BadRequestError):
            await ctx.svc.change_role(
                actor_id=1, thread_id=1, user_id=42, role=ThreadMemberRole.OWNER
            )

    async def test_target_not_a_member_raises_not_found(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        with pytest.raises(NotFoundError):
            await ctx.svc.change_role(
                actor_id=1, thread_id=1, user_id=999, role=ThreadMemberRole.ADMIN
            )


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def _scalar_result(values: list[int]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value = list(values)
    return result


def _all_result(rows: list[tuple[int, str]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


class TestCandidates:
    async def test_returns_human_search_results_excluding_members(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)  # actor
        ctx.store.add_membership(1, 2, ThreadMemberRole.MEMBER)  # existing member

        # session.execute is called: human search, device query, agent-screen query.
        ctx.session.execute = AsyncMock(
            side_effect=[
                _scalar_result([2, 3, 4]),  # humans matching q (includes existing member 2)
                _scalar_result([]),  # actor's devices
                _all_result([]),  # agent screens
            ]
        )

        result = await ctx.svc.candidates(actor_id=1, thread_id=1, q="user")

        candidates = result["candidates"]
        assert isinstance(candidates, list)
        ids = {c["user_id"] for c in candidates}
        # existing member (2) and actor (1) excluded; only 3 and 4 remain
        assert ids == {3, 4}

    async def test_non_member_cannot_list_candidates(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)

        with pytest.raises(ForbiddenError):
            await ctx.svc.candidates(actor_id=99, thread_id=1, q="user")


# ---------------------------------------------------------------------------
# post_message — 引用消息 (reply_to_id + quoted preview)
# ---------------------------------------------------------------------------


class TestPostMessageReply:
    async def test_reply_to_id_persisted(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        original = ctx.store.add_block(thread_id=1, author_id=1, content="hello there")

        await ctx.svc.post_message(
            actor_id=1, thread_id=1, text="a reply", reply_to_id=original.id
        )

        reply = next(b for b in ctx.store.blocks if b.content == "a reply")
        assert reply.reply_to_id == original.id

    async def test_returned_message_carries_quoted_preview(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        original = ctx.store.add_block(thread_id=1, author_id=7, content="the original text")

        result = await ctx.svc.post_message(
            actor_id=1, thread_id=1, text="a reply", reply_to_id=original.id
        )

        message = result["message"]
        assert isinstance(message, dict)
        assert message["reply_to_id"] == original.id
        quoted = message["quoted"]
        assert isinstance(quoted, dict)
        assert quoted["id"] == original.id
        assert quoted["author_id"] == 7
        assert quoted["excerpt"] == "the original text"
        assert quoted["deleted"] is False

    async def test_long_excerpt_is_truncated(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        original = ctx.store.add_block(thread_id=1, author_id=1, content="x" * 300)

        result = await ctx.svc.post_message(
            actor_id=1, thread_id=1, text="r", reply_to_id=original.id
        )

        message = result["message"]
        assert isinstance(message, dict)
        quoted = message["quoted"]
        assert isinstance(quoted, dict)
        excerpt = quoted["excerpt"]
        assert isinstance(excerpt, str)
        assert len(excerpt) == 141  # 140 chars + ellipsis
        assert excerpt.endswith("…")

    async def test_reply_to_missing_block_marked_deleted(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        result = await ctx.svc.post_message(
            actor_id=1, thread_id=1, text="a reply", reply_to_id=999999
        )

        message = result["message"]
        assert isinstance(message, dict)
        quoted = message["quoted"]
        assert isinstance(quoted, dict)
        assert quoted["deleted"] is True
        assert quoted["author_id"] is None
        assert quoted["excerpt"] is None

    async def test_reply_to_block_in_another_thread_marked_deleted(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_thread(2)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)
        # block lives in thread 2; replying from thread 1 must not leak it
        foreign = ctx.store.add_block(thread_id=2, author_id=5, content="other thread")

        result = await ctx.svc.post_message(
            actor_id=1, thread_id=1, text="a reply", reply_to_id=foreign.id
        )

        message = result["message"]
        assert isinstance(message, dict)
        quoted = message["quoted"]
        assert isinstance(quoted, dict)
        assert quoted["deleted"] is True

    async def test_plain_message_has_no_quoted(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 1, ThreadMemberRole.OWNER)

        result = await ctx.svc.post_message(actor_id=1, thread_id=1, text="hi")

        message = result["message"]
        assert isinstance(message, dict)
        assert message["reply_to_id"] is None
        assert "quoted" not in message


# ---------------------------------------------------------------------------
# delete_message — 删除 (soft delete / tombstone)
# ---------------------------------------------------------------------------


class TestDeleteMessage:
    async def test_author_can_delete(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        msg = ctx.store.add_block(thread_id=1, author_id=5, content="mine")

        result = await ctx.svc.delete_message(actor_id=5, thread_id=1, block_id=msg.id)

        assert msg.deleted_at is not None
        message = result["message"]
        assert isinstance(message, dict)
        assert message["deleted"] is True
        assert message["text"] == ""

    async def test_admin_can_delete_others_message(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.ADMIN)
        ctx.store.add_membership(1, 6, ThreadMemberRole.MEMBER)
        msg = ctx.store.add_block(thread_id=1, author_id=6, content="theirs")

        await ctx.svc.delete_message(actor_id=5, thread_id=1, block_id=msg.id)

        assert msg.deleted_at is not None

    async def test_non_author_non_admin_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        ctx.store.add_membership(1, 6, ThreadMemberRole.MEMBER)
        msg = ctx.store.add_block(thread_id=1, author_id=6, content="theirs")

        with pytest.raises(ForbiddenError):
            await ctx.svc.delete_message(actor_id=5, thread_id=1, block_id=msg.id)
        assert msg.deleted_at is None

    async def test_deleted_message_still_listed_as_tombstone(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        original = ctx.store.add_block(thread_id=1, author_id=5, content="first")
        msg = ctx.store.add_block(
            thread_id=1, author_id=5, content="reply", reply_to_id=original.id
        )

        await ctx.svc.delete_message(actor_id=5, thread_id=1, block_id=msg.id)
        result = await ctx.svc.list_messages(actor_id=5, thread_id=1)

        messages = result["messages"]
        assert isinstance(messages, list)
        tombstone = next(m for m in messages if m["id"] == msg.id)
        assert tombstone["deleted"] is True
        assert tombstone["text"] == ""
        # a deleted message drops its quoted preview
        assert "quoted" not in tombstone


# ---------------------------------------------------------------------------
# pin / unpin / list_pins — 置顶
# ---------------------------------------------------------------------------


class TestPinMessage:
    async def test_member_can_pin_and_unpin(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        msg = ctx.store.add_block(thread_id=1, author_id=5, content="hi")

        pinned = await ctx.svc.pin_message(actor_id=5, thread_id=1, block_id=msg.id)
        assert msg.pinned_at is not None
        message = pinned["message"]
        assert isinstance(message, dict)
        assert message["pinned"] is True

        await ctx.svc.unpin_message(actor_id=5, thread_id=1, block_id=msg.id)
        assert msg.pinned_at is None

    async def test_non_member_cannot_pin(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        msg = ctx.store.add_block(thread_id=1, author_id=5, content="hi")

        with pytest.raises(ForbiddenError):
            await ctx.svc.pin_message(actor_id=99, thread_id=1, block_id=msg.id)

    async def test_list_pins_returns_only_pinned(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        a = ctx.store.add_block(thread_id=1, author_id=5, content="a")
        ctx.store.add_block(thread_id=1, author_id=5, content="b")

        await ctx.svc.pin_message(actor_id=5, thread_id=1, block_id=a.id)
        result = await ctx.svc.list_pins(actor_id=5, thread_id=1)

        messages = result["messages"]
        assert isinstance(messages, list)
        ids = {m["id"] for m in messages}
        assert ids == {a.id}


# ---------------------------------------------------------------------------
# forward_message — 转发
# ---------------------------------------------------------------------------


class TestForwardMessage:
    async def test_forward_copies_text_with_provenance(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_thread(2)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        ctx.store.add_membership(2, 5, ThreadMemberRole.MEMBER)
        src = ctx.store.add_block(thread_id=1, author_id=9, content="please forward me")

        result = await ctx.svc.forward_message(
            actor_id=5, from_thread_id=1, block_id=src.id, to_thread_id=2
        )

        message = result["message"]
        assert isinstance(message, dict)
        assert message["thread_id"] == 2
        assert message["author_id"] == 5
        assert message["text"] == "please forward me"
        created = next(b for b in ctx.store.blocks if b.id == message["id"])
        assert created.refs == [f"forwarded_from:1:{src.id}"]

    async def test_forward_requires_membership_in_source(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_thread(2)
        ctx.store.add_membership(2, 5, ThreadMemberRole.MEMBER)
        src = ctx.store.add_block(thread_id=1, author_id=9, content="x")

        with pytest.raises(ForbiddenError):
            await ctx.svc.forward_message(
                actor_id=5, from_thread_id=1, block_id=src.id, to_thread_id=2
            )

    async def test_forward_requires_membership_in_target(self, ctx: _Ctx) -> None:
        ctx.store.add_thread(1)
        ctx.store.add_thread(2)
        ctx.store.add_membership(1, 5, ThreadMemberRole.MEMBER)
        src = ctx.store.add_block(thread_id=1, author_id=9, content="x")

        with pytest.raises(ForbiddenError):
            await ctx.svc.forward_message(
                actor_id=5, from_thread_id=1, block_id=src.id, to_thread_id=2
            )


class TestPostMessageForwarding:
    async def test_excludes_author_from_forward(self, ctx: _Ctx) -> None:
        """An agent's own message is never forwarded back to itself — otherwise post-note
        (which runs this same pipeline) would self-notify in an infinite loop at 立即."""
        author, other = 9001, 9002
        ctx.store.agents[author] = 1  # both members are agents (owner id present)
        ctx.store.agents[other] = 1
        ctx.store.add_thread(50)
        ctx.store.add_membership(50, author, ThreadMemberRole.MEMBER)
        ctx.store.add_membership(50, other, ThreadMemberRole.MEMBER)

        await ctx.svc.post_message(actor_id=author, thread_id=50, text="hi from author")

        forward = ctx.svc._agents.forward_message_to_thread_agents
        forward.assert_awaited_once()
        policies = forward.await_args.kwargs["agent_policies"]
        assert {p.user_id for p in policies} == {other}  # author excluded, other included
