"""Who is in a 题目版, and who is waiting to be let in.

Split out of ``app/domain/space/repositories.py`` so that module keeps one
reason to change: a membership row and a request to join describe the same
fact — how someone comes to be on the board — and the space domain's other
writes never touch either. Both classes are re-exported from ``repositories``,
so ``from app.domain.space.repositories import SpaceMemberRepository`` keeps
resolving.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import Space, SpaceJoinRequest, SpaceMember

#: A request still waiting for an owner or admin.
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"


class SpaceMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_member(self, space_id: int, user_id: int) -> SpaceMember | None:
        stmt: Select[tuple[SpaceMember]] = select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(self, space_id: int) -> Sequence[SpaceMember]:
        stmt: Select[tuple[SpaceMember]] = (
            select(SpaceMember)
            .where(
                SpaceMember.space_id == space_id,
                SpaceMember.deleted_at.is_(None),
            )
            .order_by(SpaceMember.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add_member(
        self, *, space_id: int, user_id: int
    ) -> tuple[SpaceMember, bool]:
        """Join, or re-join after having been removed. Returns (row, created).

        A removed member keeps a soft-deleted row, which is why this revives
        it instead of inserting a second one — otherwise the same person
        would accumulate a row per attempt and `list_members` would have to
        know to deduplicate.

        ``created`` answers "was this call the one that let them in".

        Both halves are written so the database, not a preceding read, decides
        that. Two requests for the same person do arrive together — a double
        tap, or an admin adding someone while they join by the link — and a
        read-then-write pair lets both read "not a member" and both write:

        * Nothing there yet: they race on the insert and
          ``uq_space_member_active`` lets exactly one through. The loser's
          ``IntegrityError`` is the answer to the race rather than a failure,
          so it re-reads and returns the row the winner wrote. The insert
          runs inside a savepoint, which is what leaves the session usable.
        * A removed row: the revive is one conditional UPDATE, and only a
          ``rowcount`` of 1 means this call is the one that un-deleted it.
        """
        now = datetime.now(UTC)
        member = await self._get_any_member(space_id, user_id)

        if member is None:
            member = SpaceMember(
                space_id=space_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(member)
            try:
                async with self._session.begin_nested():
                    await self._session.flush()
            except IntegrityError:
                existing = await self._get_any_member(space_id, user_id)
                if existing is None:
                    # Not this race — a constraint we know nothing about.
                    # Better a 500 than a membership that only looks granted.
                    raise
                return existing, False
            return member, True

        stmt = (
            update(SpaceMember)
            .where(
                SpaceMember.id == member.id,
                SpaceMember.deleted_at.is_not(None),
            )
            .values(deleted_at=None, updated_at=now)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        created = (result.rowcount or 0) > 0  # type: ignore[attr-defined]
        # Refreshed either way: on the created path to load what the UPDATE
        # wrote, and otherwise because the row this session already holds may
        # predate another request's revive and would be returned still marked
        # deleted.
        await self._session.refresh(member)
        return member, created

    async def remove_member(self, member: SpaceMember) -> None:
        member.deleted_at = member.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def _get_any_member(self, space_id: int, user_id: int) -> SpaceMember | None:
        """Including soft-deleted rows — the caller decides what to do with one."""
        stmt: Select[tuple[SpaceMember]] = (
            select(SpaceMember)
            .where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == user_id,
            )
            .order_by(SpaceMember.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SpaceJoinRepository:
    """A space's join link, and the requests it produces while it asks for
    approval."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def space_by_token(self, token: str) -> Space | None:
        """The live space this link names; deleted spaces answer None."""
        stmt = select(Space).where(
            Space.join_token == token, Space.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def pending_for(self, space_id: int, user_id: int) -> SpaceJoinRequest | None:
        stmt = select(SpaceJoinRequest).where(
            SpaceJoinRequest.space_id == space_id,
            SpaceJoinRequest.user_id == user_id,
            SpaceJoinRequest.status == PENDING,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ask(self, *, space_id: int, user_id: int) -> SpaceJoinRequest:
        """File a request, or return the one already waiting.

        ``uq_space_join_request_pending`` decides a double click: the loser's
        insert fails inside a savepoint and it returns the winner's row.
        """
        request = SpaceJoinRequest(
            space_id=space_id,
            user_id=user_id,
            status=PENDING,
            created_at=datetime.now(UTC),
        )
        self._session.add(request)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            existing = await self.pending_for(space_id, user_id)
            if existing is None:
                raise
            return existing
        return request

    async def list_pending(self, space_id: int) -> Sequence[SpaceJoinRequest]:
        stmt = (
            select(SpaceJoinRequest)
            .where(
                SpaceJoinRequest.space_id == space_id,
                SpaceJoinRequest.status == PENDING,
            )
            .order_by(SpaceJoinRequest.created_at.asc(), SpaceJoinRequest.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def decide(
        self, *, space_id: int, request_id: int, status: str, decided_by: int
    ) -> SpaceJoinRequest | None:
        """Answer a waiting request; ``None`` when there is none by that id.

        One conditional UPDATE, so two managers answering together cannot
        both win: the second finds nothing pending.
        """
        stmt = (
            update(SpaceJoinRequest)
            .where(
                SpaceJoinRequest.id == request_id,
                SpaceJoinRequest.space_id == space_id,
                SpaceJoinRequest.status == PENDING,
            )
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
            .returning(SpaceJoinRequest)
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
