"""Compute-credit conversion + platform copy (spec §9.1 机构提供算力).

Credits are the institution-facing unit; tokens are what turns actually burn.
The conversion rate lives in settings (compute_credit_tokens, default 1 credit
= 10k tokens) so a deployment can re-price without code changes.

The exhaustion message is PLATFORM copy posted as a structured system event —
never words put in 芝士's mouth (CLAUDE.md 硬性禁止 #4).
"""

from app.core.config import settings
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    notice,
)

# Posted into the topic 现场 when a turn is refused for lack of credits. The
# room draws the severity from `meta`; the line itself just says what happened.
CREDITS_EXHAUSTED_EVENT = "可用的 tokens 额度已用完，这轮没有执行"
CREDITS_EXHAUSTED_META = notice(
    EVENT_TURN_FAILED,
    severity=SEVERITY_ERROR,
    who=WHO_HUMAN,
    detail="请联系团队管理员或额度发放方补充额度。",
    detail_label="怎么恢复",
)


def tokens_to_credits(total_tokens: int) -> float:
    """Fold a turn's token usage into compute credits."""
    rate = max(1, settings.compute_credit_tokens)
    return total_tokens / rate


def usage_to_credits(usage, *, spend_priced: bool) -> float:
    """Credits a turn actually burns.

    ``spend_priced`` (gateway-routed turn + ``llm_gateway_credit_usd`` set):
    convert the REAL cost (the gateway's spend, cache discounts included) at the
    configured price-per-credit — cached input burns proportionally less, and
    the app-layer credit gate lines up exactly with the gateway's budget brake
    (both are ``credits × price``). Otherwise: the flat token rate."""
    price = settings.llm_gateway_credit_usd
    if spend_priced and price and usage.cost_usd > 0:
        return usage.cost_usd / price
    return tokens_to_credits(usage.input_tokens + usage.output_tokens)
