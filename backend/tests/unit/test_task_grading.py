"""评分怎么合成（方案 §7 分数怎么合成、§分数怎么合成下面那张例子表）。

``app.domain.task.grading`` 是纯函数：读进来的是评分要点、老师送来的逐项得分、一
次评测结果的分子分母，没有任何数据库访问 —— 于是方案里那几行例子都能逐条对着试：

* 60（测试点）/20/20，用例分 70/100 → 42、20、18，总分 80；
* 老师把测试那一项从小算出来的 42 手动调到 48，写明原因 → 20、18、总分 86；
* 纯编程题只用一项 100（测试点），用例分 85/100 → 85，总分 85；
* 评分要点合计只有 50（30 测试点 + 20 报告），全过 → 30、15，总分 90（45÷50×100）。

平移一位（.5）的舍入单独钉一条：``(2n + d) // (2d)`` 全程整数，永远 0.5 向上，不
会因为浮点在 .5 上抖到少一分。
"""

from types import SimpleNamespace

import pytest

from app.core.errors import BadRequestError
from app.domain.task.grading import (
    TEACHER,
    TESTS,
    compose_total,
    line_scores_from_api,
    line_scores_to_api,
    settle_line_scores,
)
from app.domain.task.grading import (
    tests_line_score as weighted_score,
)


def _lines(*rows) -> list[SimpleNamespace]:
    """评分要点的一行，按 ``(分值, 评分方式)`` 给，序号就是它的下标。

    ``settle_line_scores`` 读的是库里的行（``TaskRubricItem``：``index``、
    ``points``、``source``、``item``）；这里拿一个够读的替身，不去建表。
    """
    return [
        SimpleNamespace(
            index=i,
            item=f"第 {i + 1} 项",
            points=points,
            source=source,
        )
        for i, (points, source) in enumerate(rows)
    ]


class TestTestsLineScore:
    """``TESTS`` 那一项的得分 = 通过用例的分值之和 ÷ 全部用例分值之和 × 这一项的分值。"""

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
        assert weighted_score(points, passed, total) == want

    def test_rounding_is_half_up(self):
        # 1 / 3 × 100 = 33.33… → 33；2 / 3 × 100 = 66.66… → 67。
        assert weighted_score(100, 1, 3) == 33
        assert weighted_score(100, 2, 3) == 67
        # 正好 .5：1 / 8 × ... 挑一个正好落半的：10 × 1 / 4 = 2.5 → 3。
        assert weighted_score(10, 1, 4) == 3
        assert weighted_score(10, 3, 4) == 8  # 7.5 → 8

    def test_no_run_leaves_it_blank(self):
        # 还没有评测结果：这一项空着，等结果或者老师手填。
        assert weighted_score(60, None, None) is None
        # 用例一个分值都没有（分母 0）：也是空着，不是 0。
        assert weighted_score(60, 0, 0) is None

    def test_never_exceeds_the_line(self):
        # 分子分母送来离谱的数（半路改题之类）时封顶到这一项的分值。
        assert weighted_score(60, 200, 100) == 60
        assert weighted_score(60, 0, 100) == 0


class TestTheWorkedExamples:
    """方案那张表逐行走一遍。"""

    def test_correctness_is_a_tests_line(self):
        lines = _lines((60, TESTS), (20, TEACHER), (20, TEACHER))
        settled = settle_line_scores(
            lines,
            [
                {"index": 1, "score": 20},
                {"index": 2, "score": 18},
            ],
            accepted=True,
            passed_points=70,
            total_points=100,
        )
        assert [(s.index, s.score, s.source) for s in settled] == [
            (0, 42, TESTS),
            (1, 20, TEACHER),
            (2, 18, TEACHER),
        ]
        assert compose_total(settled) == 80

    def test_a_manual_adjust_needs_a_reason(self):
        lines = _lines((60, TESTS), (20, TEACHER), (20, TEACHER))
        settled = settle_line_scores(
            lines,
            [
                {"index": 0, "score": 48, "adjusted": True, "note": "手算的"},
                {"index": 1, "score": 20},
                {"index": 2, "score": 18},
            ],
            accepted=True,
            passed_points=70,
            total_points=100,
        )
        assert settled[0].score == 48
        assert settled[0].adjusted is True
        assert settled[0].note == "手算的"
        assert compose_total(settled) == 86

    def test_a_pure_programming_task(self):
        lines = _lines((100, TESTS))
        settled = settle_line_scores(
            lines, None, accepted=True, passed_points=85, total_points=100
        )
        assert [s.score for s in settled] == [85]
        assert compose_total(settled) == 85

    def test_lines_that_do_not_add_up_to_a_hundred(self):
        # 合计 50（30 测试点 + 20 报告），全过 → 30 + 15，总分派生 45÷50×100 = 90。
        lines = _lines((30, TESTS), (20, TEACHER))
        settled = settle_line_scores(
            lines,
            [{"index": 1, "score": 15}],
            accepted=True,
            passed_points=100,
            total_points=100,
        )
        assert [s.score for s in settled] == [30, 15]
        assert compose_total(settled) == 90


class TestTestsLineIsNotAutoAccepted:
    """测试那一项**不自动判通过**：没结果时它空着，两边都不计。"""

    def test_no_run_and_no_manual_score_leaves_it_blank(self):
        lines = _lines((60, TESTS), (40, TEACHER))
        settled = settle_line_scores(lines, [{"index": 1, "score": 40}], accepted=True)
        assert settled[0].score is None
        # 空着的一项：得分与分值两边都不计 —— 40 / 40 × 100 = 100。
        assert compose_total(settled) == 100

    def test_nothing_scored_at_all_is_zero(self):
        lines = _lines((60, TESTS), (40, TEACHER))
        assert compose_total(settle_line_scores(lines, None, accepted=False)) == 0

    def test_rejected_review_leaves_teacher_lines_unscored(self):
        # 驳回时照现在的规则不给分，测试结果照样留着（这里没送结果）。
        lines = _lines((60, TESTS), (40, TEACHER))
        settled = settle_line_scores(
            lines, None, accepted=False, passed_points=70, total_points=100
        )
        # 测试那一项有结果就填上了，即便这一份评审是驳回。
        assert settled[0].score == 42
        assert settled[1].score is None


class TestRefusals:
    """送进来的逐项得分不对就拒掉，理由带文案键。"""

    def test_a_manual_tests_score_must_be_marked_adjusted(self):
        lines = _lines((60, TESTS))
        with pytest.raises(BadRequestError):
            settle_line_scores(lines, [{"index": 0, "score": 48}], accepted=True)

    def test_a_manual_tests_score_must_carry_a_reason(self):
        lines = _lines((60, TESTS))
        with pytest.raises(BadRequestError):
            settle_line_scores(
                lines,
                [{"index": 0, "score": 48, "adjusted": True, "note": "  "}],
                accepted=True,
            )

    def test_a_score_over_the_line_is_refused(self):
        lines = _lines((20, TEACHER))
        with pytest.raises(BadRequestError):
            settle_line_scores(lines, [{"index": 0, "score": 21}], accepted=True)

    def test_a_score_out_of_range_is_refused(self):
        lines = _lines((20, TEACHER))
        with pytest.raises(BadRequestError):
            settle_line_scores(lines, [{"index": 0, "score": -1}], accepted=True)

    def test_an_unknown_line_index_is_refused(self):
        lines = _lines((20, TEACHER))
        with pytest.raises(BadRequestError):
            settle_line_scores(lines, [{"index": 9, "score": 1}], accepted=True)

    def test_an_accepted_review_must_score_every_teacher_line(self):
        lines = _lines((60, TESTS), (40, TEACHER))
        with pytest.raises(BadRequestError):
            settle_line_scores(
                lines, None, accepted=True, passed_points=70, total_points=100
            )


class TestApiShape:
    """存的形状就是送出去的形状，读回来逐项照抄。"""

    def test_round_trip(self):
        lines = _lines((60, TESTS), (20, TEACHER))
        settled = settle_line_scores(
            lines,
            [{"index": 1, "score": 15}],
            accepted=True,
            passed_points=70,
            total_points=100,
        )
        sent = line_scores_to_api(settled)
        assert sent == [
            {
                "index": 0,
                "points": 60,
                "source": TESTS,
                "score": 42,
                "adjusted": False,
                "note": "",
            },
            {
                "index": 1,
                "points": 20,
                "source": TEACHER,
                "score": 15,
                "adjusted": False,
                "note": "",
            },
        ]
        assert line_scores_from_api(sent) == settled

    def test_an_empty_column_reads_as_nothing(self):
        assert line_scores_from_api(None) == []
        assert line_scores_from_api([]) == []

    def test_a_broken_row_is_stepped_over(self):
        # 库里那一列是 JSONB，手改或旧版数据里可能有一行读不出 index/points：
        # 跳过它，别让整份评审读不出来。
        read = line_scores_from_api([{"score": 1}, {"index": 0, "points": 10}])
        assert [(s.index, s.points) for s in read] == [(0, 10)]
