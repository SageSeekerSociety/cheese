"""程序的输出和期望输出比，四种比法（方案 §输入/输出用例）。

比对在评测主机上、容器外做：期望输出从不进容器，学生程序也就没有机会改它或读到它。
所以这个模块和 ``container``、``runner`` 一起被原样发到评测主机上跑（``runner`` 开头
那一段），只能用标准库，也不能写 ``from __future__``（几份源码拼成一个脚本时它不在
第一行）。主机上的 Python 不比 3.10 旧。

* ``TRIM_TRAILING``（默认）：每行行末的空白不算，末尾的空行不算。
* ``EXACT``：逐字节一致。
* ``NUMERIC``：按空白切成一个个词，个数相同；两边都是数的，差在允许误差内就算对
  （``tolerance`` 是相对误差，数很小时按绝对误差算）；不是数的词要一字不差。
* ``IGNORE_CASE``：在 ``TRIM_TRAILING`` 之上不分大小写。
"""

import math

TRIM_TRAILING = "TRIM_TRAILING"
EXACT = "EXACT"
NUMERIC = "NUMERIC"
IGNORE_CASE = "IGNORE_CASE"
MODES = (TRIM_TRAILING, EXACT, NUMERIC, IGNORE_CASE)


def _lines(data: bytes) -> list[str]:
    lines = [line.rstrip() for line in data.decode("utf-8", "replace").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _number(word: str) -> float | None:
    try:
        value = float(word)
    except ValueError:
        return None
    return None if math.isnan(value) else value


def _numbers_match(actual: bytes, expected: bytes, tolerance: float) -> bool:
    got = actual.decode("utf-8", "replace").split()
    want = expected.decode("utf-8", "replace").split()
    if len(got) != len(want):
        return False
    for mine, theirs in zip(got, want, strict=True):
        a, b = _number(mine), _number(theirs)
        if a is None or b is None:
            if mine != theirs:
                return False
            continue
        if a == b:
            continue
        if math.isinf(a) or math.isinf(b):
            return False
        if abs(a - b) > tolerance * max(1.0, abs(b)):
            return False
    return True


def same_output(actual: bytes, expected: bytes, mode: str, tolerance: float) -> bool:
    """Whether the program's output ``actual`` counts as ``expected`` under
    ``mode``. An unknown mode compares like the default."""
    if mode == EXACT:
        return actual == expected
    if mode == NUMERIC:
        return _numbers_match(actual, expected, max(tolerance, 0.0))
    got, want = _lines(actual), _lines(expected)
    if mode == IGNORE_CASE:
        return [line.casefold() for line in got] == [line.casefold() for line in want]
    return got == want
