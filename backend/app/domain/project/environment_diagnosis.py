"""「让芝士看看」: read why a channel's environment failed to prepare, and
say what to change.

One question to the model, not a session: it is handed the end of the failed
log and the two scripts that ran, and answers with the cause, the change, and
the scripts as they would read after it. It reads nothing else and runs
nothing; a person reads the change and decides whether to take it (the
settings page saves it and retries). It is work a person asked the platform
for about the platform's own environment runner, so the platform pays for it,
as for naming tasks.
"""

import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.gateway_chat import Usage, response_cost
from app.domain.service_keys import KeySpec, gateway_base, service_key
from app.domain.usage.ledger import Ledger

logger = logging.getLogger(__name__)

USAGE_KIND = "environment_diagnosis"
_ANSWER_TOKENS = 4096

SYSTEM_PROMPT = "\n".join(
    [
        "你帮人看一次工作电脑环境准备为什么失败。",
        "",
        "工作电脑开工前先跑「准备脚本」（每份新的工作区第一次开工前跑一次），"
        "再跑「启动脚本」（每次开工前跑一次）。两段都是 Bash，在项目仓库的检出里执行。",
        "",
        "你只拿到失败日志的末尾和这两段脚本。只根据它们判断，不编造日志里没有的事；"
        "看不出原因就如实说。",
        "",
        "只回一个 JSON 对象：",
        '{"reason": "一两句：为什么失败", "change": "一两句：怎么改", '
        '"setup_script": "改后的完整准备脚本，不用改就是 null", '
        '"startup_script": "改后的完整启动脚本，不用改就是 null", '
        '"sure": true 或 false}',
        "",
        "改动尽量小：只动和这次失败有关的行，其余原样保留。不要为了让它不报错"
        "而删掉必要的安装步骤，也不要加忽略错误的写法。",
    ]
)


@dataclass(frozen=True)
class Diagnosis:
    reason: str
    change: str
    setup_script: str | None
    startup_script: str | None
    sure: bool


def _key_spec() -> KeySpec:
    return KeySpec(
        name="environment-diagnosis-gateway-key",
        alias="environment-diagnosis",
        model=settings.environment_diagnosis_model,
        budget_usd=settings.environment_diagnosis_budget_usd,
        rpm=60,
    )


def _material(config: dict, failure: dict) -> str:
    return "\n\n".join(
        [
            "失败在："
            + ("启动脚本" if failure.get("stage") == "startup" else "准备脚本")
            + "，"
            f"退出码 {failure.get('exit_code')}",
            "<准备脚本>\n" + (config.get("setup_script") or "") + "\n</准备脚本>",
            "<启动脚本>\n" + (config.get("startup_script") or "") + "\n</启动脚本>",
            "<日志末尾>\n" + (failure.get("log") or "") + "\n</日志末尾>",
        ]
    )


def parse(content: str) -> Diagnosis | None:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("reason") or "").strip():
        return None

    def script(name: str) -> str | None:
        value = data.get(name)
        return value if isinstance(value, str) else None

    return Diagnosis(
        reason=str(data["reason"]).strip(),
        change=str(data.get("change") or "").strip(),
        setup_script=script("setup_script"),
        startup_script=script("startup_script"),
        sure=data.get("sure") is True,
    )


async def diagnose(
    session: AsyncSession,
    *,
    config: dict,
    failure: dict,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Diagnosis | None:
    """What the model makes of this failure; None when it could not be asked
    or did not answer in the shape asked for."""
    key = await service_key(session, _key_spec(), transport)
    if key is None:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.environment_diagnosis_timeout_seconds),
            transport=transport,
        ) as client:
            r = await client.post(
                f"{gateway_base()}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": settings.environment_diagnosis_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _material(config, failure)},
                    ],
                    "max_tokens": _ANSWER_TOKENS,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            payload = r.json()
            usage = Usage.of(payload.get("usage"))
            cost_usd = response_cost(r)
            content = payload["choices"][0]["message"]["content"] or ""
    except Exception:  # noqa: BLE001 — a failed call is an answer of None
        logger.warning("environment diagnosis call failed", exc_info=True)
        return None
    if usage is not None and (usage.total_tokens or cost_usd):
        await Ledger(session).record_platform(
            kind=USAGE_KIND,
            model=settings.environment_diagnosis_model,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=cost_usd,
        )
    return parse(content)
