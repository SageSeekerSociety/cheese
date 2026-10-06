"""智能命名的数据页: what the automatic titles cost, and how well they land.

Naming is a platform job that runs off the agent's turn
(``domain/room_task/naming.py``): a small model gives a task opened without a
title its first one, then may calibrate it once, then follows the task only
when its direction changes.
The question this page answers is 「这件事值不值得继续开着」 — what it spends,
whether the calls work, and whether the names survive.

Two sources, and **no new tracking** for either:

* the **gateway**, for what naming spends. Naming calls the gateway on a virtual
  key of its own (``KeySpec.alias = "topic-naming"``), and the gateway books
  requests, failures, tokens and spend per key per day. The model ledger reads
  the same two endpoints, so this page adds no call the deployment does not
  already make elsewhere.
* ``task_titles``, for what naming does. Every title a task has had is a row
  with its source and its reason, which is enough for 「改了多少次」「哪个阶段
  动的」「人后来改掉多少」 — the last one being the only quality signal the
  tables keep, and the reason that table exists at all.

What this page **cannot** show, and says so rather than guessing: whether the
model produced a usable answer. A truncated answer or a broken JSON wrapper is
an HTTP 200 as far as the gateway is concerned, and naming writes those reasons
to its log, not to a table. So there is no 「命名成功率」 here — there is the
gateway's call success rate, labelled as exactly that.

Reachable gateway: the numbers are the gateway's own. Unreachable: they are
``None`` and the page draws a dash, never 0 (same discipline as ``pricing``).

A gateway that answers but carries **no naming key** is a third state, and it is
not zero either: nothing was booked under a key that does not exist, so there is
nothing to report about its spend. ``GatewayRead.source`` says which of the three
it was, so the page can say 「密钥不存在」 rather than 「读不到网关」 — and never
prints 0 for either.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent.gateway_admin import AdminKey, GatewayAdmin, ModelUsage
from app.domain.platform_stats.windows import dense_series, utc_day_window
from app.domain.room_task.models import TaskTitle, TaskTitleSource

logger = logging.getLogger(__name__)

FEATURE_ID = "task-naming"
TITLE = "智能命名"
SUMMARY = "任务标题的自动命名：花了多少、几个阶段在动、人后来改掉了多少"

# The key naming mints for itself (``domain/room_task/naming.py``, ``KeySpec.alias``).
# Only traffic on this key is this feature's — every other key on the gateway
# belongs to someone else.
KEY_ALIAS = "topic-naming"

# The three stages a title can be written at (``naming.Stage``), in the order a
# task meets them. The reason column of an automatic row holds one of these, or
# ``proposal`` for the title an AI teammate proposed the task under.
STAGES = ("name", "calibrate", "follow")

# The naming key never changes, and the window only moves at midnight, so a
# minute of staleness costs nothing and keeps someone flipping between 7 and 90
# days from re-reading the gateway every time.
_USAGE_TTL_S = 60.0
_KEY_TTL_S = 600.0

_ZERO = ModelUsage(
    spend_usd=0.0,
    requests=0,
    failed_requests=0,
    prompt_tokens=0,
    completion_tokens=0,
    cache_read_tokens=0,
    total_tokens=0,
)


@dataclass(frozen=True)
class GatewayRead:
    """What the gateway said about the naming key, in one of three states.

    ``source`` is what the page branches on:

    * ``gateway`` — the key is there and ``usage`` is its numbers for the window.
      Zero calls then really is zero: the gateway booked nothing on this key.
    * ``no-key`` — the gateway answered and has no key with this alias. Naming
      was never minted one, or the alias changed. **``usage`` is None**: there is
      no key to have booked anything under, so a 0 here would be a claim we
      cannot make.
    * ``unavailable`` — we could not ask at all (not configured, or the call
      failed). ``usage`` is None for the same reason.
    """

    source: str
    usage: ModelUsage | None
    key: AdminKey | None


# ``(monotonic, start, end, read)`` for a whole answered window, and
# ``(monotonic, key-or-None)`` for the key lookup on its own (it is asked before
# the usage, and outlives it: the key's identity never changes).
_usage_cache: tuple[float, str, str, GatewayRead] | None = None
_key_cache: tuple[float, AdminKey | None] | None = None


def forget() -> None:
    """Drop both cached answers (tests, and anyone who just changed a budget)."""
    global _usage_cache, _key_cache
    _usage_cache = None
    _key_cache = None


async def _naming_key(admin: GatewayAdmin) -> AdminKey | None:
    """The gateway's row for the naming key, or None when it was never minted."""
    global _key_cache
    if _key_cache is not None and time.monotonic() - _key_cache[0] < _KEY_TTL_S:
        return _key_cache[1]
    keys = await admin.keys()
    found = next((key for key in keys if key.alias == KEY_ALIAS), None)
    _key_cache = (time.monotonic(), found)
    return found


async def _gateway_usage(
    start: date, end: date, transport: httpx.AsyncBaseTransport | None = None
) -> GatewayRead:
    """The naming key's usage over the window, from the gateway.

    See ``GatewayRead`` for the three states. The one that matters here: a
    gateway that answered without this key on it yields ``no-key`` with no
    usage, **not** ``_ZERO`` — 「这把密钥不存在」 and 「这把密钥这个窗口没花钱」
    are different sentences, and only the second one is a zero the page can show.

    A key that exists but had no traffic in the window is absent from ``by_key``
    rather than present as zero; both mean zero here. The lookup trusts that the
    gateway's per-key breakdown covers platform keys too — it is built the same
    way as the project keys the model ledger reads.
    """
    global _usage_cache
    if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
        return GatewayRead("unavailable", None, None)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    if (
        _usage_cache is not None
        and _usage_cache[1] == start_iso
        and _usage_cache[2] == end_iso
        and time.monotonic() - _usage_cache[0] < _USAGE_TTL_S
    ):
        return _usage_cache[3]
    admin = GatewayAdmin(
        settings.llm_gateway_admin_base,
        settings.llm_gateway_admin_key,
        transport=transport,
    )
    try:
        key = await _naming_key(admin)
        if key is None:
            # 没铸过这把 key，就没有它的用量可问：连 usage 那次请求都不发，
            # 也不能报零。
            read = GatewayRead("no-key", None, None)
        else:
            window = await admin.usage(start_iso, end_iso)
            read = GatewayRead("gateway", window.by_key.get(key.key_hash, _ZERO), key)
    except Exception:  # noqa: BLE001 — an unreachable gateway is unknown, not zero
        logger.warning(
            "reading topic naming usage from the gateway failed", exc_info=True
        )
        return GatewayRead("unavailable", None, None)
    _usage_cache = (time.monotonic(), start_iso, end_iso, read)
    return read


async def _titles(session: AsyncSession, since: datetime, until: datetime) -> dict:
    """Every title written in the window, read as the things the page counts.

    One query, computed in the process: renaming a task is rare, so a window
    holds a handful of rows even on a busy deployment, and the 「人后来改掉了」
    count needs the rows in order per task — which is what a window function
    would say and six lines of Python say without one.
    """
    rows = (
        await session.execute(
            select(
                TaskTitle.task_id,
                TaskTitle.source,
                TaskTitle.reason,
                TaskTitle.created_at,
            )
            .where(TaskTitle.created_at >= since, TaskTitle.created_at < until)
            .order_by(TaskTitle.task_id, TaskTitle.created_at)
        )
    ).all()

    by_stage = dict.fromkeys(STAGES, 0)
    auto_by_day: dict[date, int] = {}
    person_by_day: dict[date, int] = {}
    # Task -> the position of its first automatic (and first person's) title in
    # this window. Ordered by ``created_at``, so 「first」 is 「earliest」.
    first_auto: dict[uuid.UUID, int] = {}
    first_person: dict[uuid.UUID, int] = {}
    auto_total = 0
    person_total = 0
    for position, (task, source, reason, created) in enumerate(rows):
        day = created.astimezone(UTC).date()
        if source == TaskTitleSource.auto:
            auto_total += 1
            if reason in by_stage:
                by_stage[reason] += 1
            auto_by_day[day] = auto_by_day.get(day, 0) + 1
            first_auto.setdefault(task, position)
        elif source == TaskTitleSource.human:
            person_total += 1
            person_by_day[day] = person_by_day.get(day, 0) + 1
            first_person.setdefault(task, position)
        # Any other source is neither: 「人改掉」和「平台命名」是这一页仅有的两件事，
        # 一个不认识的来源必须是不可见的，而不是被算进「人」那一栏。
    # A task counts as 「改掉了」 when a person wrote a title after the automatic
    # one, both inside the window. A person's title that came *first* is someone
    # naming the task themselves, which is not this feature's loss.
    overridden = sum(
        1
        for task, position in first_auto.items()
        if first_person.get(task, -1) > position
    )
    return {
        "by_stage": by_stage,
        "auto_by_day": auto_by_day,
        "person_by_day": person_by_day,
        "auto_total": auto_total,
        "person_total": person_total,
        "named": len(first_auto),
        "overridden": overridden,
    }


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    """``None`` when the denominator is empty — a rate with no base is not 0."""
    if not denominator or numerator is None:
        return None
    return numerator / denominator


def _iso(day: date) -> str:
    return day.isoformat()


async def load(
    session: AsyncSession,
    *,
    days: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """The whole page for one window. See the module docstring for the shape."""
    since, until, buckets = utc_day_window(days)
    titles = await _titles(session, since, until)
    read = await _gateway_usage(buckets[0], buckets[-1], transport)
    usage, key = read.usage, read.key

    calls = usage.requests if usage else None
    failed = usage.failed_requests if usage else None
    return {
        "id": FEATURE_ID,
        "title": TITLE,
        "summary": SUMMARY,
        "days": days,
        "start": _iso(buckets[0]),
        "end": _iso(buckets[-1]),
        "numbers": {
            "calls": {
                "value": calls,
                "failed": failed,
                # The gateway's own success rate: a request that reached the
                # model and came back at all. It is not 「命名成功率」 — see the
                # module docstring.
                "success_rate": (
                    None if not calls else (calls - (failed or 0)) / calls
                ),
            },
            "tokens": {
                "value": usage.total_tokens if usage else None,
                "prompt": usage.prompt_tokens if usage else None,
                "completion": usage.completion_tokens if usage else None,
                "cache_read": usage.cache_read_tokens if usage else None,
            },
            "cost": {
                "usd": usage.spend_usd if usage else None,
                # 「gateway」= 网关自己记的账；「no-key」= 网关答了话但上面没有这把
                # key；「unavailable」= 读不到网关。后两种上面的数都是 None：页面
                # 分开说这两句话，都不画 0。
                "source": read.source,
                # The key's budget is the thing that would silently stop naming
                # (the gateway refuses once it is spent), so it belongs next to
                # the spend rather than in a config file nobody reads.
                "budget_usd": key.max_budget if key else None,
                "budget_duration": key.budget_duration if key else None,
                # 网关自己给这把 key 记的花费，**不是上面那个窗口的**。平台铸这把
                # key 时给的额度周期是固定的 30 天（``service_keys`` 写死
                # ``budget_duration: "30d"``），所以页面把它连同 ``budget_duration``
                # 一起画，不写成「累计」——那读起来像是这一页选的窗口，而 2026-10-01
                # 在 dev 上它（$0.0151）离 7 天窗口（$0.2638）差了整整一个量级。
                "key_spend_usd": key.spend if key else None,
            },
            "renames": {
                "value": titles["auto_total"],
                "name": titles["by_stage"]["name"],
                "calibrate": titles["by_stage"]["calibrate"],
                "follow": titles["by_stage"]["follow"],
            },
            "person_edits": {"value": titles["person_total"]},
            "overridden": {
                "value": titles["overridden"],
                "named": titles["named"],
                "share": _ratio(titles["overridden"], titles["named"]),
            },
        },
        # Two lines over the same unit (titles written that day): the platform's
        # moves and the people's. Days with nothing are filled with 0 by
        # ``dense_series``, from the window and never from the days that happen
        # to have rows.
        "trend": dense_series(
            buckets,
            {"auto": titles["auto_by_day"], "person": titles["person_by_day"]},
        ),
    }
