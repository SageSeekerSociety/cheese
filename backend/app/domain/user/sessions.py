"""Sign-in sessions: refresh is stateful, access is not.

A session is one sign-in. It holds a rotating refresh token, of which only a
SHA-256 digest is stored; the token is 256 random bits, so a plain digest is
as good as a keyed one and lets the lookup be an index scan. Access tokens are
short-lived JWTs verified by signature alone. They name their session (the
``sid`` claim) so a request can tell which entry is "this device", never so
the session is looked up per request: an access token already issued stays
valid for the rest of its lifetime after its session is revoked.

A session ends on sign-out, on revocation from the device list, when the
password is changed or reset, when it has gone unused past the idle timeout,
when its absolute lifetime runs out, and when two parties are seen holding it.

That last one is RFC 9700 §4.14.2's detection of a stolen refresh token, and
it has to be told apart from a browser that simply never heard the answer to
its last refresh. The server rotates and commits; the answer is lost to a
client-side timeout, a dropped connection or a proxy error; the browser still
holds the token that was rotated away and presents it at its next refresh.
Treating that as theft ended about a quarter of all sign-ins on a slow day.
So the rotated-away token, presented after the grace window, gets a fresh
successor, and the one it never received is kept as ``abandoned_hash``. The
successor cannot have been used, or ``previous_hash`` would have moved past
it. If the abandoned token turns up later, somebody did receive it, the chain
has two holders, and the session ends.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.user.models import UserSession


class RevokeReason(StrEnum):
    SIGNED_OUT = "signed_out"
    REVOKED = "revoked"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    REUSED = "reused"


@dataclass(frozen=True)
class Started:
    session_id: uuid.UUID
    refresh_token: str
    expires_at: datetime


@dataclass(frozen=True)
class Refreshed:
    session_id: uuid.UUID
    user_id: int
    expires_at: datetime
    # None when the presented token had just been rotated away by a
    # concurrent refresh: the caller already holds the successor, so there is
    # nothing new to hand out.
    refresh_token: str | None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


# How long a sign-in, once it has proved a credential, may get sudo tickets
# without proving one again.
SUDO_WINDOW = timedelta(minutes=10)


def _idle_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.refresh_idle_timeout_seconds)


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def start(
        self,
        user_id: int,
        login_method: str,
        *,
        ip: str,
        user_agent: str,
        two_factor_skipped: bool = False,
        sudo: bool = False,
    ) -> Started:
        """Open a session and hand back its first refresh token. ``sudo``
        opens its sudo window from now."""
        token = secrets.token_urlsafe(32)
        now = _now()
        row = UserSession(
            id=uuid.uuid4(),
            user_id=user_id,
            current_hash=_digest(token),
            login_method=login_method,
            two_factor_skipped=two_factor_skipped,
            user_agent=user_agent[:1024],
            ip=ip[:512],
            last_used_at=now,
            sudo_until=now + SUDO_WINDOW if sudo else None,
            expires_at=now + timedelta(seconds=settings.refresh_token_expires_seconds),
        )
        self._db.add(row)
        await self._db.flush()
        return Started(row.id, token, row.expires_at)

    async def refresh(self, token: str) -> Refreshed | None:
        """Exchange a refresh token for its successor, or None if it is spent.

        The swap is one conditional UPDATE, so of two requests presenting the
        same token exactly one rotates it. The other finds the token in
        ``previous_hash``: moments after the rotation that is a second tab
        refreshing at the same time, and it is answered without a new token.
        Later than that, the browser never received the successor, and it is
        given another one (see the module docstring). A token found in
        ``abandoned_hash`` was received after all, by someone, and the
        session ends.
        """
        presented = _digest(token)
        now = _now()
        successor = secrets.token_urlsafe(32)
        rotated = (
            await self._db.execute(
                update(UserSession)
                .where(
                    UserSession.current_hash == presented,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                    UserSession.last_used_at > _idle_cutoff(now),
                )
                .values(
                    previous_hash=UserSession.current_hash,
                    current_hash=_digest(successor),
                    rotated_at=now,
                    last_used_at=now,
                )
                .returning(UserSession.id, UserSession.user_id, UserSession.expires_at)
            )
        ).one_or_none()
        if rotated is not None:
            return Refreshed(rotated.id, rotated.user_id, rotated.expires_at, successor)

        earlier = await self._db.scalar(
            select(UserSession).where(UserSession.previous_hash == presented)
        )
        if earlier is not None:
            if not _is_live(earlier, now):
                return None
            grace = timedelta(seconds=settings.refresh_reuse_grace_seconds)
            if earlier.rotated_at is not None and now - earlier.rotated_at <= grace:
                return Refreshed(earlier.id, earlier.user_id, earlier.expires_at, None)
            # Conditional on the successor this request saw, so of two
            # browsers retrying at once exactly one replaces it; the other
            # lands inside the grace window of the one that did.
            reissued = (
                await self._db.execute(
                    update(UserSession)
                    .where(
                        UserSession.id == earlier.id,
                        UserSession.previous_hash == presented,
                        UserSession.current_hash == earlier.current_hash,
                    )
                    .values(
                        abandoned_hash=UserSession.current_hash,
                        current_hash=_digest(successor),
                        rotated_at=now,
                        last_used_at=now,
                    )
                    .returning(UserSession.id)
                )
            ).one_or_none()
            if reissued is None:
                return Refreshed(earlier.id, earlier.user_id, earlier.expires_at, None)
            return Refreshed(earlier.id, earlier.user_id, earlier.expires_at, successor)

        copied = await self._db.scalar(
            select(UserSession).where(UserSession.abandoned_hash == presented)
        )
        if copied is None or not _is_live(copied, now):
            return None
        copied.revoked_at = now
        copied.revoked_reason = RevokeReason.REUSED
        # Committed here: the caller answers this with a 401, and the
        # request's transaction is rolled back on an error.
        await self._db.commit()
        return None

    async def end(self, token: str) -> None:
        """Sign out the session this refresh token belongs to, if any."""
        presented = _digest(token)
        await self._db.execute(
            update(UserSession)
            .where(
                (UserSession.current_hash == presented)
                | (UserSession.previous_hash == presented),
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=_now(), revoked_reason=RevokeReason.SIGNED_OUT)
        )

    async def revoke(self, user_id: int, session_id: uuid.UUID) -> bool:
        """End one of the user's live sessions; False if there is no such."""
        now = _now()
        result = await self._db.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                *_live_clauses(now),
            )
            .values(revoked_at=now, revoked_reason=RevokeReason.REVOKED)
            .returning(UserSession.id)
        )
        return result.one_or_none() is not None

    async def revoke_all(
        self,
        user_id: int,
        reason: RevokeReason,
        *,
        keep: uuid.UUID | None = None,
    ) -> int:
        """End every live session of the user except ``keep``."""
        now = _now()
        query = update(UserSession).where(
            UserSession.user_id == user_id, *_live_clauses(now)
        )
        if keep is not None:
            query = query.where(UserSession.id != keep)
        result = await self._db.execute(
            query.values(revoked_at=now, revoked_reason=reason).returning(
                UserSession.id
            )
        )
        return len(result.all())

    async def open_sudo(self, user_id: int, session_id: uuid.UUID) -> None:
        """Open the session's sudo window from now, if it is still live."""
        now = _now()
        await self._db.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                *_live_clauses(now),
            )
            .values(sudo_until=now + SUDO_WINDOW)
        )

    async def in_sudo(self, user_id: int, session_id: uuid.UUID) -> bool:
        """Whether the session is live and inside its sudo window. Revoking
        the session ends the window with it."""
        now = _now()
        found = await self._db.scalar(
            select(UserSession.id).where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.sudo_until > now,
                *_live_clauses(now),
            )
        )
        return found is not None

    async def live(self, user_id: int) -> list[UserSession]:
        """The user's sessions that can still refresh, most recent first."""
        rows = await self._db.scalars(
            select(UserSession)
            .where(UserSession.user_id == user_id, *_live_clauses(_now()))
            .order_by(UserSession.last_used_at.desc())
        )
        return list(rows)


def _live_clauses(now: datetime) -> tuple:
    return (
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
        UserSession.last_used_at > _idle_cutoff(now),
    )


def _is_live(row: UserSession, now: datetime) -> bool:
    return (
        row.revoked_at is None
        and row.expires_at > now
        and row.last_used_at > _idle_cutoff(now)
    )
