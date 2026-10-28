"""Recognising somebody else's failure from the text it arrived in.

A full disk and a missing docker image are not the platform's sentences: one is
the kernel's or a daemon's, the other a registry's. The platform cannot make
them structured, so it matches their text — and where the text is an exception
it can ask errno first, which is the one reading that cannot drift with a
translation or a copy edit.

Every rule here is deliberately narrow. `"no space"` in a schedule and
`Permission denied` on an unrelated file both mean nothing about the platform,
so a rule that fires on a fragment of English is worse than no rule: it blames
something that did not happen.
"""

from __future__ import annotations

import errno
import re

# --- 磁盘写满 ------------------------------------------------------------------

_STORAGE_PATTERNS = (
    re.compile(r"\bno space left on device\b", re.IGNORECASE),
    re.compile(r"\benospc\b", re.IGNORECASE),
    re.compile(r"\berrno\s*28\b", re.IGNORECASE),
)


def _text_is_storage_exhausted(text: str) -> bool:
    return any(pattern.search(text) for pattern in _STORAGE_PATTERNS)


def is_storage_exhausted(value: BaseException | str) -> bool:
    """Conservatively identify ENOSPC without guessing from vague copy."""
    if isinstance(value, str):
        return _text_is_storage_exhausted(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            return True
        if _text_is_storage_exhausted(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


# --- 运行时镜像拉不到 ----------------------------------------------------------

_RUNTIME_IMAGE_MARKERS = (
    "cheesex-agent-sandbox",
    "/sandbox:",
)
_RUNTIME_IMAGE_FAILURES = (
    "unable to find image",
    "no such image",
    "pull access denied",
    "manifest unknown",
)


def _text_is_runtime_image_missing(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RUNTIME_IMAGE_MARKERS) and any(
        failure in lowered for failure in _RUNTIME_IMAGE_FAILURES
    )


def is_runtime_image_missing(value: BaseException | str) -> bool:
    """Identify an unavailable Cheese agent image without leaking daemon copy."""
    if isinstance(value, str):
        return _text_is_runtime_image_missing(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _text_is_runtime_image_missing(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


# --- 平台接入的 AI 服务账户没钱了 ------------------------------------------------

# The platform's own account with a model provider has no money left: a state
# no wait changes and that only the platform's administrators can fix, never
# the person whose turn it was. The gateway takes such a row out of rotation
# and, once every row of a model is out, answers with
# `cheese_upstream_credit_exhausted` (deploy/gateway/provider_credit.py); the
# rest are providers' own words, for a refusal that reaches a turn some other
# way. One list for both readers: the room's notice
# (`turn.state.notices`) and whether the platform starts the turn again by
# itself (`resumable_failures`).
#
# Every alternative names a spent balance or a paused account. The bare word
# "billing" used to be one of them, and it read the platform's own transient
# 503 — 「cheese: task billing attribution is temporarily unavailable」, the
# metering proxy's answer when its admission check cannot answer — as an empty
# account. Two things followed from that: the room was told to wait for a
# top-up that was not owed, and no resume was scheduled, because a spent
# balance is a state no wait changes (`resumable_failures.classify`).
#
# The word is not dropped, only tied to the account it is about. OpenAI names
# three of its own refusals with it — 「You exceeded your current quota, please
# check your plan and billing details」, 「Billing hard limit has been
# reached」 and 「Your account is not active, please check your billing
# details」 — and all three mean a state no wait changes. A bare "billing"
# matched those and every other mention of the word besides.
_PROVIDER_OUT_OF_CREDIT = re.compile(
    r"cheese_upstream_credit_exhausted"
    r"|insufficient[ _](balance|quota|funds)"
    r"|余额不足|请充值"
    r"|quota (exceeded|exhausted)"
    r"|exceeded your current quota"
    r"|billing[ _](hard[ _]limit|not[ _]active)"
    r"|check your billing details"
    r"|\bsuspended\b"
    r"|credit balance is too low"
    r"|SetLimitExceeded|exceeded_current_quota",
    re.IGNORECASE,
)


def is_provider_out_of_credit(text: str | None) -> bool:
    """Whether a failure says the platform's provider account has no money."""
    return bool(text) and bool(_PROVIDER_OUT_OF_CREDIT.search(text))
