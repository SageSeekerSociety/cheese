"""LLM judge: score a scenario's evidence against its rubric.

Calls the same Anthropic-compatible gateway the backend uses (httpx, one
/v1/messages call — no CLI spin-up), with a configurable judge model. The judge
returns STRICT JSON {"score": 0|1|2, "reason": "..."}; we parse that JSON — our
own requested structured format, not the agent's free prose.
"""

import json
import re
from dataclasses import dataclass

import httpx

from evals.lib.records import JudgeVerdict

_SYSTEM = (
    "你是严格的验收评审（judge）。你会拿到：一条产品验收标准（rubric）和被测系统"
    "一次真实运行的证据（evidence）。请只依据证据打分，不要臆测未展示的行为。\n"
    "评分为 0-2 分：2=完全达标；1=部分达标（方向对但有明显缺口）；0=未达标。\n"
    '只输出一个 JSON 对象，不要多余文字：{"score": 0|1|2, "reason": "简要理由，'
    '引用证据中的关键点"}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class JudgeConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 120.0


def _parse_verdict(text: str) -> tuple[int, str]:
    match = _JSON_RE.search(text)
    if match is None:
        raise ValueError(f"judge output contains no JSON object: {text[:200]!r}")
    obj = json.loads(match.group(0))
    score = int(obj["score"])
    if score not in (0, 1, 2):
        raise ValueError(f"judge score out of range: {score}")
    return score, str(obj.get("reason", ""))


async def run_judge(
    *, rubric: str, evidence: str, config: JudgeConfig
) -> JudgeVerdict:
    """One judge call → structured verdict. Retries once with a JSON-only nudge
    if the first response fails to parse."""
    prompt = (
        f"## 验收标准（rubric）\n{rubric}\n\n"
        f"## 运行证据（evidence）\n{evidence}\n\n"
        "请按系统指令打分，只输出 JSON。"
    )
    url = f"{config.base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": config.api_key,
        "authorization": f"Bearer {config.api_key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    messages: list[dict] = [{"role": "user", "content": prompt}]
    raw = ""
    async with httpx.AsyncClient(timeout=config.timeout_s) as client:
        for attempt in range(2):
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": config.model,
                    "max_tokens": 1024,
                    "system": _SYSTEM,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            raw = "".join(
                blk.get("text", "")
                for blk in body.get("content", [])
                if blk.get("type") == "text"
            )
            try:
                score, reason = _parse_verdict(raw)
            except (ValueError, KeyError, json.JSONDecodeError):
                if attempt == 1:
                    raise
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": '输出不是合法 JSON。请只输出 {"score": 0|1|2, '
                        '"reason": "..."}，不要任何其他文字。',
                    },
                ]
                continue
            return JudgeVerdict(
                score=score,
                reason=reason,
                model=config.model,
                raw_response=raw,
                prompt=prompt,
            )
    raise RuntimeError("unreachable")  # pragma: no cover
