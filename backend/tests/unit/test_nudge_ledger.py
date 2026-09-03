"""消毒和去重账本本身 —— 两件不需要数据库就能钉死的事。

链路级的断言在 `tests/integration/test_nudge_pr_signals.py`；这里只管这两个纯函
数式的部件，因为它们各自有一批边界，走一遍完整轮询去覆盖太贵也太绕。
"""

import pytest

from app.domain.review.pr_signals import (
    NudgeKind,
    NudgeLedger,
    ReviewSignal,
    sanitize_external,
    signature,
)


@pytest.mark.parametrize(
    "raw",
    [
        "\x1b[31m红\x1b[0m",  # CSI：颜色
        "\x1b[2J清屏",  # CSI：清屏
        "\x1b]0;标题\x07之后",  # OSC：改标题栏
        "响铃\x07",  # 裸控制字符
        "空\x00字节",
        "覆盖上一行\r",
    ],
)
def test_no_escape_or_control_byte_survives(raw: str) -> None:
    """洗完之后不许再有 ESC / BEL / NUL —— 这些最终会被贴进一个真的终端。"""
    cleaned = sanitize_external(raw)
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "\x00" not in cleaned
    assert "\r" not in cleaned


def test_newlines_and_tabs_survive() -> None:
    """洗的是载体不是内容：多行日志靠换行和制表符才读得懂。"""
    assert sanitize_external("a\nb\tc") == "a\nb\tc"
    assert sanitize_external("windows\r\nline") == "windows\nline"


def test_the_words_themselves_are_untouched() -> None:
    assert sanitize_external("\x1b[31mpytest failed\x1b[0m") == "pytest failed"


def test_a_signal_line_sanitizes_author_and_body() -> None:
    line = ReviewSignal(
        id="review:1",
        kind="changes_requested",
        author="b\x1b[2Job",
        body="改\x07这里",
        where="app/\x1b[2Kx.py:3",
    ).line()
    assert "\x1b" not in line and "\x07" not in line
    assert "改这里" in line


# ---- 账本 --------------------------------------------------------------------


def test_a_kind_only_dedups_against_itself() -> None:
    """跨类不互相压制 —— 这正是「一件事吞掉另一件事」在数据结构上的形状。"""
    ledger = NudgeLedger()
    ledger.record(NudgeKind.ci, "sig-a")

    assert ledger.already_sent(NudgeKind.ci, "sig-a")
    assert not ledger.already_sent(NudgeKind.review, "sig-a")
    assert not ledger.already_sent(NudgeKind.conflict, "sig-a")


def test_a_new_signature_reopens_the_same_kind() -> None:
    ledger = NudgeLedger()
    ledger.record(NudgeKind.ci, "sig-a")
    assert not ledger.already_sent(NudgeKind.ci, "sig-b")


def test_the_ledger_survives_a_round_trip_through_the_column() -> None:
    """存进去、读回来，判断必须一模一样 —— 它存在的全部理由就是活过重启。"""
    ledger = NudgeLedger()
    ledger.record(NudgeKind.review, "sig-a")
    ledger.record(NudgeKind.review, "sig-b")

    revived = NudgeLedger.load(ledger.dump())

    assert revived.already_sent(NudgeKind.review, "sig-b")
    assert revived.rounds(NudgeKind.review) == 2


def test_dump_never_hands_back_the_same_object() -> None:
    """SQLAlchemy 看不见 JSON 列的原地修改：就地改一个 dict 等于改完不落库。"""
    ledger = NudgeLedger()
    ledger.record(NudgeKind.ci, "sig-a")
    dumped = ledger.dump()
    ledger.record(NudgeKind.ci, "sig-b")
    assert dumped["seen"]["ci"] == "sig-a"


@pytest.mark.parametrize(
    "raw",
    [None, "not a dict", 7, {}, {"seen": "wrong"}, {"attempts": {"ci": "three"}}],
)
def test_a_broken_ledger_degrades_to_empty(raw: object) -> None:
    """一本读不懂的账最多让芝士被多叫一次；为它抛异常会让整轮轮询停掉。"""
    ledger = NudgeLedger.load(raw)
    assert not ledger.already_sent(NudgeKind.ci, "anything")
    assert ledger.rounds(NudgeKind.review) == 0


def test_signature_separates_its_parts() -> None:
    """拼接不能有歧义：`ab` + `c` 和 `a` + `bc` 必须是两个签名。"""
    assert signature("ab", "c") != signature("a", "bc")
