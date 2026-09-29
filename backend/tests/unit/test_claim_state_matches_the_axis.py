"""卡片那格（``claim_state``）与完成状态那条轴（``completion_status_for``）不许分叉。

两者是同一个问题的两种粒度：轴有五档、带截止时间；卡片那格只有四档、不看截止时间。
所以 ``claim_state`` 不自己排一遍优先级，而是把判决折成同一组事实再交给
``completion_status_for``。这条用例把「判决 → 档位」的每一种组合都过一遍，锁住这层
关系：谁哪天在里面另写一套 if，这里就红。
"""

from itertools import product

import pytest

from app.domain.task.submission_state import (
    CLAIM_IN_PROGRESS,
    CLAIM_PASSED,
    CLAIM_REJECTED,
    CLAIM_SUBMITTED,
    COMPLETION_STATUS_NOT_SUBMITTED,
    COMPLETION_STATUS_PENDING_REVIEW,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
    COMPLETION_STATUS_SUCCESS,
    claim_state,
    completion_status_for,
)

#: 完成状态的取值 → 卡片那格。``FAILED`` 不在这里：卡片那格不看截止时间。
CLAIM_BY_STATUS = {
    COMPLETION_STATUS_SUCCESS: CLAIM_PASSED,
    COMPLETION_STATUS_PENDING_REVIEW: CLAIM_SUBMITTED,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE: CLAIM_REJECTED,
    COMPLETION_STATUS_NOT_SUBMITTED: CLAIM_IN_PROGRESS,
}

VERDICTS = [True, False, None]


def _label(verdict):
    return "pass" if verdict is True else "back" if verdict is False else "queue"


@pytest.mark.parametrize(
    "verdicts",
    [list(combo) for size in range(0, 4) for combo in product(VERDICTS, repeat=size)],
    ids=lambda verdicts: "[" + ",".join(_label(v) for v in verdicts) + "]",
)
def test_claim_state_is_the_axis_read_at_four_levels(verdicts):
    """同一个判决集合，两边算出来必须是同一档。"""
    status = completion_status_for(
        has_a_passed_submission=any(v is True for v in verdicts),
        has_a_submission_in_the_queue=any(v is None for v in verdicts),
        has_a_live_submission=bool(verdicts),
        past_deadline=False,
    )
    assert claim_state(verdicts) == CLAIM_BY_STATUS[status]


def test_the_named_tiers_are_what_the_cards_show():
    """四档的名字与先后：任一版判过就是「通过」，退回后重交是「已提交」。"""
    assert claim_state([]) == CLAIM_IN_PROGRESS
    assert claim_state([False]) == CLAIM_REJECTED
    assert claim_state([True]) == CLAIM_PASSED
    assert claim_state([None]) == CLAIM_SUBMITTED
    assert claim_state([False, None]) == CLAIM_SUBMITTED
    assert claim_state([False, True]) == CLAIM_PASSED
    assert claim_state([True, None]) == CLAIM_PASSED


def test_a_verdict_list_is_iterated_once():
    """``verdicts`` 只保证是可迭代的，传进来一个生成器也得算对。"""
    assert claim_state(iter([False, None])) == CLAIM_SUBMITTED
