"""Take an upstream out of rotation while the platform's account with it has no money.

A provider whose account is out of credit keeps refusing until somebody tops
the account up, which takes minutes to days. The router does not know that:
it cools a deployment down by HTTP status alone, so DeepSeek's
`400 Insufficient Balance` was not cooled down at all and Zhipu's
`429 [1113][余额不足…]` for the router's 5 seconds. Every request that
overflowed from RUC onto such a row failed on it (2026-10-22: 107 refusals
from DeepSeek in three days), and a request that found only that row left
failed with the provider's raw 400 although RUC freed a slot seconds later.

What this module does, once per request, when the router has finished with it
(`install` wraps the router's outermost `async_function_with_fallbacks`):

- `out_of_credit` reads every refusal the request met: the attempts the router
  walked past (its `previous_models` record) and the one it gave up on. One
  classifier for the whole gateway; `provider_http_timing.py` labels its log
  records with it too.
- Each row that refused for credit goes into the router's own cooldown for
  `COOLDOWN_S`, recorded under status 402 (Payment Required) so the reason can
  be read back. The router skips a cooling row and walks on to the next
  `order` as for any other cooldown; once the cooldown ends, the next request
  tries the account again, so a top-up is picked up by itself.
- The rows are named in the `HEADER` response header. The metering proxy
  reports them to the backend's error intake, which tells the administrators
  once per row (`billing_addon._report_out_of_credit_rows`, `backend_log`). A
  row refuses about once per cooldown, so that is also how often it is named.
- A request that failed because rows ran out of credit gets an answer that
  says what to do next. When every row of its model group is cooling down for
  credit: one 402 whose message starts with `CODE`, saying the platform's own
  account ran dry, not the caller's budget; Claude Code does not retry a 402,
  and the backend reads `CODE` to tell the room the administrators have it.
  When a row that is not out of credit remains: a 429 with a Retry-After of
  that row's remaining cooldown (RUC's is seconds), the answer the router
  itself gives when every row is cooling. Claude Code retries a 429 after
  Retry-After, and the retry reaches the live row. Waiting is left to that
  retry, which is already there and bounded, rather than to a queue held open
  in the gateway.

Why at the end of the request rather than in a failure callback: on the
Messages path LiteLLM does not run failure callbacks for an attempt reached by
fallback (its logging object logs one failure per request), which is exactly
where an overflow row is reached, and the Messages endpoint ignores an error a
post-call-failure hook returns. The router writes its own cooldown for every
attempt as it goes, so writing after it is also what keeps the long one.

Installed into site-packages as `cheese_provider_credit`: `install` is called
from the worker startup hooks (`LITELLM_WORKER_STARTUP_HOOKS` in the
Dockerfile) and `handler` is registered in config.yaml under
`litellm_settings.callbacks`.
"""

import logging
import math
import re
import time

from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("cheese.provider_credit")
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.StreamHandler())

#: How long a row whose account is out of credit stays out of rotation before
#: one request tries it again. A top-up takes a person minutes at the least;
#: each retry costs one refused call, which the caller is not handed.
COOLDOWN_S = 600.0
#: The status the cooldown is recorded under, which is how it is told apart
#: from a cooldown for any other reason; also the status of the answer when no
#: row of a model has credit left.
STATUS = 402
CODE = "cheese_upstream_credit_exhausted"
HEADER = "x-cheese-upstream-out-of-credit"
# Where the rows a request named wait for the response headers: the router's
# own metadata bucket, never the provider's `metadata` field.
_ROWS_KEY = "cheese_out_of_credit_rows"

# What each upstream says when the platform's account with it has no money,
# verbatim where it was seen on the dev gateway:
#   DeepSeek   400 {"error":{"message":"Insufficient Balance (request_id: …)"}}
#   Moonshot   429 "Your account … is suspended due to insufficient balance …",
#              type exceeded_current_quota_error
#   Zhipu      429 [1113][余额不足或无可用资源包,请充值。]
#   Volcengine 429 {"error":{"code":"SetLimitExceeded","message":"Your account
#              […] has reached the set usage limit … model service has been paused"}}
#   OpenCode   429 FreeUsageLimitError "Free usage limit reached. Subscribe …"
#   OpenAI     429 insufficient_quota; Anthropic 400 "credit balance is too low"
# and "quota exhausted" for the ones whose wording was never captured (MiMo).
# Not "quota exceeded": Gemini says that for its per-minute rate limit.
_OUT_OF_CREDIT = re.compile(
    r"insufficient[ _](balance|quota|funds)"
    r"|余额不足|请充值|\[1113\]|\brecharge\b"
    r"|exceeded_current_quota"
    r"|SetLimitExceeded"
    r"|FreeUsageLimitError"
    r"|credit balance is too low"
    r"|quota[ _]exhausted"
    r"|\bsuspended\b",
    re.I,
)


def out_of_credit(said: object) -> bool:
    """Whether an upstream's refusal says the account behind it has no money."""
    return isinstance(said, str) and bool(_OUT_OF_CREDIT.search(said))


def _bucket(kwargs: dict) -> dict | None:
    from litellm.litellm_core_utils.core_helpers import get_metadata_variable_name_from_kwargs

    bucket = kwargs.get(get_metadata_variable_name_from_kwargs(kwargs))
    return bucket if isinstance(bucket, dict) else None


def refused_rows(kwargs: dict, error: BaseException | None) -> list[str]:
    """The deployments that refused this request because their account is out
    of credit: the attempts the router walked past, and the one it gave up on."""
    attempts = [
        (crumb.get("deployment_id"), crumb.get("exception_string"))
        for crumb in ((_bucket(kwargs) or {}).get("previous_models") or [])
        if isinstance(crumb, dict)
    ]
    if error is not None:
        attempts.append((getattr(error, "failed_deployment_id", None), str(error)))
    rows: list[str] = []
    for deployment, said in attempts:
        if isinstance(deployment, str) and out_of_credit(said) and deployment not in rows:
            rows.append(deployment)
    return rows


def label(router, deployment: str) -> str:
    """How a person finds the row on the admin models page: its model, its
    upstream model and its id. Never its key, and never its address: RUC's is
    another team's to publish."""
    found = router.get_deployment(model_id=deployment)
    if found is None:
        return deployment
    return f"{found.model_name} {found.litellm_params.model} {deployment}"


async def _rows_left(router, group: str) -> tuple[bool, float | None]:
    """Whether every row of the group that takes requests is cooling down for
    credit, and if not, how soon the first of the others can take one (0 when
    one can now). A line switched off on the admin page (`model_info.blocked`)
    takes none, so it is not counted."""
    ids = [
        d["model_info"]["id"]
        for d in router.get_model_list(model_name=group) or []
        if isinstance((d.get("model_info") or {}).get("id"), str) and d["model_info"].get("blocked") is not True
    ]
    cooling = dict(await router.cooldown_cache.async_get_active_cooldowns(model_ids=ids, parent_otel_span=None))
    now = time.time()
    soonest = None
    for deployment in ids:
        value = cooling.get(deployment)
        if value is not None and str(value.get("status_code")) == str(STATUS):
            continue
        wait = 0.0 if value is None else max(0.0, value["timestamp"] + value["cooldown_time"] - now)
        soonest = wait if soonest is None else min(soonest, wait)
    return bool(ids) and soonest is None, soonest


async def answer(router, group: object, error: BaseException, rows: list[str]) -> BaseException | None:
    """What the caller gets instead of ``error``, or None to pass it on."""
    from litellm.proxy._types import ProxyException
    from litellm.types.router import RouterRateLimitError

    said = str(error)
    if not isinstance(group, str) or not (out_of_credit(said) or isinstance(error, RouterRateLimitError)):
        return None
    all_out, soonest = await _rows_left(router, group)
    named = {HEADER: "; ".join(label(router, row) for row in rows)} if rows else {}
    if all_out:
        return ProxyException(
            message=(
                f"{CODE}: the platform's own account with every provider serving {group} is out of "
                "credit. This is not your budget; the platform's administrators have to top the "
                "account up, and retrying before then cannot succeed."
            ),
            type="invalid_request_error",
            param=None,
            code=STATUS,
            headers=named,
        )
    if out_of_credit(said) and soonest is not None:
        wait = max(1, math.ceil(soonest))
        return ProxyException(
            message=(
                f"The provider this request overflowed to is out of credit; another one serving "
                f"{group} takes requests again in {wait} s."
            ),
            type="rate_limit_error",
            param=None,
            code=429,
            headers={"retry-after": str(wait), **named},
        )
    if named and isinstance(error, RouterRateLimitError):
        # The router's own "every row is cooling" answer, unchanged but for
        # naming the rows this request found empty on its way there.
        return ProxyException(
            message=said,
            type="rate_limit_error",
            param=None,
            code=429,
            headers={"retry-after": str(max(1, math.ceil(error.cooldown_time))), **named},
        )
    return None


def _cool(router, kwargs: dict, error: BaseException | None) -> list[str]:
    from litellm.router_utils.cooldown_cache import CooldownCache

    rows = refused_rows(kwargs, error)
    for deployment in rows:
        # The router has already cooled a 429 for its 5 s, and the in-memory
        # cache keeps a live entry's expiry when it is written again: the
        # 5 s entry has to go before the long one can take its place. Only the
        # in-memory copy: Redis takes the new expiry on the write below, and a
        # Redis that is down must not fail a request the write would not.
        router.cooldown_cache.in_memory_cache.delete_cache(CooldownCache.get_cooldown_cache_key(deployment))
        router.cooldown_cache.add_deployment_to_cooldown(
            model_id=deployment,
            original_exception=error or RuntimeError("out of credit"),
            exception_status=STATUS,
            cooldown_time=COOLDOWN_S,
        )
        logger.warning(
            "provider_credit out of credit, cooled for %ss: %s", int(COOLDOWN_S), label(router, deployment)
        )
    bucket = _bucket(kwargs)
    if rows and bucket is not None:
        bucket[_ROWS_KEY] = [label(router, row) for row in rows]
    return rows


def install() -> None:
    """Wrap the router's outermost call so every request ends through here."""
    from litellm.router import Router
    from litellm.router_utils.fallback_event_handlers import AttemptedFallbackTargets

    original = Router.async_function_with_fallbacks
    if getattr(original, "cheese_provider_credit", False):
        return

    async def with_credit(self, *args, **kwargs):
        if isinstance(kwargs.get("attempted_targets"), AttemptedFallbackTargets):
            # A fallback hop inside a request: the outermost call sees its result.
            return await original(self, *args, **kwargs)
        try:
            response = await original(self, *args, **kwargs)
        except Exception as error:
            rows = _cool(self, kwargs, error)
            replaced = await answer(self, kwargs.get("model"), error, rows)
            if replaced is None:
                raise
            raise replaced from error
        _cool(self, kwargs, None)
        return response

    with_credit.cheese_provider_credit = True  # type: ignore[attr-defined]
    Router.async_function_with_fallbacks = with_credit


class ProviderCredit(CustomLogger):
    """Names, on an answered request, the rows it found out of credit on the
    way. A failed request carries them on its error (`answer`): the Messages
    endpoint does not run this hook for a request that failed."""

    async def async_post_call_response_headers_hook(
        self, data, user_api_key_dict, response, request_headers=None, litellm_call_info=None
    ):
        rows = (_bucket(data) or {}).get(_ROWS_KEY)
        return {HEADER: "; ".join(rows)} if rows else None


handler = ProviderCredit()
