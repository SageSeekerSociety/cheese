"""市场: the resource-pool catalog (AI pools + compute pools)."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_profile_registry
from app.api.response import ok
from app.core.config import settings
from app.domain.agent.market import ai_listings, compute_listings
from app.domain.agent.profiles import ProfileRegistry

router = APIRouter(prefix="/api/market", tags=["market"])

Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


@router.get("/pools")
async def list_pools(registry: Registry) -> dict:
    """The full catalog: every AI pool and compute pool on offer, each with its
    tier, price, and whether it's available to select. Powers the 市场 browse
    view; a project selects from these in its settings."""
    return ok(
        {
            "ai": [asdict(p) for p in ai_listings(registry)],
            "compute": [asdict(p) for p in compute_listings(settings)],
        }
    )
