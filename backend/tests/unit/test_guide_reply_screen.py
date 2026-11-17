"""引导为主的两项保护落在一条 AI 回复上：去掉提示阶梯的标记、长代码换成提示。

纯函数（``app.domain.task.guide.screen``）：回复变成什么只看回复本身和哪项保护开着。
落库链路上「非引导模式的项目一个字都不改」在
``tests/integration/test_guide_reply_guards.py``。
"""

from app.domain.task.guide import OUTPUT_CHECK_LINES, screen

LONG = "\n".join(f"line_{n} = {n}" for n in range(OUTPUT_CHECK_LINES))
SHORT = "\n".join(f"line_{n} = {n}" for n in range(3))


def test_the_ladder_mark_is_read_and_taken_out_of_the_message() -> None:
    got = screen("[[hint:2]]\n想想外层那一行在内层叫什么？", ladder=True, check=False)
    assert got.hint == 2
    assert got.text == "想想外层那一行在内层叫什么？"


def test_a_mark_is_left_alone_when_the_ladder_is_off() -> None:
    text = "[[hint:2]] 想想看"
    got = screen(text, ladder=False, check=True)
    assert got.hint is None and got.text == text


def test_a_long_code_block_becomes_a_hint_and_a_short_one_stays() -> None:
    reply = f"可以这样写：\n```python\n{LONG}\n```\n再看看这个：\n```\n{SHORT}\n```"
    got = screen(reply, ladder=False, check=True)
    assert got.checked == [OUTPUT_CHECK_LINES]
    assert "line_11" not in got.text
    assert "可以这样写" in got.text
    assert SHORT in got.text
    assert "引导为主" in got.text


def test_an_unclosed_long_block_is_caught_too() -> None:
    got = screen(f"```js\n{LONG}", ladder=False, check=True)
    assert got.checked and "line_5" not in got.text


def test_nothing_changes_when_the_check_is_off() -> None:
    reply = f"```\n{LONG}\n```"
    got = screen(reply, ladder=True, check=False)
    assert got.text == reply and not got.changed
