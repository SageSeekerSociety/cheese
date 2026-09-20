"""Project account tokens, with durable reservations before upstream issuance."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.db import SessionFactory, async_session_factory
from app.domain.project.models import ForgeToken, ProjectForge

logger = logging.getLogger(__name__)


class ForgejoTokenError(RuntimeError):
    pass


class ForgejoTokens:
    def __init__(
        self,
        binding: ProjectForge,
        *,
        sessions: SessionFactory = async_session_factory,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.binding = binding
        self.sessions = sessions
        self.transport = transport

    async def installation_token(self) -> tuple[str, str]:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            cached = await session.scalar(
                select(ForgeToken)
                .where(
                    ForgeToken.project_id == self.binding.project_id,
                    ForgeToken.api_url == self.binding.api_url,
                    ForgeToken.username == self.binding.repo.split("/", 1)[0],
                    ForgeToken.expires_at > now + timedelta(minutes=5),
                    ForgeToken.value.is_not(None),
                )
                .order_by(ForgeToken.expires_at.desc())
                .limit(1)
            )
            if cached is not None and cached.value:
                return decrypt_text(cached.value), cached.expires_at.isoformat()
            if not self.binding.account_password:
                raise ForgejoTokenError("Forgejo project account is not configured")
            lease = ForgeToken(
                id=uuid.uuid4(),
                project_id=self.binding.project_id,
                api_url=self.binding.api_url,
                username=self.binding.repo.split("/", 1)[0],
                account_password=self.binding.account_password,
                token_name="cheese-" + uuid.uuid4().hex,
                expires_at=now + timedelta(seconds=settings.forge_token_ttl_seconds),
            )
            session.add(lease)
            # Commit BEFORE minting: even a lost HTTP response leaves a revocable name.
            await session.commit()
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20
            ) as client:
                response = await client.post(
                    lease.api_url.rstrip("/")
                    + f"/users/{quote(lease.username, safe='')}/tokens",
                    auth=(lease.username, decrypt_text(lease.account_password)),
                    # Repository authority must not include changing the bot password.
                    json={
                        "name": lease.token_name,
                        "scopes": [
                            "write:repository",
                            "write:issue",
                            "write:package",
                            "write:notification",
                            "read:user",
                        ],
                    },
                )
            if response.status_code != 201:
                raise ForgejoTokenError(
                    f"Forgejo token mint failed (HTTP {response.status_code})"
                )
            token = response.json()["sha1"]
            lease.value = encrypt_text(token)
            await session.commit()
            return token, lease.expires_at.isoformat()

    async def write_token(self) -> tuple[str, str]:
        return await self.installation_token()

    async def granted_permissions(self) -> dict[str, str]:
        return {"project repository": "owner"}


async def revoke_expired_tokens(
    sessions: SessionFactory,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    """Retry failures next tick; retain their rows until upstream confirms deletion."""
    revoked = failed = 0
    async with sessions() as session:
        ids = list(
            await session.scalars(
                select(ForgeToken.id).where(ForgeToken.expires_at <= datetime.now(UTC))
            )
        )
    for lease_id in ids:
        async with sessions() as session:
            lease = await session.get(ForgeToken, lease_id)
            if lease is None:
                continue
            try:
                async with httpx.AsyncClient(transport=transport, timeout=20) as client:
                    response = await client.delete(
                        lease.api_url.rstrip("/")
                        + f"/users/{quote(lease.username, safe='')}/tokens/"
                        + quote(lease.token_name, safe=""),
                        auth=(lease.username, decrypt_text(lease.account_password)),
                    )
                if response.status_code not in (204, 404):
                    raise ForgejoTokenError(f"HTTP {response.status_code}")
                await session.delete(lease)
                await session.commit()
                revoked += 1
            except (httpx.HTTPError, ForgejoTokenError) as exc:
                failed += 1
                # Never log HTTP exception text: a URL can contain credentials.
                logger.error(
                    "Forgejo credential revocation failed for lease %s (%s)",
                    lease_id,
                    type(exc).__name__,
                )
    return {"revoked": revoked, "failed": failed}
