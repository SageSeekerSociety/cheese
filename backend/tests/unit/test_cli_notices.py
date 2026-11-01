"""聊天区里那些不该以芝士的名义出现的英文。

两类，处理办法不同：CLI 自己印的换成中文故障卡（归属错了比语言错了更糟），
芝士自己写的通篇英文则落库但不露面（不翻译、不删，只是不占聊天区）。

用例里的每一句都是从真实会话记录里抓出来的原文，不是编的 —— 前两句在 200 个
会话里出现了 57 次。归属比语言更要紧：一个平台故障被读成 AI 的回答，谁也不知道
该找谁。
"""

import pytest

from app.domain.agent.cli_notices import cli_notice
from app.domain.agent.platform_failures import (
    CONTROL_PLANE_UNAVAILABLE_CODE,
    MODEL_LIMIT_REACHED_CODE,
    PROVIDER_OVERLOADED_CODE,
    PROVIDER_UNREACHABLE_CODE,
    RESPONSE_TRUNCATED_CODE,
    SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
    SUBSCRIPTION_EGRESS_OFFLINE_CODE,
    classify_cli_notice,
)


def test_the_two_most_common_ones_are_recognised():
    assert (
        classify_cli_notice("API Error: Unable to connect to API (ConnectionRefused)")
        == PROVIDER_UNREACHABLE_CODE
    )
    assert (
        classify_cli_notice("API Error: Unable to connect to API (ECONNRESET)")
        == PROVIDER_UNREACHABLE_CODE
    )


def test_the_rest_of_the_real_ones():
    assert (
        classify_cli_notice(
            "API Error: 502 Bad Gateway. This is a server-side issue, usually "
            "temporary — try again in a moment."
        )
        == PROVIDER_OVERLOADED_CODE
    )
    assert (
        classify_cli_notice(
            "API Error: 529 Overloaded. This is a server-side issue, usually temporary."
        )
        == PROVIDER_OVERLOADED_CODE
    )
    assert (
        classify_cli_notice("You've reached your Fable limit. /model to switch models.")
        == MODEL_LIMIT_REACHED_CODE
    )
    assert (
        classify_cli_notice(
            "API Error: Response stalled mid-stream. The response above may "
            "be incomplete."
        )
        == RESPONSE_TRUNCATED_CODE
    )


def test_a_models_spent_upstream_cap_is_a_failed_model_call():
    """Our gateway's lifetime cap on an upstream key words itself like a rate
    limit (`deploy/gateway/spend_cap.py`). It is the platform's key: the room
    reads a failed model call with a retry that works once the cap is raised,
    and the operators are told it was the cap."""
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import (
        _turn_failure_notice,
        platform_side_cause,
    )

    line = (
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        " · gemini/gemini-4-argon has reached its spend cap: $1005.08 of $1000.00."
        " Received Model Group=gemini-4-argon\nAvailable Model Group Fallbacks=None"
        "（rate_limit）"
    )
    assert classify_cli_notice(line) == MODEL_LIMIT_REACHED_CODE
    said, meta = _turn_failure_notice(line, None)
    assert said == say("turnFailedModelError")
    assert meta["retryable"] is True
    assert platform_side_cause(line) == MODEL_LIMIT_REACHED_CODE


@pytest.mark.parametrize(
    "line",
    [
        # The admission check could not say which project this call is billed
        # to, and the platform has a Claude login to fall back on.
        "API Error: 503 cheese: task billing attribution is temporarily "
        "unavailable; retry shortly. This is a server-side issue, usually "
        "temporary — try again in a moment. If it persists, check "
        "https://status.claude.com.（server_error）",
        # The same 503 with no Claude login to fall back on: the control plane
        # could not place the request at all.
        "API Error: 503 cheese: the control plane could not say where this "
        "request goes, and the platform has no Claude login to fall back on; "
        "retry shortly",
    ],
)
def test_the_metering_proxys_admission_outage_is_the_platforms_to_fix(line):
    """The proxy's 503 when it cannot ask who the call is billed to is neither
    an overloaded provider nor a spent account. It used to reach the room as
    「平台的 AI 服务额度暂时不足」, because its own wording contains the word
    "billing". The room reads a failed model call with a retry, the service's
    words kept whole behind it; the operators are told it was the proxy, which
    an overloaded provider is not."""
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import (
        _turn_failure_notice,
        platform_side_cause,
    )

    assert classify_cli_notice(line) == CONTROL_PLANE_UNAVAILABLE_CODE
    said, meta = _turn_failure_notice(line, None)
    assert said == say("turnFailedModelError")
    assert meta["retryable"] is True
    assert line in meta["detail"]
    assert platform_side_cause(line) == CONTROL_PLANE_UNAVAILABLE_CODE
    assert platform_side_cause("API Error: 502 Bad Gateway.") is None


def test_the_metering_proxys_offline_egress_is_the_platforms_to_fix():
    """The proxy's 503 for an egress that does not answer is not an overloaded
    provider: the machine has to come back, and the operators are told so."""
    from app.domain.agent.turn.state.notices import platform_side_cause

    line = (
        'API Error: 503 {"type":"error","error":{"type":"api_error","message":'
        '"cheese: the subscription egress is offline, so no model call was made. '
        'It works again once that machine is back online."}}'
    )
    assert classify_cli_notice(line) == SUBSCRIPTION_EGRESS_OFFLINE_CODE
    assert platform_side_cause(line) == SUBSCRIPTION_EGRESS_OFFLINE_CODE


# ---- 不能误伤芝士自己的话 ----


def test_a_chinese_message_quoting_the_error_is_still_a_message():
    # 芝士讨论这个报错时会把原话引进来。那条消息是它说的话，不能被换掉 ——
    # 这正是本话题里发生过的事。
    text = (
        "查到了：`API Error: Unable to connect to API (ConnectionRefused)` "
        "在本平台有两个完全不同的根因，别混为一谈。"
    )
    assert classify_cli_notice(text) is None
    assert cli_notice(text) is None


def test_a_long_english_message_is_content_not_a_notice():
    # 提示是短的。长成一段的东西是内容，哪怕它以同样的词开头。
    text = "API Error: Unable to connect to API (ConnectionRefused). " + "x" * 400
    assert classify_cli_notice(text) is None


def test_an_ordinary_english_sentence_is_left_alone():
    # 模型自己写的英文过场话不归这里管（那是另一半，靠提示词/小模型）。
    assert classify_cli_notice("I'll start by confirming the environment.") is None
    assert classify_cli_notice("Now the test pinning those two env vars:") is None


def test_it_must_start_with_the_known_opening():
    # 只在句中出现不算 —— 否则一句「the log says API Error: ...」也会被吞掉。
    assert classify_cli_notice("the log says API Error: 502 Bad Gateway") is None


# ---- 换成什么 ----


@pytest.mark.parametrize(
    "original",
    [
        "API Error: Unable to connect to API (ConnectionRefused)",
        "API Error: 529 Overloaded. This is a server-side issue.",
        "API Error: 502 Bad Gateway. This is a server-side issue, usually temporary.",
        "You've reached your Fable limit. /model to switch models.",
    ],
)
def test_a_failed_model_call_says_only_that_with_a_retry(original):
    """Whatever made calling the model fail, the room reads one line of the
    platform's own, offered with a retry. The CLI's English is the only copy
    of what the service said: folded behind it, not dropped."""
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    line, meta = _turn_failure_notice(original, None)
    assert line == say("turnFailedModelError")
    assert meta["severity"] == "error"
    assert meta["retryable"] is True
    assert original in meta["detail"]


def test_a_cli_line_about_the_turn_itself_keeps_its_own_card():
    """What the CLI says about the reply itself, not about calling the model,
    is still its own card in the platform's words."""
    original = (
        "API Error: Response stalled mid-stream. The response above may be incomplete."
    )
    result = cli_notice(original)
    assert result is not None
    line, meta = result
    assert "API Error" not in line
    assert original in meta["detail"]


# ---- 同一句话结束了一轮 ----


def test_a_turn_ended_by_the_subscription_cooling_down_tells_the_operators():
    """FB-87: the proxy refused every call because each Claude account of the
    subscription was cooling down, and the room read only 「本轮未完成」. The
    room reads a failed model call with a retry, the service's words behind it;
    the subscription's accounts are the platform's, so its operators are told
    which cause it was."""
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import (
        _turn_failure_notice,
        platform_side_cause,
    )

    said = (
        "API Error: Server is temporarily limiting requests (not your usage "
        "limit) · cheese: all Claude accounts are cooling down（rate_limit）"
    )
    line, meta = _turn_failure_notice(said, None)
    assert line == say("turnFailedModelError")
    assert said in meta["detail"]
    assert platform_side_cause(said) == SUBSCRIPTION_ACCOUNTS_COOLING_CODE


def test_a_turn_the_provider_throttled_says_so():
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    line, _ = _turn_failure_notice(
        "API Error: Server is temporarily limiting requests (not your usage limit)",
        None,
    )
    assert line != say("turnFailedService")


@pytest.mark.parametrize(
    "said",
    [
        # The shape of the refusal that ended a conversation for good: an image
        # in it that does not decode, refused through the model gateway.
        "API Error: 400 litellm.BadRequestError: DeepseekException - "
        '{"error":{"message":"messages.138.content.0.tool_result.image[0]: You '
        'have uploaded an unsupported image.","type":"invalid_request_error"}}',
        'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
        '"message":"Could not process image"}}',
    ],
)
def test_a_request_refused_as_invalid_is_not_offered_again(said):
    """The model's API refused the request as invalid. A retry sends the same
    conversation and is refused the same way, so the card does not offer one —
    whichever provider refused it — and the service's words stay behind it."""
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    _, meta = _turn_failure_notice(said, None)
    assert not meta.get("retryable")
    assert said in meta["detail"]


def test_a_failure_the_platform_cannot_place_still_offers_a_retry():
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    _, meta = _turn_failure_notice("upstream connect error before headers", None)
    assert meta.get("retryable") is True
