"""Unit tests for the chat ``ReactionService`` (behavioural, no database).

The service builds its repositories from a ``session_factory``; we replace only its
collaborators with in-memory fakes over a shared ``_Store`` and assert *behaviour* —
idempotent react, unreact, member authorization, and the Feishu-style grouped shape.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.reaction.services import ReactionService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Store:
    # (block_id, user_id, emoji)
    reactions: list[SimpleNamespace] = field(default_factory=list)
    members: set[tuple[int, int]] = field(default_factory=set)  # (thread_id, user_id)
    messages: set[tuple[int, int]] = field(default_factory=set)  # (thread_id, block_id)
    _seq: int = 0

    def add_member(self, thread_id: int, user_id: int) -> None:
        self.members.add((thread_id, user_id))

    def add_message(self, thread_id: int, block_id: int) -> None:
        self.messages.add((thread_id, block_id))


class _FakeReactionRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def _find(self, block_id: int, user_id: int, emoji: str) -> SimpleNamespace | None:
        return next(
            (
                r
                for r in self._s.reactions
                if r.block_id == block_id and r.user_id == user_id and r.emoji == emoji
            ),
            None,
        )

    async def add(self, block_id: int, user_id: int, emoji: str) -> SimpleNamespace:
        existing = self._find(block_id, user_id, emoji)
        if existing is not None:
            return existing
        self._s._seq += 1
        row = SimpleNamespace(
            id=self._s._seq, block_id=block_id, user_id=user_id, emoji=emoji
        )
        self._s.reactions.append(row)
        return row

    async def remove(self, block_id: int, user_id: int, emoji: str) -> bool:
        existing = self._find(block_id, user_id, emoji)
        if existing is None:
            return False
        self._s.reactions.remove(existing)
        return True

    async def list_for_blocks(self, block_ids: list[int]) -> list[SimpleNamespace]:
        return [r for r in self._s.reactions if r.block_id in set(block_ids)]

    async def list_for_block(self, block_id: int) -> list[SimpleNamespace]:
        return [r for r in self._s.reactions if r.block_id == block_id]


class _FakeMembershipRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    async def is_member(self, thread_id: int, user_id: int) -> bool:
        return (thread_id, user_id) in self._s.members


class _FakeBlockRepo:
    def __init__(self, store: _Store) -> None:
        self._s = store

    async def get_message_in_thread(
        self, thread_id: int, block_id: int
    ) -> SimpleNamespace | None:
        if (thread_id, block_id) in self._s.messages:
            return SimpleNamespace(id=block_id, thread_id=thread_id)
        return None


@dataclass
class _Ctx:
    svc: ReactionService
    store: _Store


def _session_factory() -> Callable[[], object]:
    class _SessionCtx:
        async def __aenter__(self) -> MagicMock:
            session = MagicMock()

            async def _commit() -> None:
                return None

            session.commit = _commit
            return session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    return lambda: _SessionCtx()


@pytest.fixture
def ctx() -> Iterator[_Ctx]:
    store = _Store()
    svc = ReactionService(_session_factory())  # type: ignore[arg-type]
    mod = "app.domain.reaction.services"
    with ExitStack() as es:
        es.enter_context(patch(f"{mod}.ReactionRepository", lambda _s: _FakeReactionRepo(store)))
        es.enter_context(
            patch(f"{mod}.ThreadMembershipRepository", lambda _s: _FakeMembershipRepo(store))
        )
        es.enter_context(patch(f"{mod}.BlockRepository", lambda _s: _FakeBlockRepo(store)))
        yield _Ctx(svc=svc, store=store)


class TestReact:
    async def test_react_adds(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)
        ctx.store.add_message(1, 800)

        result = await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")

        assert result == {"ok": True}
        assert len(ctx.store.reactions) == 1
        assert ctx.store.reactions[0].emoji == "👍"

    async def test_react_is_idempotent(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)
        ctx.store.add_message(1, 800)

        await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")
        await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")

        assert len(ctx.store.reactions) == 1

    async def test_non_member_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_message(1, 800)

        with pytest.raises(ForbiddenError):
            await ctx.svc.react(actor_id=99, thread_id=1, block_id=800, emoji="👍")
        assert ctx.store.reactions == []

    async def test_unknown_message_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)

        with pytest.raises(NotFoundError):
            await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")


class TestUnreact:
    async def test_unreact_removes(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)
        ctx.store.add_message(1, 800)
        await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")

        result = await ctx.svc.unreact(actor_id=5, thread_id=1, block_id=800, emoji="👍")

        assert result == {"ok": True}
        assert ctx.store.reactions == []

    async def test_unreact_absent_is_noop(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)
        ctx.store.add_message(1, 800)

        result = await ctx.svc.unreact(actor_id=5, thread_id=1, block_id=800, emoji="👍")

        assert result == {"ok": True}

    async def test_non_member_rejected(self, ctx: _Ctx) -> None:
        ctx.store.add_message(1, 800)

        with pytest.raises(ForbiddenError):
            await ctx.svc.unreact(actor_id=99, thread_id=1, block_id=800, emoji="👍")


class TestGrouping:
    async def test_grouping_count_and_me_shape(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)
        ctx.store.add_member(1, 6)
        ctx.store.add_message(1, 800)
        ctx.store.add_message(1, 801)
        # 👍 from users 5 and 6 on block 800; ❤ from user 6 on block 800; 👍 on 801 by 6
        await ctx.svc.react(actor_id=5, thread_id=1, block_id=800, emoji="👍")
        await ctx.svc.react(actor_id=6, thread_id=1, block_id=800, emoji="👍")
        await ctx.svc.react(actor_id=6, thread_id=1, block_id=800, emoji="❤")
        await ctx.svc.react(actor_id=6, thread_id=1, block_id=801, emoji="👍")

        result = await ctx.svc.reactions_for_thread(actor_id=5, thread_id=1, block_ids=[800, 801])

        reactions = result["reactions"]
        assert isinstance(reactions, dict)
        chips_800 = reactions["800"]
        thumbs = next(c for c in chips_800 if c["emoji"] == "👍")
        assert thumbs["count"] == 2
        assert thumbs["userIds"] == [5, 6]
        assert thumbs["me"] is True
        heart = next(c for c in chips_800 if c["emoji"] == "❤")
        assert heart["count"] == 1
        assert heart["me"] is False  # actor 5 did not react with ❤
        # block 801: 👍 by user 6 only; actor 5 not in it
        thumbs_801 = reactions["801"][0]
        assert thumbs_801["me"] is False

    async def test_blocks_without_reactions_omitted(self, ctx: _Ctx) -> None:
        ctx.store.add_member(1, 5)

        result = await ctx.svc.reactions_for_thread(actor_id=5, thread_id=1, block_ids=[800])

        assert result == {"reactions": {}}

    async def test_non_member_rejected(self, ctx: _Ctx) -> None:
        with pytest.raises(ForbiddenError):
            await ctx.svc.reactions_for_thread(actor_id=99, thread_id=1, block_ids=[800])
