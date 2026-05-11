import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invite.models import InviteCode


class InviteCodeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate_code(self, code: str) -> InviteCode:
        """Validate an invite code. Returns the InviteCode or raises ValueError."""
        result = await self._session.execute(select(InviteCode).where(InviteCode.code == code))
        invite = result.scalar_one_or_none()
        if invite is None:
            raise ValueError("INVALID_CODE")
        if not invite.is_active:
            raise ValueError("CODE_DISABLED")
        if invite.expires_at and invite.expires_at < datetime.now(UTC):
            raise ValueError("CODE_EXPIRED")
        if invite.use_count >= invite.max_uses:
            raise ValueError("CODE_EXHAUSTED")
        return invite

    async def consume_code(self, code: str) -> None:
        """Increment usage count after successful registration."""
        result = await self._session.execute(select(InviteCode).where(InviteCode.code == code))
        invite = result.scalar_one_or_none()
        if invite:
            invite.use_count += 1

    async def create_code(
        self,
        *,
        max_uses: int = 1,
        created_by: int | None = None,
        note: str | None = None,
        expires_at: datetime | None = None,
    ) -> InviteCode:
        """Generate a new invite code."""
        code = secrets.token_urlsafe(12)
        invite = InviteCode(
            code=code,
            max_uses=max_uses,
            created_by=created_by,
            note=note,
            expires_at=expires_at,
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def list_codes(self, *, active_only: bool = False) -> list[InviteCode]:
        """List all invite codes."""
        stmt = select(InviteCode).order_by(InviteCode.created_at.desc())
        if active_only:
            stmt = stmt.where(InviteCode.is_active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate_code(self, code_id: int) -> None:
        """Deactivate an invite code."""
        result = await self._session.execute(select(InviteCode).where(InviteCode.id == code_id))
        invite = result.scalar_one_or_none()
        if invite:
            invite.is_active = False
