"""Tell the alert channel when a model's spend cap is 80% used, and when it is reached.

spend_cap.py refuses every call to an upstream once the gateway's ledger says it
has cost its cap, and the admin models page shows how far along it is; neither
tells anyone who is not looking. A cap that trips on a Saturday is found when
users report the model refusing. So a clock reads the same ledger the cap is
enforced from and says so on the alert webhook (`app.core.alerting`), once when
an upstream crosses 80% of its cap and once when it reaches it.

Once means once per crossing, not once per hour: what has been said is kept in
Redis per upstream, with the cap it was said against. Raising the cap is a new
cap, so the next crossing is news again. Without Redis nothing is remembered
here, and `alerting.send`'s own repeat windows (an hour, then four, then a day)
are what keeps it from being said every tick.
"""

import logging
from collections.abc import Callable

from redis.asyncio import Redis

from app.core import alerting
from app.core.config import settings
from app.core.redis import get_redis_client
from app.domain.agent.gateway_admin import AdminModel, GatewayAdmin, UsageWindow
from app.domain.agent.gateway_models_helpers import LEDGER_START, _spend_cap
from app.domain.day_windows import utc_now

logger = logging.getLogger(__name__)

# Fractions of the cap worth a message, highest first.
LEVELS = (1.0, 0.8)
KEY_PREFIX = "cheese:spend-cap-alert:"


def caps_by_upstream(models: list[AdminModel]) -> dict[str, tuple[float, list[str]]]:
    """Upstream model -> (its cap, the model names served from it). The page
    writes one cap to every source of an upstream; were two to differ, the
    lowest is the one that refuses first."""
    out: dict[str, tuple[float, list[str]]] = {}
    for model in models:
        for source in model.sources:
            cap = _spend_cap(source)
            if cap is None:
                continue
            was, names = out.get(source.upstream_model, (cap, []))
            if model.name not in names:
                names = [*names, model.name]
            out[source.upstream_model] = (min(was, cap), names)
    return out


def level(used: float, cap: float) -> float:
    """The highest of `LEVELS` that ``used`` has reached, or 0."""
    return next((mark for mark in LEVELS if used >= cap * mark), 0.0)


async def _said(redis: Redis | None, upstream: str, cap: float) -> float:
    if redis is None:
        return 0.0
    try:
        raw = await redis.get(KEY_PREFIX + upstream)
    except Exception:  # a broken Redis must not silence the alert
        logger.warning("spend cap alert state unavailable", exc_info=True)
        return 0.0
    if not raw:
        return 0.0
    # The client is made without decode_responses (`get_redis_client`), but a
    # caller may hand one that decodes.
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    said_cap, _, said_level = text.partition(":")
    try:
        return float(said_level) if float(said_cap) == cap else 0.0
    except ValueError:
        return 0.0


async def _remember(
    redis: Redis | None, upstream: str, cap: float, mark: float
) -> None:
    if redis is None:
        return
    try:
        await redis.set(KEY_PREFIX + upstream, f"{cap}:{mark}")
    except Exception:
        logger.warning("spend cap alert state not saved", exc_info=True)


def _send(
    upstream: str, names: list[str], used: float, cap: float, mark: float
) -> None:
    title = "模型已达消费上限，调用被拒绝" if mark >= 1.0 else "模型消费上限已用 80%"
    alerting.send(
        title,
        [
            f"{'、'.join(names)}（上游 {upstream}）",
            f"累计已用 ${used:.2f} / 上限 ${cap:.2f}（{used / cap:.0%}）",
            "在管理后台「模型」页调整上限",
        ],
        key=f"spend-cap:{upstream}:{cap}:{mark}",
    )


async def check(
    admin: GatewayAdmin, redis: Redis | None, *, today: str
) -> list[tuple[str, float]]:
    """Read every cap and the ledger once; say each new crossing. Returns the
    (upstream, level) pairs said this time."""
    caps = caps_by_upstream(await admin.models())
    if not caps:
        return []
    ledger: UsageWindow = await admin.usage(LEDGER_START.isoformat(), today)
    said: list[tuple[str, float]] = []
    for upstream, (cap, names) in caps.items():
        row = ledger.by_upstream.get(upstream)
        used = row.spend_usd if row is not None else 0.0
        mark = level(used, cap)
        before = await _said(redis, upstream, cap)
        if mark > before:
            _send(upstream, names, used, cap, mark)
            said.append((upstream, mark))
        if mark != before:
            await _remember(redis, upstream, cap, mark)
    return said


async def sweep(
    *, redis: Callable[[], Redis | None] = get_redis_client
) -> list[tuple[str, float]]:
    """The clock's tick. A deployment without gateway admin credentials has no
    caps to read, and that is not an error (`admin_models.get_gateway_admin`)."""
    if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
        return []
    admin = GatewayAdmin(
        settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
    )
    return await check(admin, redis(), today=utc_now().date().isoformat())
