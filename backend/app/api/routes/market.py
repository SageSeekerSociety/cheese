"""市场: the resource pools (AI + compute) a project can select from.

It also held a 团队↔题目 matching market on cheesex `task_templates`. That went
with the 赛题 merge (#370): 知是 already publishes 赛题 and teams already claim
them, with approval, quota and real-name checks the market never had. Browsing
and claiming a 赛题 lives on the Space pages.
"""

from dataclasses import asdict
from typing import Annotated

import httpx
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

router = APIRouter(prefix="/api/market", tags=["market"])

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


async def _probe_cheesed(url: str) -> tuple[bool, str]:
    """Liveness of a cheesed node via its existing /health endpoint. Returns
    (online, node_image) — image empty when unreachable/unhealthy."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{url.rstrip('/')}/health")
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False, ""
    if data.get("status") != "healthy":
        return False, ""
    return True, str(data.get("image") or "")


@router.get("/nodes")
async def list_nodes(runner: Runner) -> dict:
    """节点看板: every configured compute node (local + cheesed remote) with
    liveness, current load (in-flight turns), and what it runs. Load is the
    runner's active-turn count attributed to the provider turns actually run on
    (compute_provider) — the platform runs one provider at a time today."""
    from app.domain.workspace import service as ws

    active = runner.active_work_count()
    current = settings.compute_provider  # "local" | "remote"

    local_sandboxed = settings.agent_sandbox_enabled and ws.sandbox_available()
    nodes: list[dict] = [
        {
            "id": "local-docker",
            "label": "知是本地算力",
            "kind": "local",
            "online": True,
            "current": current == "local",
            "active_turns": active if current == "local" else 0,
            "detail": (
                f"沙箱镜像 {settings.sandbox_image}"
                if local_sandboxed
                else "无 Docker 沙箱（降级：纯模型回合）"
            ),
            "description": "平台托管的容器算力（CPU 级），跑代码、文档与数据分析。",
        }
    ]
    if settings.cheesed_url:
        online, image = await _probe_cheesed(settings.cheesed_url)
        nodes.append(
            {
                "id": "remote-cheesed",
                "label": "远程节点（cheesed）",
                "kind": "remote",
                "online": online,
                "current": current == "remote",
                "active_turns": active if current == "remote" else 0,
                "detail": f"沙箱镜像 {image}" if image else settings.cheesed_url,
                "description": "自带机器上的 cheesed 节点，数据不出你的环境。",
            }
        )
    return ok(
        {
            "nodes": nodes,
            "active_turns_total": active,
            "current_provider": current,
        }
    )


# ---- 题目匹配 (spec §13 阶段 6) ----
