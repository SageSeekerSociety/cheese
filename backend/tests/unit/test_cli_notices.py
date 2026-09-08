"""CLI 自己印在对话里的英文提示，不该顶着芝士的名字发出去。

用例里的每一句都是从真实会话记录里抓出来的原文，不是编的 —— 前两句在 200 个
会话里出现了 57 次。归属比语言更要紧：一个平台故障被读成 AI 的回答，谁也不知道
该找谁。
"""

from app.domain.agent.chat import _cli_notice
from app.domain.agent.platform_failures import (
    MODEL_LIMIT_REACHED_CODE,
    PROVIDER_OVERLOADED_CODE,
    PROVIDER_UNREACHABLE_CODE,
    RESPONSE_TRUNCATED_CODE,
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
    assert classify_cli_notice(
        "API Error: 502 Bad Gateway. This is a server-side issue, usually "
        "temporary — try again in a moment."
    ) == PROVIDER_OVERLOADED_CODE
    assert classify_cli_notice(
        "API Error: 529 Overloaded. This is a server-side issue, usually temporary."
    ) == PROVIDER_OVERLOADED_CODE
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


# ---- 不能误伤芝士自己的话 ----


def test_a_chinese_message_quoting_the_error_is_still_a_message():
    # 芝士讨论这个报错时会把原话引进来。那条消息是它说的话，不能被换掉 ——
    # 这正是本话题里发生过的事。
    text = (
        "查到了：`API Error: Unable to connect to API (ConnectionRefused)` "
        "在本平台有两个完全不同的根因，别混为一谈。"
    )
    assert classify_cli_notice(text) is None
    assert _cli_notice(text) is None


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
    result = _cli_notice(original)
    assert result is not None
    line, meta = result
    assert line == "芝士连不上 AI 服务，这一步没做成"
    assert meta["severity"] == "error"
    # 原话是唯一的一份，不能丢 —— 折叠起来，不是删掉。
    assert original in meta["detail"]


def test_a_limit_says_it_needs_a_person_not_a_retry():
    # 「稍后重试」对额度用完是错的建议，会把人送进一个不可能成功的循环。
    result = _cli_notice("You've reached your Fable limit. /model to switch models.")
    assert result is not None
    line, meta = result
    assert line == "这个模型的额度用完了"
    assert meta["who"] == "human"
    assert "重试无效" in meta["detail"]


def test_an_overload_says_it_is_worth_retrying():
    result = _cli_notice("API Error: 529 Overloaded. This is a server-side issue.")
    assert result is not None
    _, meta = result
    assert meta["severity"] == "warn"
    assert meta["who"] == "platform"
