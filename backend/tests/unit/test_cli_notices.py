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


def test_a_models_spent_upstream_cap_is_not_retryable():
    """Our gateway's lifetime cap on an upstream key words itself like a rate
    limit (`deploy/gateway/spend_cap.py`), which used to read as an overload
    that clears up and offered a retry that could only be refused again."""
    line = (
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        " · gemini/gemini-4-argon has reached its spend cap: $1005.08 of $1000.00."
        " Received Model Group=gemini-4-argon\nAvailable Model Group Fallbacks=None"
        "（rate_limit）"
    )
    assert classify_cli_notice(line) == MODEL_LIMIT_REACHED_CODE
    notice = cli_notice(line)
    assert notice is not None and not notice[1].get("retryable")


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
def test_the_metering_proxys_admission_outage_is_its_own_notice(line):
    """The proxy's 503 when it cannot ask who the call is billed to is neither
    an overloaded provider nor a spent account. It used to reach the room as
    「平台的 AI 服务额度暂时不足」, because its own wording contains the word
    "billing"; the readers were sent to wait for a top-up nobody owed, and the
    turn was not resumed (a spent balance is not a wait)."""
    assert classify_cli_notice(line) == CONTROL_PLANE_UNAVAILABLE_CODE
    overloaded, notice = cli_notice("API Error: 502 Bad Gateway."), cli_notice(line)
    assert overloaded is not None and notice is not None
    # Not the overload card: the provider was never called.
    assert notice[0] != overloaded[0]
    # The platform's own temporary problem, offered with a retry, and the
    # service's words kept whole.
    assert notice[1]["who"] == "platform"
    assert notice[1]["retryable"] is True
    assert line in str(notice[1])


def test_the_metering_proxys_offline_egress_is_its_own_notice():
    """The proxy's 503 for an egress that does not answer is not an overloaded
    provider: waiting on the provider will not help, the machine has to come
    back."""
    line = (
        'API Error: 503 {"type":"error","error":{"type":"api_error","message":'
        '"cheese: the subscription egress is offline, so no model call was made. '
        'It works again once that machine is back online."}}'
    )
    assert classify_cli_notice(line) == SUBSCRIPTION_EGRESS_OFFLINE_CODE
    overloaded = cli_notice("API Error: 502 Bad Gateway.")
    notice = cli_notice(line)
    assert notice is not None and overloaded is not None
    assert notice[0] != overloaded[0]
    assert line in str(notice[1])


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


def test_the_card_says_it_in_chinese_and_keeps_the_original():
    original = "API Error: Unable to connect to API (ConnectionRefused)"
    result = cli_notice(original)
    assert result is not None
    line, meta = result
    # 卡面那一行是平台自己的话，不是 CLI 的英文原话。
    assert "API Error" not in line
    assert meta["severity"] == "error"
    # 原话是唯一的一份，不能丢 —— 折叠起来，不是删掉。
    assert original in meta["detail"]


def test_a_limit_says_it_needs_a_person_not_a_retry():
    # 「稍后重试」对额度用完是错的建议，会把人送进一个不可能成功的循环。
    result = cli_notice("You've reached your Fable limit. /model to switch models.")
    assert result is not None
    line, meta = result
    assert meta["who"] == "human"
    # 房间不会给它一个重试按钮。
    assert not meta.get("retryable")


def test_an_overload_says_it_is_worth_retrying():
    result = cli_notice("API Error: 529 Overloaded. This is a server-side issue.")
    assert result is not None
    _, meta = result
    assert meta["severity"] == "warn"
    assert meta["who"] == "platform"
    assert meta["retryable"] is True


# ---- 同一句话结束了一轮 ----


def test_a_turn_ended_by_the_subscription_cooling_down_says_so():
    """FB-87: the proxy refused every call because each Claude account of the
    subscription was cooling down, Claude Code ended the turn with that refusal,
    and the room read only 「本轮未完成」. A turn that ends incomplete says why
    on its line, and the service's words stay behind it."""
    from app.core.sentences import say
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    said = (
        "API Error: Server is temporarily limiting requests (not your usage "
        "limit) · cheese: all Claude accounts are cooling down（rate_limit）"
    )
    line, meta = _turn_failure_notice(said, None)
    assert line != say("turnFailedService")
    # Not the provider being briefly overloaded: waiting a moment will not help.
    overloaded = cli_notice("API Error: 529 Overloaded.")
    assert overloaded is not None and line != overloaded[0]
    assert said in meta["detail"]


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
