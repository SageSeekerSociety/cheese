"""额度用量那一行的两种给分，和评分要点里自动行的校验。"""

import pytest

from app.core.errors import BadRequestError
from app.domain.task.rubric import rubric_lines
from app.domain.task.usage_score import rank_scores, threshold_score


@pytest.mark.parametrize(
    ("credits", "expected"),
    [
        (0.0, 20),
        (50.0, 20),  # 正好到满分线
        (76.2, 17),  # 20 × 123.8 ÷ 150 = 16.5 → 17
        (120.0, 11),  # 20 × 80 ÷ 150 = 10.7 → 11
        (132.4, 9),
        (200.0, 0),  # 正好到 0 分线
        (214.9, 0),
    ],
)
def test_threshold_is_linear_between_the_two_lines(credits, expected):
    assert threshold_score(credits, 20, 50, 200) == expected


def test_rank_scores_by_how_many_spent_more():
    scores = rank_scores({1: 38.6, 2: 50.0, 3: 76.2, 4: 132.4, 5: 214.9}, 20)
    assert {k: v[0] for k, v in scores.items()} == {1: 20, 2: 15, 3: 10, 4: 5, 5: 0}
    assert scores[4] == (5, 4, 5)


def test_rank_ties_share_a_place_and_a_score():
    scores = rank_scores({1: 10.0, 2: 10.0, 3: 30.0}, 20)
    # 两人并列第 1，都只比 1 人用得少：20 × 1 ÷ 2 = 10。
    assert scores[1] == scores[2] == (10, 1, 3)
    assert scores[3] == (0, 3, 3)


def test_rank_of_one_claim_is_full_points():
    assert rank_scores({7: 99.0}, 20) == {7: (20, 1, 1)}


USAGE = {
    "item": "",
    "points": 20,
    "kind": "usage",
    "config": {"mode": "threshold", "full": 50, "zero": 200},
}


def test_a_usage_line_is_named_and_kept():
    lines = rubric_lines([{"item": "正确性", "points": 80}, USAGE])
    assert lines[1].item == "额度用量"
    assert lines[1].automatic
    assert dict(lines[1].config) == {"mode": "threshold", "full": 50.0, "zero": 200.0}
    assert not lines[0].automatic


def test_a_rank_line_keeps_only_its_mode():
    line = {**USAGE, "config": {"mode": "rank", "full": 1}}
    lines = rubric_lines([{"item": "正确性", "points": 80}, line])
    assert dict(lines[1].config) == {"mode": "rank"}


@pytest.mark.parametrize(
    "rows",
    [
        [{"item": "正确性", "points": 70}, USAGE],  # 合计 90
        [{"item": "正确性", "points": 60}, USAGE, USAGE],  # 两行额度
        [
            {"item": "正确性", "points": 80},
            {**USAGE, "config": {"mode": "threshold", "full": 200, "zero": 50}},
        ],
        [{"item": "正确性", "points": 80}, {**USAGE, "config": {"mode": "x"}}],
        [{"item": "正确性", "points": 80, "kind": "tests"}],
    ],
)
def test_bad_automatic_rubrics_are_refused(rows):
    with pytest.raises(BadRequestError):
        rubric_lines(rows)


def test_a_rubric_without_automatic_lines_need_not_total_100():
    assert len(rubric_lines([{"item": "正确性", "points": 7}])) == 1
