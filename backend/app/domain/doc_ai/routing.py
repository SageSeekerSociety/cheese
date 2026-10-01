"""Resolve the seated teammate's project binding; never substitute a model."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.agent.budget_proxy import BudgetState, decide
from app.domain.agent.supply import GATEWAY
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.policy import gate
from app.domain.project.services import ProjectService
from app.domain.room_task import binding
from app.domain.topic.services import TopicService
from app.domain.usage.services import UsageService


async def project_binding(session: AsyncSession, room_id: uuid.UUID) -> dict:
    topic = await TopicService(session).get_or_404(room_id)
    project = await ProjectService(session).get_or_404(topic.project_id)
    agent = await AgentInstanceService(session).for_topic(topic, project)
    choices = binding.catalog(project.settings)
    bound = binding.resolve(
        None,
        choices,
        agent_model=agent.configuration.get("model"),
        default_model=(project.settings or {}).get("default_model"),
    )
    return {
        "model": bound.model,
        "wire_model": bound.wire_model,
        "supply": bound.supply,
        "agent_handle": agent.handle,
    }


async def admit(session: AsyncSession, project_id: uuid.UUID, frozen: dict) -> None:
    project = await ProjectService(session).get_or_404(project_id)
    choices = binding.catalog(project.settings)
    bound = binding.resolve(None, choices, agent_model=frozen["model"])
    if bound.supply != frozen["supply"] or bound.wire_model != frozen["wire_model"]:
        raise ValidationError("已保存的文档 AI 模型供给已变化，请重新请求")
    choice = choices[bound.model]
    result = gate.check(
        gate.Call(
            resource=gate.Resource.model,
            subject=bound.model,
            label=choice["label"],
            tier=choice["tier"],
            approver=project.owner_handle or "",
        ),
        gate.policy_of(project.settings),
        actor=frozen["agent_handle"],
    )
    if isinstance(result, gate.Proposal):
        raise ValidationError(result.content)
    summary = await UsageService(session).project_credits(project_id)
    decision = decide(
        BudgetState(
            spent=summary["credits_used"],
            limit=None if summary["unlimited"] else summary["credits_total"],
        )
    )
    if not decision.allow:
        raise ValidationError(decision.reason)
    if bound.supply != GATEWAY:
        raise ValidationError(
            "所选订阅模型尚不支持无工具文档 completion；没有切换模型或供给"
        )
