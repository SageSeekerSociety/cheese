"""测试点那一项（``tests``）的自动分怎么算（方案 §7）。

``app.domain.task.grading`` 是纯函数：读进来的是这一项的分值和一次评测结果的分子
分母，没有任何数据库访问 —— 于是方案里那几行例子都能逐条对着试。合成一条评审的总
分（各项自动分 + 老师填的手动分）在 ``auto_score`` 里，走真库，见
``tests/integration/test_task_review_line_scores.py``。

平移一位（.5）的舍入单独钉一条：``(2n + d) // (2d)`` 全程整数，永远 0.5 向上，不
会因为浮点在 .5 上抖到少一分。
"""

import pytest

from app.domain.task.grading import tests_line_score as line_score


class TestTestsLineScore:
    """``tests`` 那一项的分 = 通过用例的分值之和 ÷ 全部用例分值之和 × 这一项的分值。"""

    @pytest.mark.parametrize(
        ("points", "passed", "total", "want"),
        [
            # 方案例子：60 分那一项，用例分 70/100。
            (60, 70, 100, 42),
            # 纯编程题：100 分那一项，用例分 85/100。
            (100, 85, 100, 85),
            # 全过就是满分。
            (30, 100, 100, 30),
            # 一个用例都没过。
            (60, 0, 100, 0),
        ],
    )
    def test_weighted_by_points(self, points, passed, total, want):
        assert line_score(points, passed, total) == want

    def test_rounding_is_half_up(self):
        # 1 / 3 × 100 = 33.33… → 33；2 / 3 × 100 = 66.66… → 67。
        assert line_score(100, 1, 3) == 33
        assert line_score(100, 2, 3) == 67
        # 正好 .5：10 × 1 / 4 = 2.5 → 3。
        assert line_score(10, 1, 4) == 3
        assert line_score(10, 3, 4) == 8  # 7.5 → 8

    def test_no_run_has_no_score(self):
        # 还没有评测结果：这一项没有分，``compose_review`` 会当 0 并标 pending。
        assert line_score(60, None, None) is None
        # 用例一个分值都没有（分母 0）：也是没有分，不是 0 分。
        assert line_score(60, 0, 0) is None

    def test_never_exceeds_the_line(self):
        # 分子分母送来离谱的数（半路改题之类）时封顶到这一项的分值。
        assert line_score(60, 200, 100) == 60
        assert line_score(60, 0, 100) == 0
