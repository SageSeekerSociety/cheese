"""程序的输出算不算对：出题人能选的四种比法（方案 §输入/输出用例）。"""

import pytest

from app.domain.judge.compare import (
    EXACT,
    IGNORE_CASE,
    NUMERIC,
    TRIM_TRAILING,
    same_output,
)


@pytest.mark.parametrize(
    ("actual", "expected", "want"),
    [
        (b"3\n", b"3\n", True),
        # 行末的空白、Windows 的换行和末尾的空行都不算。
        (b"1 2  \r\n3\n\n\n", b"1 2\n3", True),
        # 行首的空白算：那是另一种输出。
        (b" 3\n", b"3\n", False),
        # 少一行、多一行都不对。
        (b"1\n2\n", b"1\n", False),
        (b"Yes\n", b"yes\n", False),
    ],
)
def test_the_default_ignores_trailing_spaces_and_blank_lines(actual, expected, want):
    assert same_output(actual, expected, TRIM_TRAILING, 0.0) is want


def test_exact_counts_every_byte():
    assert same_output(b"3\n", b"3\n", EXACT, 0.0)
    assert not same_output(b"3", b"3\n", EXACT, 0.0)
    assert not same_output(b"3 \n", b"3\n", EXACT, 0.0)


@pytest.mark.parametrize(
    ("actual", "expected", "tolerance", "want"),
    [
        (b"3.14159\n", b"3.1416\n", 1e-4, True),
        (b"3.15\n", b"3.1416\n", 1e-4, False),
        # 很小的数按绝对误差算，不会因为相对误差无限放大而永远不对。
        (b"0.0000001\n", b"0\n", 1e-6, True),
        # 大数按相对误差算。
        (b"1000001\n", b"1000000\n", 1e-5, True),
        # 换行、空格多少都不算，个数要一样。
        (b"1 2\n3", b"1\n2   3\n", 0.0, True),
        (b"1 2", b"1 2 3", 1.0, False),
        # 数按数比，2.0 就是 2；不是数的词要一字不差。
        (b"case 1: 2.0", b"case 1: 2", 0.0, True),
        (b"Case 1: 2.0", b"case 1: 2", 0.0, False),
        # nan 不当数比，当一个词：写的是同一个词才算对。
        (b"nan", b"nan", 0.0, True),
        (b"nan", b"1", 1.0, False),
        (b"inf", b"inf", 0.0, True),
    ],
)
def test_numeric_allows_the_setters_error(actual, expected, tolerance, want):
    assert same_output(actual, expected, NUMERIC, tolerance) is want


def test_ignore_case_also_ignores_trailing_spaces():
    assert same_output(b"YES  \n\n", b"yes\n", IGNORE_CASE, 0.0)
    assert not same_output(b"YES\nNO\n", b"yes\n", IGNORE_CASE, 0.0)


def test_output_that_is_not_text_is_compared_not_crashed_on():
    assert not same_output(b"\xff\xfe", b"3\n", TRIM_TRAILING, 0.0)
    assert not same_output(b"\xff\xfe", b"3\n", NUMERIC, 0.0)
