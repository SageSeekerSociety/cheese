"""写「现状」的那一问：把项目的记录交给模型，拿回分好的几块（JSON）。

一次调用，不给工具：材料里已经是它能引的全部记录，它要做的只是分块和写句子。每块
的出处和状态由 ``card`` 核对和判定，不信模型自己说的。思考强度不调低：调低以后模型
会把思考过程漏进正文。

用的是项目自己的模型（分身默认模型，没设就是项目默认模型），和文档里的芝士一样走
项目在网关上的那把钥匙，所以花费和项目里每一轮一样记在项目上，额度和模型档位的
限制也一样。项目的模型不走网关（成员自己的订阅）时不写：不拿别的模型替它。
"""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.domain.agent.document import question
from app.domain.agent.model_catalog import catalog, subagent_default
from app.domain.agent.supply import GATEWAY
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.policy import gate
from app.domain.project.services import ProjectService
from app.domain.room_task import binding
from app.domain.service_keys import gateway_base
from app.domain.usage.services import UsageService

logger = logging.getLogger(__name__)

#: 大项目的思考会吃掉八千以上，给少了正文是空的。
_ANSWER_TOKENS = 24000

#: 写现状的要求：长，所以单独放在旁边的 Markdown 里。
SYSTEM_PROMPT = (Path(__file__).parent / "situation_prompt.md").read_text(
    encoding="utf-8"
)


class ProjectModels(Protocol):
    """项目在网关上的那把钥匙，和它花掉的钱怎么记（`ChatService` 就是一个）。"""

    async def project_gateway_key(self, project_id: uuid.UUID) -> str | None: ...

    async def charge_turn_spend(
        self, project_id: uuid.UUID, topic_id: uuid.UUID | None, turn_id: uuid.UUID
    ) -> object: ...


async def _bind(session: AsyncSession, project_id: uuid.UUID) -> question.Bound | None:
    """这个项目写现状用哪个模型；不走网关、或者被额度和档位挡下，是 None。"""
    project = await ProjectService(session).get_or_404(project_id)
    configured = project.settings or {}
    choices = catalog(configured)
    usage = UsageService(session)
    policy = gate.policy_of(
        configured,
        await usage.plan_model_tiers(project.team_id),
        overseas_approved=await usage.overseas_approved(project.team_id),
    )
    chosen = binding.resolve(
        None, choices, default_model=subagent_default(configured, choices, policy)
    )
    if chosen.supply != GATEWAY:
        return None
    agent = await AgentInstanceService(session).for_project(project)
    bound = question.Bound(
        agent_handle=agent_instance_handle(agent.instance_id),
        agent_name=agent.display_name or "",
        model=chosen.model,
        wire_model=chosen.wire_model,
        supply=chosen.supply,
    )
    await question.admit(session, project_id, bound)
    return bound


@dataclass(frozen=True)
class Written:
    #: 模型回的那个 JSON 对象，还没核对过。
    answer: dict
    model: str


def _parse(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        answer = json.loads(text)
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


async def write(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    material: str,
    models: ProjectModels,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Written | None:
    """模型分好的那几块；问不了、或者回来的不是一个 JSON 对象，是 None。"""
    try:
        bound = await _bind(session, project_id)
    except UnprocessableEntityError as refused:
        logger.info("project situation not written project=%s: %s", project_id, refused)
        return None
    if bound is None:
        return None
    key = await models.project_gateway_key(project_id)
    if not key:
        return None
    content = ""
    asked = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.project_situation_timeout_seconds),
            transport=transport,
        ) as client:
            # 回来是空的再问一次：思考吃满了预算时正文就是空的，第二次通常写得出来。
            for _ in range(2):
                asked = True
                r = await client.post(
                    f"{gateway_base()}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": bound.wire_model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": material},
                        ],
                        "max_tokens": _ANSWER_TOKENS,
                    },
                )
                r.raise_for_status()
                message = r.json()["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                if content:
                    break
    except Exception:  # a failed call keeps the last one written
        logger.warning(
            "project situation call failed project=%s", project_id, exc_info=True
        )
        content = ""
    if asked:
        # 花在项目钥匙上的，和项目里的一轮一样从网关的记录里结算。
        await models.charge_turn_spend(project_id, None, uuid.uuid4())
    answer = _parse(content) if content else None
    if answer is None:
        if content:
            logger.warning(
                "project situation answer is not JSON project=%s", project_id
            )
        return None
    return Written(answer=answer, model=bound.model)
