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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.domain.agent import one_shot
from app.domain.agent.model_catalog import Scene
from app.domain.agent.skills import load_skill
from app.domain.prompts import Facts, Kind, render_system

logger = logging.getLogger(__name__)

#: 大项目的思考会吃掉八千以上，给少了正文是空的。
_ANSWER_TOKENS = 24000

#: 项目在网关上的那把钥匙，和它花掉的钱怎么记（`ChatService` 就是一个）。
ProjectModels = one_shot.ProjectModels


def system_prompt() -> str:
    """写现状的要求（`pieces/situation/`），加写作规矩、文档指南和块的写法。"""
    return render_system(Kind.situation, Facts(), skill=load_skill)


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
    # No teammate writes it: the project main model, else the platform's
    # subagent role, the job the platform does on the project's behalf.
    try:
        bound = await one_shot.bind(session, project_id, Scene.subagent)
    except UnprocessableEntityError as refused:
        logger.info("project situation not written project=%s: %s", project_id, refused)
        return None
    if bound is None:
        return None
    # 回来是空的再问一次：思考吃满了预算时正文就是空的，第二次通常写得出来。
    answer = await one_shot.ask(
        project_id=project_id,
        bound=bound,
        system=system_prompt(),
        material=material,
        models=models,
        timeout=settings.project_situation_timeout_seconds,
        max_tokens=_ANSWER_TOKENS,
        attempts=2,
        transport=transport,
    )
    if answer is None:
        return None
    return Written(content=answer.content, model=answer.model)
