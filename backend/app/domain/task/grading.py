"""测试点那一项（``tests``）的自动分怎么算（方案 §评审那一行）。

``tests`` 行的分 = 这一阶段最新一次评测里通过用例的分值之和 ÷ 全部用例分值之和 ×
这一行的分值，四舍五入到整数。分母为 0（一个用例都没有分值）时这一行给 0。

评测还没出结果、或者失败时，``auto_score.compose_review`` 给这一行 0 并标 ``pending``，
老师可以用 ``testsOverride``（带原因）手动给分。怎么把各自动行的分和老师填的手动分
合成一条评审的总分，在 ``auto_score`` 里；这里只有那一个折算公式，是纯函数。
"""


def _rounded(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` 四舍五入到整数，0.5 向上。

    ``(2n + d) // (2d)`` 就是 ``floor(n/d + 1/2)``，全程整数运算，不会因为浮点数
    在 .5 上抖到少一分。
    """
    if denominator <= 0:
        return 0
    return (2 * numerator + denominator) // (2 * denominator)


def tests_line_score(
    points: int, passed_points: int | None, total_points: int | None
) -> int | None:
    """测试点那一项自动算出来的分：``通过用例的分值之和 ÷ 全部用例分值之和 × 这一项
    的分值``，四舍五入。

    没有评测结果（``passed_points``/``total_points`` 为空）、或者一个用例都没有分值
    （分母为 0）时给 ``None``：这一行还没有结果，``compose_review`` 会当 0 并标
    ``pending``。
    """
    if passed_points is None or total_points is None or total_points <= 0:
        return None
    return min(max(points, 0), _rounded(points * passed_points, total_points))
