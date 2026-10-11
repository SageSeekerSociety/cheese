"""Compute-credit conversion + platform copy (spec §9.1 机构提供算力).

A credit is a hundredth of a US dollar of model spend, and people see it as a
点: a call costs what its model's rates make of its tokens, in USD, over
``CREDIT_USD``. Every route, the gateway's budget brake and every pack, plan
and usage row use this one yardstick (#2397).

The exhaustion message is PLATFORM copy posted as a structured system event —
never words put in 芝士's mouth (CLAUDE.md 硬性禁止 #4).
"""

from app.core.sentences import say
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    notice,
)

#: USD per credit (one 点).
CREDIT_USD = 0.01

# Posted into the topic 现场 when a turn is refused for lack of credits. The
# room draws the severity from `meta`; the line itself just says what happened.
CREDITS_EXHAUSTED_EVENT = say("creditsExhausted")


def credits_event(reason: object) -> str:
    """The room line for a turn the credits refused: the refusal's own
    sentence (when it resets) if there is one, else the general one."""
    if isinstance(reason, str) and reason:
        return say("creditsRefused", reason=reason)
    return CREDITS_EXHAUSTED_EVENT


# How a refusal for spent credits reads by the time it ends a turn: the
# metering proxy prefixes every one with this (deploy/metering-proxy/
# billing_addon.py), and LiteLLM words its own `max_budget` brake the second
# way when a caller reaches the gateway directly.
_CREDIT_REFUSALS = ("cheese project budget:", "Budget has been exceeded")


def refused_for_credits(text: str | None) -> bool:
    """Whether a turn's failure is the credits refusing it, whichever brake
    did: waiting or retrying cannot help, only more credits can."""
    return bool(text) and any(marker in text for marker in _CREDIT_REFUSALS)


CREDITS_EXHAUSTED_META = notice(
    EVENT_TURN_FAILED,
    severity=SEVERITY_ERROR,
    who=WHO_HUMAN,
    detail=say("creditsExhaustedDetail"),
    detail_label=say("labelNextStep"),
)


def spend_to_credits(cost_usd: float) -> float:
    """The credits a call that cost ``cost_usd`` burns: the same quotient the
    gateway's budget brake uses (both are ``credits × CREDIT_USD``)."""
    if cost_usd <= 0:
        return 0.0
    return cost_usd / CREDIT_USD
