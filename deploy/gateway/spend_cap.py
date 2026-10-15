"""Refuse a deployment's calls once its upstream has been charged a set total.

A deployment declares a lifetime cap, in USD, under `model_info`:

    cheese_spend_cap_usd: 1000

and every call to it is refused once the gateway's own ledger says calls to
its upstream model (`litellm_params.model`) have cost that much, summed over
all time. The ledger is `LiteLLM_DailyUserSpend` in the gateway database, the
daily totals the backend also drains; it survives releases and restarts,
which LiteLLM's own deployment budgets (`max_budget` / `budget_duration` in
`litellm_params`) do not here: their counter lives in the router's cache, and
this router keeps no Redis (README), so each release would start it at zero.

The total is read at most once a minute and the calls made since are added
on, so a cap is overshot by at most the calls already running when it is
reached. The cost is the one this file prices calls at, so the cap holds the
upstream's bill only as far as those prices do.

Installed into site-packages as `cheese_spend_cap` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import time

import litellm
from litellm.integrations.custom_logger import CustomLogger

CAP = "cheese_spend_cap_usd"
REFRESH_S = 60.0


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
        # deployment id -> (read at, ledger total then, cost of calls since).
        # Keyed by the deployment: the success callback is handed the model
        # name without its provider prefix, the deployment hook with it.
        self._seen: dict[str, tuple[float, float, float]] = {}

    async def _spent(self, deployment: str, model: str) -> float:
        now = self._clock()
        seen = self._seen.get(deployment)
        if seen is None or now - seen[0] >= REFRESH_S:
            seen = (now, await self._spend_of(model), 0.0)
            self._seen[deployment] = seen
        return seen[1] + seen[2]

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
        if declared_cap(info) is None or deployment not in self._seen:
            return
        read_at, total, since = self._seen[deployment]
        cost = kwargs.get("response_cost") or 0.0
        self._seen[deployment] = (read_at, total, since + float(cost))


handler = SpendCap()
