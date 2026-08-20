"""市场: the resource pools (AI + compute) a project can select from.

It also held a 团队↔题目 matching market on cheesex `task_templates`. That went
with the 赛题 merge (#370): 知是 already publishes 赛题 and teams already claim
them, with approval, quota and real-name checks the market never had. Browsing
and claiming a 赛题 lives on the Space pages.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_profile_registry, get_work_runner
from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.domain.agent.market import (
    ai_listings,
    compute_listings,
    visibility_listings,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.tmux_provider import TmuxChannel

router = APIRouter(prefix="/market", tags=["market"])

Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
Runner = Annotated[AgentWorkRunner, Depends(get_work_runner)]


@router.get("/pools")
async def list_pools(registry: Registry) -> dict:
    """The full catalog: every AI pool and compute pool on offer, each with its
    tier, price, and whether it's available to select. Powers the 市场 browse
    view; a project selects from these in its settings."""
    return ok(
        {
            "ai": [asdict(p) for p in ai_listings(registry)],
            "compute": [asdict(p) for p in compute_listings(settings)],
            # #282 §四 / #358: the whole-machine sub-choice under a self-hosted
            # machine, carried with its capability copy so a picker (and the room
            # badge) renders the honest warning rather than hiding it.
            "visibility": [asdict(v) for v in visibility_listings()],
        }
    )


# ---- 节点看板 (spec §9.1 机构提供算力: where turns physically run) ----


@router.get("/nodes")
async def list_nodes(runner: Runner) -> dict:
    """节点看板: the compute nodes this deployment runs, with liveness and
    current load (in-flight turns)."""
    from app.domain.workspace import service as ws

    active = runner.active_work_count()
    online = ws.sandbox_available()
    nodes: list[dict] = [
        {
            "id": TmuxChannel.name,
            "label": "知是本地算力",
            "kind": "local",
            "online": online,
            "current": True,
            "active_turns": active,
            "detail": (
                f"沙箱镜像 {settings.tmux_sandbox_image}"
                if online
                else "无 Docker 沙箱：本机跑不了回合"
            ),
            "description": "平台托管的容器算力（CPU 级），跑代码、文档与数据分析。",
        }
    ]
    return ok(
        {
            "nodes": nodes,
            "active_turns_total": active,
            "current_provider": TmuxChannel.name,
        }
    )


# ---- 题目匹配 (spec §13 阶段 6) ----
