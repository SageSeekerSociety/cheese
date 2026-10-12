"""What a feature's tokens cost, priced at the gateway's own rates — an estimate.

**Why an estimate and not the real spend.** The gateway does keep the real
number, but the only endpoint that answers per window, ``/spend/logs/v2``, hands
back raw rows a page at a time (see ``domain/agent/gateway.py``). Summing them
per day over a 90-day window would read every row the feature logged in those
90 days on each page load. ``/key/info``
is cheap but answers the wrong question: a lifetime cumulative, with no window
to divide it by. So the page multiplies the tokens the feature already recorded
by the rates the gateway would charge for them, and says on its face that the
number is an estimate. Wrong by the cache discounts the gateway applies, right
about the order of magnitude, and instant.

**Where the rates come from.** The same ``/model/info`` the model ledger reads,
because a rate written anywhere else is a second copy of the truth that drifts.
Both places a rate can live are read (``litellm_params`` and ``model_info``),
because this deployment uses both — the gateway reads them the same way, which
is the rule ``gateway.price_is_set`` states.

**Missing rates are not zero.** A model the gateway prices at nothing
contributes nothing to the total, and its tokens are reported separately as
``unpriced_tokens``: the page has to be able to say 「这个数没算全」 instead of
quietly under-reporting the spend by whatever share that model carries.

**Unreachable gateway means no number at all.** ``usd`` stays ``None`` and the
page draws a dash — the same discipline as everywhere else in the admin:
「没读到」 and 「是零」 must not look alike.
"""

import logging
import time
from collections.abc import Mapping
from typing import NamedTuple

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# How long a rate table is reused. Rates change when someone edits a model on
# the gateway, which is a rare, deliberate act; a few minutes of staleness costs
# nothing and keeps an admin flipping between 7 and 90 days from re-reading
# ``/model/info`` on every switch.
_TTL_S = 300
_TIMEOUT_S = 8.0


class Rate(NamedTuple):
    """USD per token. ``input`` and ``output`` are > 0; a cached prompt token is
    billed at ``cache_read`` and a token written to the cache at
    ``cache_write``, each ``input`` when the gateway names no price for it; a
    token written to the one-hour cache at ``cache_write_1h``, ``cache_write``
    when the gateway names none."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float | None = None


# ``model_name`` -> its rates.
_cache: tuple[float, dict[str, Rate] | None] | None = None


def _rate(sources: tuple[object, ...], field: str) -> float:
    """The first positive rate among the places LiteLLM may carry it."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            if value > 0:
                return float(value)
    return 0.0


def _price(sources: tuple[object, ...], field: str) -> float | None:
    """The first rate among the places LiteLLM may carry it, zero included: a
    cache price of nothing is a price. None when neither place names one."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            if value >= 0:
                return float(value)
    return None


def _rates_from_info(payload: object) -> dict[str, Rate]:
    """``/model/info`` rows -> rates, keyed by the name this platform uses.

    A model priced on one side only is left out entirely rather than priced at
    zero on that side: half a rate is the same silent under-count as no rate,
    and it would be the harder one to notice.
    """
    rates: dict[str, Rate] = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return rates
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("model_name")
        if not isinstance(name, str) or not name:
            continue
        info = row.get("model_info")
        params = row.get("litellm_params")
        inp = _rate((params, info), "input_cost_per_token")
        out = _rate((params, info), "output_cost_per_token")
        if inp > 0 and out > 0:
            cache_read = _price((params, info), "cache_read_input_token_cost")
            cache_write = _price((params, info), "cache_creation_input_token_cost")
            cache_write_1h = _price(
                (params, info), "cache_creation_input_token_cost_above_1hr"
            )
            write = inp if cache_write is None else cache_write
            rates[name] = Rate(
                inp,
                out,
                inp if cache_read is None else cache_read,
                write,
                write if cache_write_1h is None else cache_write_1h,
            )
    return rates


async def model_rates(
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Rate] | None:
    """The gateway's rates, cached briefly; ``None`` when it cannot be asked."""
    global _cache
    if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
        return None
    if _cache is not None and time.monotonic() - _cache[0] < _TTL_S:
        return _cache[1]
    base = (settings.llm_gateway_admin_base or "").rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S), transport=transport
        ) as client:
            response = await client.get(
                f"{base}/model/info",
                headers={"Authorization": f"Bearer {settings.llm_gateway_admin_key}"},
            )
            response.raise_for_status()
            rates = _rates_from_info(response.json())
    except Exception:  # noqa: BLE001 — an unreachable gateway is unknown, not free
        logger.warning("reading model rates from the gateway failed", exc_info=True)
        return None
    _cache = (time.monotonic(), rates)
    return rates


def forget() -> None:
    """Drop the cached rates (tests, and anyone who just edited a price)."""
    global _cache
    _cache = None


async def estimate(
    tokens_by_model: dict[str, tuple[int, int]],
    *,
    rates: Mapping[str, tuple[float, ...]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Price ``{model: (prompt_tokens, completion_tokens)}`` at gateway rates.

    Returns ``{usd, source, unpriced_tokens}``: ``usd`` is ``None`` when no rate
    table could be read at all, ``source`` says which of the two answers this is
    (``"estimated"`` / ``"unavailable"``), and ``unpriced_tokens`` counts the
    tokens no rate covered so the page can qualify the number it prints.
    """
    total_tokens = sum(
        prompt + completion for prompt, completion in tokens_by_model.values()
    )
    table = rates if rates is not None else await model_rates(transport)
    if table is None:
        return {"usd": None, "source": "unavailable", "unpriced_tokens": total_tokens}
    usd = 0.0
    unpriced = 0
    for model, (prompt, completion) in tokens_by_model.items():
        rate = table.get(model)
        if rate is None:
            unpriced += prompt + completion
            continue
        usd += prompt * rate[0] + completion * rate[1]
    return {"usd": usd, "source": "estimated", "unpriced_tokens": unpriced}
