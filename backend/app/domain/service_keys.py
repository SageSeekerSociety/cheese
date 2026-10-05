"""Gateway virtual keys the platform mints for its own small jobs.

问芝士 and topic naming each call the LiteLLM gateway on a key of their own,
with its own rate limit, so the deployment's upstream keys never leave the
gateway. A key for work the platform does on its own (topic naming) also
carries a budget; 问芝士's does not, because each question is paid for by the
person who asked it. Minting is not
idempotent on the gateway, so a key is minted once — concurrent first calls
across processes serialise on an advisory lock, and the loser reads the
winner's key — and kept in ``service_credentials``.

The gateway takes each alias once and never shows a key's secret again, so a
key under the alias with no row here (its row was dropped so the key would be
re-minted with new limits) is of no use and blocks every later mint. Minting
therefore deletes whatever key holds the alias first: the alias belongs to
this deployment, and only the holder of the advisory lock, having found no
row, ever gets that far.
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
    budget_usd: float | None  # per 30 days; None for no budget on the key
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
            admin = {"Authorization": f"Bearer {settings.llm_gateway_admin_key}"}
            # 404 is the gateway saying no key holds the alias.
            freed = await client.post(
                f"{gateway_base()}/key/delete",
                headers=admin,
                json={"key_aliases": [spec.alias]},
            )
            if freed.status_code != 404:
                freed.raise_for_status()
                logger.warning(
                    "deleted the %s gateway key, which had no stored secret",
                    spec.alias,
                )
            r = await client.post(
                f"{gateway_base()}/key/generate",
                headers=admin,
                json={
                    "key_alias": spec.alias,
                    "models": [spec.model],
                    "rpm_limit": spec.rpm,
                    "metadata": {"purpose": spec.alias},
                    **(
                        {"max_budget": spec.budget_usd, "budget_duration": "30d"}
                        if spec.budget_usd is not None
                        else {}
                    ),
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
    logger.info("minted the %s gateway key", spec.alias)
    return key
