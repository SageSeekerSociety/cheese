"""Which failed turns the platform may start again on its own, and how soon.

A turn that ends on a model-service error leaves its seat idle until someone
pokes it. Measured on dev (project 「cheese 自建」, 2026-10-03..17): 803 failed
turns in 201 tasks, about 261 idle hours after them, and for the commonest
kinds — the provider limiting requests, a stream that ended with no events, a
5xx from a gateway — the next action came a median 23 minutes later (p90 nine
hours), always from a person or another agent. Those failures go away by
themselves; waiting and asking again is the whole remedy.

This module only answers the question; `turn_resume` schedules the answer.

**An allowlist, not the failure card's ``retryable``.** The card's flag means
"a person may press retry", and it is true for every failure the platform
cannot name — a crashed process, a session that never came up, a bug. Starting
those again on a timer re-runs the bug (the 2026-09-04 room flood). So a
failure resumes only when its text says one of the transient things below, and
anything that names a spent balance, a quota with no reset, a refused
credential or an invalid request is ruled out first, whatever else it says:
Claude Code wraps a gateway's refusal in its own "temporarily limiting
requests" line, so the outer words alone would read a suspended account as a
rate limit.

**Quota waits for its reset or does not resume.** A subscription whose accounts
are all cooling down resumes once the earliest account thaws (the metering
proxy's snapshot says when); a quota the service names with a reset time
("try again in 4 hours") resumes then. A quota with no reset to wait for is a
person's decision — top up, switch the project to a model outside the
subscription — and is left to them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.domain.agent.platform_failures import (
    RUNNER_REPLACED_CODE,
    is_provider_out_of_credit,
    is_request_refused,
)

#: One kind of transient failure, and how the platform waits it out.
#:
#: ``base_s`` doubles with each consecutive attempt up to ``cap_s``; a wait the
#: service stated itself replaces the backoff. ``attempts`` is how many resumes
#: one streak gets before the platform stops and leaves the seat to a person.
#: ``needs_reset`` kinds never back off blindly: without a stated reset they do
#: not resume.


@dataclass(frozen=True, slots=True)
class Policy:
    base_s: float
    cap_s: float
    attempts: int
    needs_reset: bool = False
    #: The platform cut the turn short, not the agent or its service failing:
    #: it neither counts towards a streak's ``attempts`` nor is held to them
    #: (`turn_resume.after_failure`).
    platform: bool = False


RATE_LIMITED: Final = "rate_limited"
OVERLOADED: Final = "overloaded"
STREAM_ENDED: Final = "stream_ended"
UNREACHABLE: Final = "unreachable"
EGRESS_OFFLINE: Final = "egress_offline"
SUBSCRIPTION_COOLING: Final = "subscription_cooling"
QUOTA_RESET: Final = "quota_reset"
#: The platform's upgrade replaced the runner a turn was running in
#: (``platform_failures.RUNNER_REPLACED_CODE``): the conversation goes on in
#: the new one, so the turn is started again at once, whatever the streak of
#: resumes before it (``Policy.platform``).
RUNNER_REPLACED: Final = "runner_replaced"

POLICIES: Final[dict[str, Policy]] = {
    RATE_LIMITED: Policy(base_s=60, cap_s=30 * 60, attempts=6),
    OVERLOADED: Policy(base_s=30, cap_s=15 * 60, attempts=6),
    STREAM_ENDED: Policy(base_s=30, cap_s=15 * 60, attempts=6),
    # A tunnel helper that went away may need a person after two waits.
    UNREACHABLE: Policy(base_s=60, cap_s=10 * 60, attempts=2),
    EGRESS_OFFLINE: Policy(base_s=120, cap_s=30 * 60, attempts=6),
    # Unreadable snapshot: the accounts' thaw is unknown, so ask rarely.
    SUBSCRIPTION_COOLING: Policy(base_s=15 * 60, cap_s=2 * 3600, attempts=3),
    QUOTA_RESET: Policy(base_s=0, cap_s=0, attempts=2, needs_reset=True),
    RUNNER_REPLACED: Policy(base_s=5, cap_s=5, attempts=0, platform=True),
}


@dataclass(frozen=True, slots=True)
class Resumable:
    kind: str
    #: Seconds the service itself said to wait, when it said; None otherwise.
    wait_s: float | None = None

    @property
    def policy(self) -> Policy:
        return POLICIES[self.kind]


# Ruled out before anything else is read. Each names a state no wait changes;
# so does a provider account out of credit (`is_provider_out_of_credit`).
_NEVER = re.compile(
    r"usage limit (exceeded|reached)|UsageLimitError"
    r"|(reached|hit) your (usage )?limit"
    r"|invalid[ _](api[ _]key|x-api-key|request|_request_error)"
    r"|authentication_error|permission_error|\bunauthori[sz]ed\b"
    # Our own gateway's lifetime cap on an upstream key
    # (`deploy/gateway/spend_cap.py`): past it every call is refused until
    # somebody raises the cap or moves the model off that key. LiteLLM words
    # the refusal as a rate limit, so without this it resumes until the streak
    # runs out (2026-10-21, gemini-4-argon at $1005.08 of $1000).
    r"|\bspend[ _]cap\b",
    re.I,
)
# A 4xx other than "too many requests" / "request timeout" is the request or the
# credential being refused; sending it again is refused again.
_CLIENT_ERROR = re.compile(r"API Error:\s*4(?!08|29)\d\d\b")

_COOLING = re.compile(r"\ball Claude accounts are cooling down\b", re.I)
_EGRESS = re.compile(r"\bsubscription egress is offline\b", re.I)
_RATE = re.compile(
    r"temporarily limiting requests|\brate[ _-]?limit|Too Many Requests"
    r"|API Error:\s*429\b|\b429\b.*\brate",
    re.I,
)
_STREAM = re.compile(
    r"Stream ended without receiving any events|Stream idle timeout"
    r"|Response stalled mid-stream|The response stopped arriving"
    r"|stream disconnected before completion|MidStreamFallbackError"
    r"|Response payload is not completed",
    re.I,
)
_UNREACHABLE = re.compile(
    r"\b(ConnectionRefused|Connection refused|ECONNRESET|Connection reset"
    r"|Connection dropped|Cannot connect to host|APIConnectionError)\b",
    re.I,
)
_OVERLOADED = re.compile(
    r"API Error:\s*(500|502|503|504|529)\b"
    r"|\b(Bad Gateway|Service Unavailable|Gateway Timeout|Overloaded"
    r"|InternalServerError|Internal Server Error)\b",
    re.I,
)
_QUOTA_WITH_RESET = re.compile(r"(usage|rate) limit|limit (reached|exceeded)", re.I)

_UNIT_S: Final = {
    "ms": 0.001,
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}
_AMOUNT = (
    r"(\d+(?:\.\d+)?)\s*(ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b"
)
_RETRY_AFTER = re.compile(
    r"retry[-_ ]?after\W{0,3}(\d+(?:\.\d+)?)"
    r"\s*(ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)?\b",
    re.I,
)
_TRY_AGAIN_IN = re.compile(rf"try again in\s+((?:{_AMOUNT}[\s,]*(?:and\s+)?)+)", re.I)


def stated_wait(text: str) -> float | None:
    """How long the failure itself says to wait, in seconds; None when silent.

    Reads the two shapes services use: a ``Retry-After`` value echoed into the
    message (seconds) and "try again in 4 days 2 hours 7 minutes"."""
    if match := _TRY_AGAIN_IN.search(text):
        total = sum(
            float(amount) * _UNIT_S[unit.lower()]
            for amount, unit in re.findall(_AMOUNT, match.group(1), re.I)
        )
        if total > 0:
            return total
    if match := _RETRY_AFTER.search(text):
        unit = (match.group(2) or "s").lower()
        return float(match.group(1)) * _UNIT_S[unit]
    return None


def classify(
    text: str,
    *,
    api_error_status: int | None = None,
    reset_wait_s: float | None = None,
    failure_code: str | None = None,
) -> Resumable | None:
    """The transient kind this failure is, or None when the platform must not
    start the turn again by itself.

    ``reset_wait_s`` is a window reset the session reported in structure (the
    seconds until ``rate_limit.resetsAt``); it wins over anything read from the
    text."""
    if failure_code == RUNNER_REPLACED_CODE:
        # The platform's own typed failure, not a service's words.
        return Resumable(RUNNER_REPLACED)
    said = (text or "").strip()
    if not said:
        return None
    if (
        is_provider_out_of_credit(said)
        or _NEVER.search(said)
        or is_request_refused(said)
        or _CLIENT_ERROR.search(said)
    ):
        stated = reset_wait_s if reset_wait_s is not None else stated_wait(said)
        # A quota that names its own reset is a wait after all.
        if stated is not None and _QUOTA_WITH_RESET.search(said):
            return Resumable(QUOTA_RESET, stated)
        return None
    if api_error_status is not None and 400 <= api_error_status < 500:
        if api_error_status not in (408, 429):
            return None
    stated = reset_wait_s if reset_wait_s is not None else stated_wait(said)
    if _COOLING.search(said):
        return Resumable(SUBSCRIPTION_COOLING, stated)
    if _EGRESS.search(said):
        return Resumable(EGRESS_OFFLINE, stated)
    if _STREAM.search(said):
        return Resumable(STREAM_ENDED, stated)
    if api_error_status == 429 or _RATE.search(said):
        return Resumable(RATE_LIMITED, stated)
    if (api_error_status is not None and api_error_status >= 500) or _OVERLOADED.search(
        said
    ):
        return Resumable(OVERLOADED, stated)
    if _UNREACHABLE.search(said):
        return Resumable(UNREACHABLE, stated)
    return None
