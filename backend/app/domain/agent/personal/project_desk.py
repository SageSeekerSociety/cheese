"""A project's settings that 芝士 may change, written out for it.

What it reads before it changes them for the person: the project's short name,
how its tasks are named, the AI teammate and the model new channels start
with — each with what it can be set to — and who manages each channel.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.model_catalog import model_choices
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.assistant import refs
from app.domain.project.models import Project
from app.domain.room_task.naming import naming_mode
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.services import UsageService

_NAMING = {"auto": "自动命名（auto）", "manual": "手动命名（manual）"}


async def settings(session: AsyncSession, project: Project, channels: list) -> str:
    """``channels`` are the project's channels the person sees, as their
    pages list them."""
    agents = await AgentInstanceService(session).list_for_project(project.id)
    default = next(
        (a.handle for a in agents if a.id == project.default_agent_instance_id), None
    )
    access = await UsageService(session).model_access(project.team_id)
    models = [
        m["id"]
        for m in model_choices(project.settings)
        if access.shows(m) and access.allows(m)
    ]
    chosen = (project.settings or {}).get("default_model") or "平台默认"
    lines = [
        f"项目「{project.name}」的设置",
        f"短名（slug）：{project.slug}",
        f"任务命名（task_naming）：{_NAMING[naming_mode(project.settings)]}",
        f"默认 AI 队友（default_agent）：{default or '未设'}；可选："
        + "、".join(a.handle for a in agents),
        f"默认模型（default_model）：{chosen}；可选：" + "、".join(models),
        "频道负责人（在频道编号上改 manager）：",
    ]
    members = TopicMemberService(session)
    for channel in channels:
        manager = await members.owner_of(channel.id)
        lines.append(
            f"- 「{channel.title}」（{refs.channel(project.slug, channel.number)}）："
            f"{manager or '未设'}"
        )
    return "\n".join(lines)
