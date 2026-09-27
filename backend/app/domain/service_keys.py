"""Gateway virtual keys the platform mints for its own small jobs.

问芝士 and topic naming each call the LiteLLM gateway on a key of their own,
with its own budget and rate limit, so one feature cannot spend another's money
and the deployment's upstream keys never leave the gateway. Minting is not
idempotent on the gateway, so a key is minted once — concurrent first calls
across processes serialise on an advisory lock, and the loser reads the
winner's key — and kept in ``service_credentials``.
"""

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.docs_site.models import ServiceCredential

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeySpec:
    """What a key is for and what it may spend."""

    name: str  # the row in service_credentials
    alias: str  # the gateway's key_alias, and metadata.purpose
    model: str
    budget_usd: float  # per 30 days
    rpm: int


def gateway_configured() -> bool:
    return bool(settings.llm_gateway_admin_base and settings.llm_gateway_admin_key)


def gateway_base() -> str:
    return (settings.llm_gateway_admin_base or "").rstrip("/")


async def service_key(
    session: AsyncSession,
    spec: KeySpec,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """The key for ``spec``, minted on first use; None when there is no gateway
    or minting failed (the feature is unavailable, not broken)."""
    if not gateway_configured():
        return None
    row = await session.get(ServiceCredential, spec.name)
    if row is not None:
        return row.secret
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": spec.name}
    )
    row = (
        await session.execute(
            select(ServiceCredential).where(ServiceCredential.name == spec.name)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row.secret
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0), transport=transport
        ) as client:
            r = await client.post(
                f"{gateway_base()}/key/generate",
                headers={"Authorization": f"Bearer {settings.llm_gateway_admin_key}"},
                json={
                    "key_alias": spec.alias,
                    "models": [spec.model],
                    "max_budget": spec.budget_usd,
                    "budget_duration": "30d",
                    "rpm_limit": spec.rpm,
                    "metadata": {"purpose": spec.alias},
                },
            )
            r.raise_for_status()
            key = r.json().get("key")
    except Exception:  # noqa: BLE001 — see the docstring
        logger.warning("minting the %s gateway key failed", spec.alias, exc_info=True)
        return None
    if not isinstance(key, str) or not key:
        return None
    session.add(ServiceCredential(name=spec.name, secret=key))
    await session.commit()
    return key
