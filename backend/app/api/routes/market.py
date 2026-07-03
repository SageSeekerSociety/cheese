"""市场: resource pools (AI + compute) and the 团队↔题目 matching market.

The matching market (spec §13 阶段 6): Spaces publish Task Templates (题目),
teams apply with a Project (应征), the Space accepts → Task + link + 通知.
"""

import uuid
from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_profile_registry, get_turn_runner
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.domain.agent.market import ai_listings, compute_listings
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.runtime import TurnRunner
from app.domain.task.models import TaskApplication
from app.domain.task.schemas import (
    ApplicationCreate,
    ApplicationDecide,
    MarketTaskOut,
    TaskApplicationOut,
)
from app.domain.task.services import TaskApplicationService, TaskTemplateService

router = APIRouter(prefix="/api/market", tags=["market"])

Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
Runner = Annotated[TurnRunner, Depends(get_turn_runner)]


def _application_out(application: TaskApplication, project_name: str = "") -> dict:
    out = TaskApplicationOut.model_validate(application).model_dump(mode="json")
    out["project_name"] = project_name
    return out


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

    active = runner.active_turns()
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


@router.get("/tasks")
async def list_market_tasks(db: DbSession, q: str | None = None) -> dict:
    """All published 题目 (Task Templates) with their Space name; `q` filters
    by keyword on the template name/description or Space name."""
    rows = await TaskTemplateService(db).list_published(query=q)
    items = [
        MarketTaskOut.model_validate(
            {
                "id": template.id,
                "space_id": template.space_id,
                "space_name": space_name,
                "name": template.name,
                "description": template.description,
                "resource_pack": template.resource_pack,
                "conditions": template.conditions,
                "default_role": template.default_role,
                "created_at": template.created_at,
            }
        ).model_dump(mode="json")
        for template, space_name in rows
    ]
    return ok(page(items, len(items)))


@router.post("/tasks/{template_id}/apply")
async def apply_market_task(
    template_id: uuid.UUID, body: ApplicationCreate, db: DbSession
) -> dict:
    """应征: a team throws its project's hat in the ring. Idempotent — applying
    again with the same project returns the existing application."""
    application = await TaskApplicationService(db).apply(
        template_id=template_id, project_id=body.project_id, pitch=body.pitch
    )
    return ok(_application_out(application))


@router.get("/tasks/{template_id}/applications")
async def list_task_applications(template_id: uuid.UUID, db: DbSession) -> dict:
    """Space side: everyone who applied to this 题目 (no fine-grained Space
    permissions yet — MVP)."""
    rows = await TaskApplicationService(db).list_for_template(template_id)
    items = [_application_out(a, name) for a, name in rows]
    return ok(page(items, len(items)))


@router.post("/applications/{application_id}/accept")
async def accept_application(
    application_id: uuid.UUID, body: ApplicationDecide, db: DbSession
) -> dict:
    """Accept an 应征: creates the Task + ProjectTaskLink and notifies the
    project (protocol signed, spec §4.2)."""
    application = await TaskApplicationService(db).accept(
        application_id=application_id, decided_by=body.decided_by
    )
    return ok(_application_out(application))


@router.post("/applications/{application_id}/decline")
async def decline_application(
    application_id: uuid.UUID, body: ApplicationDecide, db: DbSession
) -> dict:
    """婉拒 an 应征 (the project gets a light notification)."""
    application = await TaskApplicationService(db).decline(
        application_id=application_id, decided_by=body.decided_by
    )
    return ok(_application_out(application))
