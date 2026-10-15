"""Refuse a deployment's calls once its upstream has been charged a set total.

A deployment declares a lifetime cap, in USD, under `model_info`:

    cheese_spend_cap_usd: 1000

and every call to it is refused once the gateway's own ledger says calls to
its upstream model (`litellm_params.model`) have cost that much, summed over
all time. The ledger is `LiteLLM_DailyUserSpend` in the gateway database, the
daily totals the backend also drains; it survives releases and restarts.
LiteLLM's own deployment budgets (`max_budget` / `budget_duration` in
`litellm_params`) keep their counter in the router's cache, which is
`litellm-redis` here, and that Redis runs without persistence (README): a
Redis restart would start the count at zero, and a budget is a window that
resets, not a lifetime total.

The total is read at most once a minute and the calls made since are added
on. LiteLLM writes the daily totals in batches every 10-15 s, so a call that
finished up to FLUSH_LAG_S before a read keeps counting after it, even if the
read already includes it: the cap errs early, never late, and is overshot by
at most the calls already running when it is reached. The cost is the one
config.yaml prices calls at, so the cap holds the upstream's bill only as far
as those prices do.

Installed into site-packages as `cheese_spend_cap` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import time

import litellm
from litellm.integrations.custom_logger import CustomLogger

CAP = "cheese_spend_cap_usd"
REFRESH_S = 60.0
# LiteLLM's daily-spend batch writer runs every PROXY_BATCH_WRITE_AT (10 s)
# plus up to 5 s of jitter; a little more, so no finished call falls between.
FLUSH_LAG_S = 20.0


async def ledger_spend(model: str) -> float:
    """What the gateway's ledger says calls to ``model`` have cost, all time."""
    from litellm.proxy.proxy_server import prisma_client  # deferred-import: set at proxy start-up

    if prisma_client is None:
        raise RuntimeError("the gateway database is not connected")
    rows = await prisma_client.db.query_raw(
        'SELECT COALESCE(SUM(spend), 0) AS spend FROM "LiteLLM_DailyUserSpend" WHERE model = $1',
        model,
    )
    return float(rows[0]["spend"]) if rows else 0.0


def declared_cap(model_info: object) -> float | None:
    cap = model_info.get(CAP) if isinstance(model_info, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, int | float) or cap <= 0:
        return None
    return float(cap)


class SpendCap(CustomLogger):
    def __init__(self, spend_of=ledger_spend, clock=time.monotonic):
        super().__init__()
        self._spend_of = spend_of
        self._clock = clock
        # deployment id -> (read at, ledger total then). Keyed by the
        # deployment: the success callback is handed the model name without
        # its provider prefix, the deployment hook with it.
        self._read: dict[str, tuple[float, float]] = {}
        # deployment id -> [(finished at, cost)] of its recent calls.
        self._calls: dict[str, list[tuple[float, float]]] = {}

    async def _spent(self, deployment: str, model: str) -> float:
        now = self._clock()
        read = self._read.get(deployment)
        if read is None or now - read[0] >= REFRESH_S:
            read = (now, await self._spend_of(model))
            self._read[deployment] = read
        # Calls the ledger may not hold yet: since the read, or just before it.
        recent = [c for c in self._calls.get(deployment, []) if c[0] > read[0] - FLUSH_LAG_S]
        self._calls[deployment] = recent
        return read[1] + sum(cost for _, cost in recent)

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info")
        cap = declared_cap(info)
        if cap is None:
            return None
        model = kwargs.get("model")
        deployment = info.get("id")
        if not isinstance(model, str) or not model or not isinstance(deployment, str):
            return None
        spent = await self._spent(deployment, model)
        if spent >= cap:
            raise litellm.BudgetExceededError(
                current_cost=spent,
                max_budget=cap,
                message=f"{model} has reached its spend cap: ${spent:.2f} of ${cap:.2f}",
            )
        return None

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        info = (kwargs.get("litellm_params") or {}).get("model_info")
        deployment = info.get("id") if isinstance(info, dict) else None
        if declared_cap(info) is None or not isinstance(deployment, str):
            return
        cost = float(kwargs.get("response_cost") or 0.0)
        self._calls.setdefault(deployment, []).append((self._clock(), cost))


handler = SpendCap()
