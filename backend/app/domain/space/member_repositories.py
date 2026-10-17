"""Who is in a 题目版, and the codes that let people in.

Split out of ``app/domain/space/repositories.py`` so that module keeps one
reason to change: a membership row and the invite code behind it describe the
same fact — how someone came to be on the board — and the space domain's other
writes never touch either. Both classes are re-exported from ``repositories``,
so ``from app.domain.space.repositories import SpaceMemberRepository`` keeps
resolving.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceInviteCode, SpaceMember


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
        self, *, space_id: int, user_id: int, invite_code_id: int | None = None
    ) -> tuple[SpaceMember, bool]:
        """Join, or re-join after having been removed. Returns (row, created).

        A removed member keeps a soft-deleted row, which is why this revives
        it instead of inserting a second one — otherwise the same person
        would accumulate a row per attempt and `list_members` would have to
        know to deduplicate.

        ``created`` answers "was this call the one that let them in". Only
        that one consumes an invite-code use, so a double-tapped「加入」does
        not spend two.

        ``invite_code_id`` is the code this membership came from, and it is
        written on **both** paths — the insert and the revive — because the
        column describes the membership that is live now, not the first one
        this person ever had. Somebody who left and was later added back by
        the owner has no code behind their current row, and leaving the old
        code's name on it would report the wrong thing for good. ``None``
        therefore means what it says on the read side.

        Both halves are written so the database, not a preceding read, decides
        that. Two requests for the same person do arrive together — a double
        tap, or an admin adding someone while they redeem a code — and a
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
                invite_code_id=invite_code_id,
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
            .values(deleted_at=None, updated_at=now, invite_code_id=invite_code_id)
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


class SpaceInviteCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_code(
        self,
        *,
        space_id: int,
        code: str,
        max_uses: int,
        expires_at: datetime | None,
        created_by: int | None,
        note: str | None = None,
    ) -> SpaceInviteCode:
        now = datetime.now(UTC)
        invite = SpaceInviteCode(
            space_id=space_id,
            code=code,
            max_uses=max_uses,
            use_count=0,
            expires_at=expires_at,
            created_by=created_by,
            note=note,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def list_codes_for_space(self, space_id: int) -> Sequence[SpaceInviteCode]:
        stmt: Select[tuple[SpaceInviteCode]] = (
            select(SpaceInviteCode)
            .where(
                SpaceInviteCode.space_id == space_id,
                SpaceInviteCode.deleted_at.is_(None),
            )
            .order_by(SpaceInviteCode.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_code(self, code: str) -> SpaceInviteCode | None:
        stmt: Select[tuple[SpaceInviteCode]] = select(SpaceInviteCode).where(
            SpaceInviteCode.code == code,
            SpaceInviteCode.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, code_ids: Sequence[int]) -> dict[int, SpaceInviteCode]:
        """Several codes at once, by id, **revoked ones included**.

        Not scoped by space, unlike ``get_for_space``: the callers here start
        from a member row whose ``invite_code_id`` the server itself wrote, so
        the id is not something a client got to choose.

        ``deleted_at`` is deliberately not filtered. A revoked code is the case
        the 加入方式 column exists for — the question it answers is "which code
        let this person in", and revoking a code afterwards does not change the
        answer. An empty list is answered without a query, because SQLAlchemy
        warns about ``IN ()`` and this path runs on every roster read.
        """
        ids = list(code_ids)
        if not ids:
            return {}
        stmt: Select[tuple[SpaceInviteCode]] = select(SpaceInviteCode).where(
            SpaceInviteCode.id.in_(ids)
        )
        result = await self._session.execute(stmt)
        return {row.id: row for row in result.scalars().all()}

    async def code_exists(self, code: str) -> bool:
        stmt = select(SpaceInviteCode.id).where(SpaceInviteCode.code == code)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_for_space(
        self, *, space_id: int, code_id: int
    ) -> SpaceInviteCode | None:
        """One live code of this space, by id.

        Scoped by space on purpose: knowing an id must not reach another
        board's code. Revoked ones are absent too — a code that is out of
        circulation is not something to edit back into it.
        """
        stmt: Select[tuple[SpaceInviteCode]] = select(SpaceInviteCode).where(
            SpaceInviteCode.id == code_id,
            SpaceInviteCode.space_id == space_id,
            SpaceInviteCode.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, invite: SpaceInviteCode) -> SpaceInviteCode:
        invite.updated_at = datetime.now(UTC)
        await self._session.flush()
        return invite

    async def revoke(self, invite: SpaceInviteCode) -> None:
        """Take a code out of circulation by soft delete.

        Soft rather than a DELETE because ``consume_use`` already refuses a
        row whose ``deleted_at`` is set — so this one write is the whole of
        the revocation, with no second place to keep in step. The row stays
        as the record of what was handed out.
        """
        now = datetime.now(UTC)
        invite.deleted_at = now
        invite.updated_at = now
        await self._session.flush()

    async def consume_use(self, code_id: int) -> bool:
        """Spend one use, only if there is one left and the code has not expired.

        The limit is enforced inside the UPDATE rather than by reading the
        row first: two people redeeming the last use at the same moment both
        read "0 of 1" and both get in otherwise. Returns False when the
        UPDATE matched nothing — exhausted, deleted, or expired.

        Expiry belongs in the same predicate for the same reason the count
        does: `join_space` reads the row before calling this, so a code that
        expires in between would otherwise still be spent. Checking it here,
        in the statement that decides, is what makes the refusal real rather
        than a property of how far apart two reads happened to be.
        """
        stmt = (
            update(SpaceInviteCode)
            .where(
                SpaceInviteCode.id == code_id,
                SpaceInviteCode.deleted_at.is_(None),
                SpaceInviteCode.use_count < SpaceInviteCode.max_uses,
                or_(
                    SpaceInviteCode.expires_at.is_(None),
                    SpaceInviteCode.expires_at > datetime.now(UTC),
                ),
            )
            .values(
                use_count=SpaceInviteCode.use_count + 1,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
