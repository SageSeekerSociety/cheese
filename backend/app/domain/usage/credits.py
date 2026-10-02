"""Compute-credit conversion + platform copy (spec §9.1 机构提供算力).

Credits are the institution-facing unit; a call costs what its model's rates
make of its tokens, in USD, and one credit is ``llm_gateway_credit_usd`` of
that. Every route uses this one yardstick (#2397).

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
from app.domain.block.notice_text import say

# Posted into the topic 现场 when a turn is refused for lack of credits. The
# room draws the severity from `meta`; the line itself just says what happened.
CREDITS_EXHAUSTED_EVENT = say("creditsExhausted")


def credits_event(reason: object) -> str:
    """The room line for a turn the credits refused: the refusal's own
    sentence (when it resets) if there is one, else the general one."""
    if isinstance(reason, str) and reason:
        return say("creditsRefused", reason=reason)
    return CREDITS_EXHAUSTED_EVENT


CREDITS_EXHAUSTED_META = notice(
    EVENT_TURN_FAILED,
    severity=SEVERITY_ERROR,
    who=WHO_HUMAN,
    detail=say("creditsExhaustedDetail"),
    detail_label=say("labelNextStep"),
)


def spend_to_credits(cost_usd: float) -> float:
    """The credits a call that cost ``cost_usd`` burns: the same quotient the
    gateway's budget brake uses (both are ``credits × price``). Nothing can be
    charged on a deployment that sets no price per credit."""
    price = settings.llm_gateway_credit_usd
    if not price or cost_usd <= 0:
        return 0.0
    return cost_usd / price
