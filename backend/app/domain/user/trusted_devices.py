"""Browsers trusted to stand in for two-step verification.

After passing 2FA, the owner may ask not to be asked again on that browser
for 30 days. The browser then holds a random token in an HttpOnly cookie, and
a later sign-in to the same account from it skips the second step. The first
step is never skipped: a trust only means "this browser already proved the
second factor recently".

A trust belongs to one account. A cookie presented for another account is
simply not found, so one browser trusts at most the account it was last
granted for. It ends when it expires, never extended by use; when the
password is changed or reset; when 2FA is turned off or set up again; and
when the device it was last used on is signed out from the device list.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.models import UserTrustedDevice

TRUST_DAYS = 30


@dataclass(frozen=True)
class Granted:
    token: str
    expires_at: datetime


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _live(now: datetime) -> tuple:
    return (
        UserTrustedDevice.revoked_at.is_(None),
        UserTrustedDevice.expires_at > now,
    )


class TrustedDeviceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def grant(
        self,
        user_id: int,
        session_id: uuid.UUID,
        *,
        user_agent: str,
        replacing: str | None = None,
    ) -> Granted:
        """Trust the browser behind ``session_id`` for this user.

        ``replacing`` is the trust cookie the browser already holds, if any.
        The new cookie overwrites it, so the trust it named can no longer be
        presented and is ended here rather than left listed as live.
        """
        now = _now()
        if replacing:
            await self._db.execute(
                update(UserTrustedDevice)
                .where(
                    UserTrustedDevice.token_hash == _digest(replacing),
                    UserTrustedDevice.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        token = secrets.token_urlsafe(32)
        row = UserTrustedDevice(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=_digest(token),
            expires_at=now + timedelta(days=TRUST_DAYS),
            last_used_at=now,
            session_id=session_id,
            user_agent=user_agent[:1024],
        )
        self._db.add(row)
        await self._db.flush()
        return Granted(token, row.expires_at)

    async def find(self, token: str | None, user_id: int) -> UserTrustedDevice | None:
        """The live trust ``token`` names for this user, if there is one."""
        if not token:
            return None
        return await self._db.scalar(
            select(UserTrustedDevice).where(
                UserTrustedDevice.token_hash == _digest(token),
                UserTrustedDevice.user_id == user_id,
                *_live(_now()),
            )
        )

    async def used(self, trust: UserTrustedDevice, session_id: uuid.UUID) -> None:
        """Record a sign-in made with the trust. Its expiry does not move."""
        trust.last_used_at = _now()
        trust.session_id = session_id
        await self._db.flush()

    async def revoke_all(
        self, user_id: int, *, keep_session: uuid.UUID | None = None
    ) -> None:
        """End every live trust of the user, except the one last used with
        ``keep_session``."""
        now = _now()
        query = update(UserTrustedDevice).where(
            UserTrustedDevice.user_id == user_id, *_live(now)
        )
        if keep_session is not None:
            query = query.where(
                UserTrustedDevice.session_id.is_distinct_from(keep_session)
            )
        await self._db.execute(query.values(revoked_at=now))

    async def revoke_for_session(self, user_id: int, session_id: uuid.UUID) -> None:
        """End the trust last used with this sign-in."""
        now = _now()
        await self._db.execute(
            update(UserTrustedDevice)
            .where(
                UserTrustedDevice.user_id == user_id,
                UserTrustedDevice.session_id == session_id,
                *_live(now),
            )
            .values(revoked_at=now)
        )

    async def trusted_sessions(self, user_id: int) -> set[uuid.UUID]:
        """The sign-ins whose browser holds a live trust."""
        rows = await self._db.scalars(
            select(UserTrustedDevice.session_id).where(
                UserTrustedDevice.user_id == user_id,
                UserTrustedDevice.session_id.is_not(None),
                *_live(_now()),
            )
        )
        return {row for row in rows if row is not None}
