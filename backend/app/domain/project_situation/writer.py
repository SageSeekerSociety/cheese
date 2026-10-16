"""写「现状」的那一问：把项目材料交给模型，拿回一段芝士文档写法的 Markdown。

提示词是 2026-10-13 在 dev 上三个真实项目各跑三次后定下的那版：一句整体判断，
下面按项目要做出来的东西分三到五块，各带「可用 / 进行中 / 未开始」标签。只写项目
本身，不写人和平台；语气照界面文案规范的 8.0 和 8.6。思考强度不调低：调低以后模型会
把思考过程漏进正文。
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent.harness.prompt import WRITING
from app.domain.gateway_chat import Usage, response_cost
from app.domain.service_keys import KeySpec, gateway_base, service_key
from app.domain.skill_library import SKILL_DIR, parse_frontmatter
from app.domain.usage.ledger import Ledger

logger = logging.getLogger(__name__)

USAGE_KIND = "project_situation"
#: 大项目的思考会吃掉八千以上，给少了正文是空的。
_ANSWER_TOKENS = 24000

#: 写现状的要求：长，所以单独放在旁边的 Markdown 里。
_RULES = (Path(__file__).parent / "situation_prompt.md").read_text(encoding="utf-8")


def system_prompt() -> str:
    guide = parse_frontmatter(SKILL_DIR / "doc_writing.md")[1]
    blocks = parse_frontmatter(SKILL_DIR / "doc_blocks.md")[1]
    return f"{_RULES}\n\n{WRITING}\n\n# 文档指南\n{guide}\n\n# 块的写法\n{blocks}"


def _key_spec() -> KeySpec:
    return KeySpec(
        name="project-situation-gateway-key",
        alias="project-situation",
        model=settings.project_situation_model,
        budget_usd=settings.project_situation_budget_usd,
        rpm=60,
    )


@dataclass(frozen=True)
class Written:
    content: str
    model: str
    cost_usd: float


async def write(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    material: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Written | None:
    """模型写的现状；问不了、或者回来的是空的，是 None。花费记在项目所在的团队上。"""
    key = await service_key(session, _key_spec(), transport)
    if key is None:
        return None
    model = settings.project_situation_model
    total = Usage()
    cost = 0.0
    content = ""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.project_situation_timeout_seconds),
            transport=transport,
        ) as client:
            # 回来是空的再问一次：思考吃满了预算时正文就是空的，第二次通常写得出来。
            for _ in range(2):
                r = await client.post(
                    f"{gateway_base()}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt()},
                            {"role": "user", "content": material},
                        ],
                        "max_tokens": _ANSWER_TOKENS,
                    },
                )
                r.raise_for_status()
                payload = r.json()
                total = total + Usage.of(payload.get("usage"))
                cost += response_cost(r)
                content = (
                    payload["choices"][0]["message"].get("content") or ""
                ).strip()
                if content:
                    break
    except Exception:  # noqa: BLE001 — a failed call keeps the last one written
        logger.warning(
            "project situation call failed project=%s", project_id, exc_info=True
        )
        content = ""
    if total.total_tokens or cost:
        await Ledger(session).record_project_call(
            project_id,
            kind=USAGE_KIND,
            model=model,
            input_tokens=total.prompt_tokens,
            output_tokens=total.completion_tokens,
            cost_usd=cost,
        )
    if not content:
        return None
    return Written(content=content, model=model, cost_usd=cost)
