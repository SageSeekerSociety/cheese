"""市场: the resource pools (AI + compute) a project can select from.

It also held a 团队↔题目 matching market on cheesex `task_templates`. That went
with the 赛题 merge (#370): 知是 already publishes 赛题 and teams already claim
them, with approval, quota and real-name checks the market never had. Browsing
and claiming a 赛题 lives on the Space pages.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_profile_registry, get_work_runner
from app.api.response import ok
from app.core.config import settings
from app.core.sentences import notice_keys, say
from app.domain.agent.environment import STANDARD, VM
from app.domain.agent.market import ai_listings, compute_listings
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.runtime import AgentWorkRunner

router = APIRouter(prefix="/market", tags=["market"])

Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]

Runner = Annotated[AgentWorkRunner, Depends(get_work_runner)]


@router.get("/pools")
async def list_pools(registry: Registry) -> dict:
    """The full catalog: every AI pool and sandbox spec on offer, each with
    its tier, price, and whether it's available to select. Powers the 市场
    browse view; a project selects from these in its settings."""
    return ok(
        {
            "ai": [asdict(p) for p in ai_listings(registry)],
            "compute": [asdict(p) for p in compute_listings(settings)],
        }
    )


# ---- 节点看板 (spec §9.1 机构提供算力: where turns physically run) ----


@router.get("/nodes")
async def list_nodes(runner: Runner) -> dict:
    """节点看板: the sandbox specs this deployment can place an agent's
    environment on, with liveness and current load (in-flight turns).

    Built from the same catalogue the 市场 browses, so the board cannot show a
    node the picker does not offer."""
    # What makes each spec live, in its own terms — the mono line under the card.
    cloud = (say("nodeCloudOnline"), say("nodeCloudOffline"))
    detail = {
        STANDARD: cloud,
        VM: cloud,
        "devices_of": (say("nodeDeviceOnline"), say("nodeDeviceOffline")),
    }
    nodes = [
        {
            "id": pool.id,
            "label": pool.label,
            "kind": pool.id,
            "online": pool.available,
            "current": pool.default,
            "detail": detail[pool.id][0 if pool.available else 1],
            "description": pool.description,
            **notice_keys(detail=detail[pool.id][0 if pool.available else 1]),
        }
        for pool in compute_listings(settings)
    ]
    # In-flight turns are counted per DEPLOYMENT: the work runner tracks a turn
    # without recording which machine took it, so there is no per-node number to
    # report. Repeating the total under every card would read as that many each.
    return ok(
        {
            "nodes": nodes,
            "active_turns_total": runner.active_work_count(),
            "current_provider": STANDARD,
        }
    )


# ---- 题目匹配 (spec §13 阶段 6) ----
