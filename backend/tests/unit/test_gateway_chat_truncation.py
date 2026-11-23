"""一个答案读不出来时，日志里要能看出是「被截断」还是「模型写了乱码」。

``finish_reason: length`` 是供应商自己说撞了输出上限；没有它时，一个没收口的 ``{``
或一条没结束的字符串，也是被截断的 JSON 留下的形状。两者分开，才不会把一次上限不
够读成模型不会写 JSON —— 那正是这个缺陷一开始难看见的原因。
"""

import json

from app.domain.gateway_chat import (
    Completion,
    Usage,
    looks_cut_short,
    unreadable_note,
)


def _done(content: str, finish: str = "stop") -> Completion:
    return Completion(content, Usage(), 0.0, finish)


def test_a_brace_or_string_left_open_looks_cut():
    assert looks_cut_short('{"questions": [{"stem": "内层怎么引用')
    assert looks_cut_short('{"questions": [{"stem": "第一道"}, {"stem": "第二道')
    # 完整的 JSON 收口了，不是被截断的形状。
    assert not looks_cut_short('{"questions": []}')
    assert not looks_cut_short("")
    assert not looks_cut_short("我不知道")


def test_finish_reason_length_is_named_even_if_the_json_happens_to_close():
    # 供应商说撞了上限：即使文本看起来凑巧收了口，也按截断记。
    assert unreadable_note(_done('{"a": 1}', finish="length")) == "cut at max_tokens"


def test_an_unclosed_answer_without_the_flag_is_still_flagged_as_cut():
    assert unreadable_note(_done('{"questions": [{"stem": "没写完')) == (
        "looks cut off mid-JSON"
    )


def test_a_complete_but_wrong_answer_is_malformed_not_truncated():
    # 收口了、又解析不出来：是乱码，不是被截断 —— 两种问题分开记。
    assert unreadable_note(_done("对不起，我不会")) == "malformed"
    assert (
        unreadable_note(_done(json.dumps({"questions": "not a list"}))) == "malformed"
    )
