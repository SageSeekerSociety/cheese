"""个人芝士 的数据页: what its gate let through and stopped, what the gate
cost the platform, and how well conversations keep their prompt's start.

Two things are written as the feature runs, and this page only reads them:

* ``assistant_gate_checks`` — one row per action 芝士 was about to take that
  others would see (`assistant.gate`): its kind, whether it was done or turned
  into a card, at which stage that was settled, and the tokens, cache share,
  cost and time of the looks. The platform pays for them; this is where it
  sees what it pays.
* each answer's ``parts.usage.calls`` on ``assistant_messages`` — every model
  call of the answer, as the prompt it sent and how much of it the provider's
  cache answered. The prompt cache is what makes a conversation cheap; its
  share is the number to watch when the rules or the tools change.

As on the other pages, a rate with nothing under it is ``null``, not zero. No
query selects who asked or what was said: the kinds and the counts are enough
to decide whether the gate stops too much.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.assistant.models import AssistantGateCheck, AssistantMessage
from app.domain.day_windows import dense_series, utc_day, utc_day_window
from app.domain.feature_stats import stats

FEATURE_ID = "personal-assistant"
TITLE = "个人芝士"
SUMMARY = "把关放行和拦下了什么、花了多少，对话的缓存命中"


async def _gate(session: AsyncSession, since, until) -> dict[str, Any]:
    window = (
        AssistantGateCheck.created_at >= since,
        AssistantGateCheck.created_at < until,
    )
    rows = (
        await session.execute(
            select(
                AssistantGateCheck.kind,
                AssistantGateCheck.verdict,
                AssistantGateCheck.stage,
                AssistantGateCheck.input_tokens,
                AssistantGateCheck.cache_read_tokens,
                AssistantGateCheck.output_tokens,
                AssistantGateCheck.cost_usd,
                AssistantGateCheck.latency_ms,
            ).where(*window)
        )
    ).all()
    by_stage: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for row in rows:
        key = f"{row.verdict}:{row.stage}"
        by_stage[key] = by_stage.get(key, 0) + 1
        kind = by_kind.setdefault(row.kind, {"allowed": 0, "asked": 0})
        kind[row.verdict] = kind.get(row.verdict, 0) + 1
    looked = [r for r in rows if r.stage in ("quick", "reasoned")]
    prompt = sum(r.input_tokens for r in rows)
    cached = sum(r.cache_read_tokens for r in rows)
    return {
        "checks": len(rows),
        "allowed": sum(1 for r in rows if r.verdict == "allowed"),
        "asked": sum(1 for r in rows if r.verdict == "asked"),
        "byStage": by_stage,
        "byKind": [{"kind": k, **v} for k, v in sorted(by_kind.items())],
        "inputTokens": prompt,
        "cacheReadTokens": cached,
        "cacheRate": stats.ratio(cached, prompt),
        "outputTokens": sum(r.output_tokens for r in rows),
        "costUsd": sum(r.cost_usd for r in rows),
        "latencyMs": stats.describe([r.latency_ms for r in looked]),
    }


async def _answers(session: AsyncSession, since, until) -> dict[str, Any]:
    parts = await session.scalars(
        select(AssistantMessage.parts).where(
            AssistantMessage.role == "assistant",
            AssistantMessage.created_at >= since,
            AssistantMessage.created_at < until,
            AssistantMessage.parts["usage"].is_not(None),
        )
    )
    answers = calls = prompt = cached = 0
    per_call: list[float] = []
    for part in parts:
        made = ((part or {}).get("usage") or {}).get("calls") or []
        if not made:
            continue
        answers += 1
        for sent, read in made:
            calls += 1
            prompt += sent
            cached += read
            if sent:
                per_call.append(read / sent)
    return {
        "answers": answers,
        "calls": calls,
        "inputTokens": prompt,
        "cacheReadTokens": cached,
        "cacheRate": stats.ratio(cached, prompt),
        "callCacheRate": {
            "median": stats.percentile(per_call, 0.5),
            "p10": stats.percentile(per_call, 0.1),
        },
    }


async def _daily(session: AsyncSession, since, until, buckets) -> list[dict]:
    day = utc_day(AssistantGateCheck.created_at)
    rows = (
        await session.execute(
            select(
                day,
                func.count(),
                func.count().filter(AssistantGateCheck.verdict == "asked"),
                func.coalesce(func.sum(AssistantGateCheck.cost_usd), 0.0),
            )
            .where(
                AssistantGateCheck.created_at >= since,
                AssistantGateCheck.created_at < until,
            )
            .group_by(day)
        )
    ).all()
    return dense_series(
        buckets,
        {
            "checks": {r[0].date(): r[1] for r in rows},
            "asked": {r[0].date(): r[2] for r in rows},
            "costUsd": {r[0].date(): float(r[3]) for r in rows},
        },
    )


async def load(session: AsyncSession, *, days: int) -> dict:
    since, until, buckets = utc_day_window(days)
    return {
        "days": days,
        "gate": await _gate(session, since, until),
        "answers": await _answers(session, since, until),
        "daily": await _daily(session, since, until, buckets),
    }
