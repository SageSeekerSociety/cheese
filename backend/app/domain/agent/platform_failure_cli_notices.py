"""What the CLI prints into the conversation as if the agent had said it.

The failures above travel as exceptions or as an ``AgentResult``. This kind does
not: Claude Code prints these lines **as assistant output**, so they land in the
room verbatim and read as if 芝士 were speaking English — 「API Error: Unable to
connect to API (ConnectionRefused)」. Measured over 200 sessions: 88 such lines,
highly repetitive (57 of them the first two shapes).

This is exactly the first kind in the module docstring: sentences somebody else
wrote, which the platform cannot make structured and can only match by text. Once
recognised they get the platform's own notice card rather than being sent under
芝士's name in English.

The match is against the **whole message**, never a substring: 芝士 quoting a
provider's error inside a Chinese sentence is 芝士's own message and must not be
rewritten. So all three conditions must hold — no CJK anywhere, short, and one of
the known openings.
"""

from __future__ import annotations

import re

PROVIDER_UNREACHABLE_CODE = "provider_unreachable"
PROVIDER_OVERLOADED_CODE = "provider_overloaded"
SUBSCRIPTION_EGRESS_OFFLINE_CODE = "subscription_egress_offline"
SUBSCRIPTION_ACCOUNTS_COOLING_CODE = "subscription_accounts_cooling"
MODEL_LIMIT_REACHED_CODE = "model_limit_reached"
RESPONSE_TRUNCATED_CODE = "response_truncated"
TOOL_UNAVAILABLE_CODE = "tool_unavailable"
CONTROL_PLANE_UNAVAILABLE_CODE = "control_plane_unavailable"

#: 一条 CLI 提示最长能有多长。真实样本最长的一条 120 字符出头;留三倍余量,再长
#: 就不是提示而是内容了。
_CLI_NOTICE_MAX = 300

_CLI_NOTICES: tuple[tuple[re.Pattern[str], str], ...] = (
    # The metering proxy's own answer when the machine the subscription must
    # leave through does not respond (`deploy/metering-proxy`, a 503 carrying
    # `retry-after`, so the client waits and asks again). Before the 503 pattern
    # below, which it also matches — and so this fires once the turn has spent
    # its retries and the client prints the refusal after all.
    (
        re.compile(r"^API Error:.*\bsubscription egress is offline\b", re.I),
        SUBSCRIPTION_EGRESS_OFFLINE_CODE,
    ),
    # The metering proxy's own 503 while its admission check cannot answer
    # (`deploy/metering-proxy/cheese_billing_core.py`, `billing_addon.py`): the
    # platform could not say which project and work this call is billed to, so
    # it made no model call and told the client to retry shortly. Not the
    # provider being overloaded, and never the platform's account being empty —
    # the second is what the word "billing" in it used to be read as, which
    # also stopped the turn from resuming by itself (`resumable_failures`).
    # Before the 502/503 pattern below, which it also matches.
    (
        re.compile(
            r"^API Error:.*\b(task billing attribution is temporarily"
            r" unavailable|control plane could not say where this request goes)\b",
            re.I,
        ),
        CONTROL_PLANE_UNAVAILABLE_CODE,
    ),
    # The metering proxy's 429 when every Claude account of the subscription is
    # cooling down after hitting its own limit (`deploy/metering-proxy`,
    # `claude_accounts.py`). Not the provider throttling everyone for a moment:
    # the accounts stay out until their limits reset, minutes to days later.
    (
        re.compile(r"^API Error:.*\ball Claude accounts are cooling down\b", re.I),
        SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
    ),
    (
        re.compile(
            r"^API Error:.*\b(ConnectionRefused|ECONNRESET|Connection refused)\b",
            re.I,
        ),
        PROVIDER_UNREACHABLE_CODE,
    ),
    (
        re.compile(r"^API Error:\s*(502|503|529)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^API Error:.*\b(Overloaded|Bad Gateway)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    # Our gateway's lifetime cap on one upstream key (`deploy/gateway/spend_cap.py`).
    # It arrives wrapped in the "temporarily limiting requests" wording below,
    # which would read as an overload that clears up; nothing does until the cap
    # is raised or the model is moved off that key.
    (
        re.compile(r"^API Error:.*\bhas reached its spend cap\b", re.I),
        MODEL_LIMIT_REACHED_CODE,
    ),
    # Claude Code's wording for a 429 that is not the account's usage limit.
    (
        re.compile(r"^API Error:.*\btemporarily limiting requests\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^You'?ve reached your .*\blimit\b", re.I),
        MODEL_LIMIT_REACHED_CODE,
    ),
    (
        re.compile(
            r"^(API Error:\s*)?(Response stalled mid-stream"
            r"|The response stopped arriving)",
            re.I,
        ),
        RESPONSE_TRUNCATED_CODE,
    ),
    # 「我还没有 X 的权限,请批准一下」—— 在这个平台上根本没有人能批准:那个
    # 授权框画在容器的终端里,房间里的人够不着。所以它出现本身就说明配置不对,
    # 而它读起来却像一句正常的请求 —— 一个故障伪装成了一句话,最坏的一种。
    (
        re.compile(
            r"^(I don'?t have permission to use\b"
            r"|No response requested\.?$"
            r"|Tool ran without output)",
            re.I,
        ),
        TOOL_UNAVAILABLE_CODE,
    ),
)

_HAS_CJK = re.compile(r"[一-鿿]")

#: Claude Code's line for a request the model's API refused as invalid. Read by
#: its status, 400, which every provider gives an invalid request, and never by
#: the provider's own words after it, which differ from one provider to the next.
_REQUEST_REFUSED = re.compile(r"^API Error:\s*400\b")


def is_request_refused(text: str) -> bool:
    """A failure the model's API refused as an invalid request.

    Retrying sends the same conversation again, and it is refused the same way:
    what it refuses is already in the conversation, an image that does not
    decode being the case that ended a conversation for good."""
    return bool(_REQUEST_REFUSED.match(text.strip()))


def classify_cli_notice(text: str) -> str | None:
    """整条消息其实是 CLI 自己印的一句英文提示时,它属于哪一类;否则 None。"""
    line = text.strip()
    if not line or len(line) > _CLI_NOTICE_MAX or _HAS_CJK.search(line):
        return None
    for pattern, failure in _CLI_NOTICES:
        if pattern.search(line):
            return failure
    return None
