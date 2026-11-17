"""写「现状」的那一问：把项目材料交给模型，拿回一段芝士文档写法的 Markdown。

提示词是 2026-10-13 在 dev 上三个真实项目各跑三次后定下的那版：一句整体判断，
下面按项目要做出来的东西分三到五块，各带「可用 / 进行中 / 未开始」标签。只写项目
本身，不写人和平台；语气照界面文案规范的 8.0 和 8.6。思考强度不调低：调低以后模型会
把思考过程漏进正文。

用的是项目自己的模型（分身默认模型，没设就是项目默认模型），和文档里的芝士一样走
项目在网关上的那把钥匙，所以花费和项目里每一轮一样记在项目上，额度和模型档位的
限制也一样。项目的模型不走网关（成员自己的订阅）时不写：不拿别的模型替它。
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.domain.agent.document import question
from app.domain.agent.model_catalog import Scene, catalog, pick
from app.domain.agent.skills import load_skill
from app.domain.agent.supply import GATEWAY
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.prompts import Facts, Kind, render_system
from app.domain.service_keys import gateway_base

logger = logging.getLogger(__name__)

#: 大项目的思考会吃掉八千以上，给少了正文是空的。
_ANSWER_TOKENS = 24000


def system_prompt() -> str:
    """写现状的要求（`pieces/situation/`），加写作规矩、文档指南和块的写法。"""
    return render_system(Kind.situation, Facts(), skill=load_skill)


class ProjectModels(Protocol):
    """项目在网关上的那把钥匙，和它花掉的钱怎么记（`ChatService` 就是一个）。"""

    async def project_gateway_key(self, project_id: uuid.UUID) -> str | None: ...

    async def charge_turn_spend(
        self, project_id: uuid.UUID, topic_id: uuid.UUID | None, turn_id: uuid.UUID
    ) -> object: ...


async def _bind(session: AsyncSession, project_id: uuid.UUID) -> question.Bound | None:
    """这个项目写现状用哪个模型；不走网关、或者被额度和档位挡下，是 None。"""
    project = await ProjectService(session).get_or_404(project_id)
    # No teammate writes it: the project main model, else the platform's
    # subagent role, the job the platform does on the project's behalf.
    picked = pick(
        Scene.subagent, catalog(project.settings), project_settings=project.settings
    )
    chosen = picked.binding
    if chosen.supply != GATEWAY:
        return None
    agent = await AgentInstanceService(session).for_project(project)
    bound = question.Bound(
        agent_handle=agent_instance_handle(agent.instance_id),
        agent_name=agent.display_name or "",
        model=chosen.model,
        wire_model=chosen.wire_model,
        supply=chosen.supply,
        automatic=picked.automatic,
    )
    await question.admit(session, project_id, bound)
    return bound


@dataclass(frozen=True)
class Written:
    content: str
    model: str


async def write(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    material: str,
    models: ProjectModels,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Written | None:
    """模型写的现状；问不了、或者回来的是空的，是 None。"""
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
                            {"role": "system", "content": system_prompt()},
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
    if not content:
        return None
    return Written(content=content, model=bound.model)
