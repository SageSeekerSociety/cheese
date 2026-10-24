"""卡片那格（``claim_state``）与完成状态那条轴（``completion_status_for``）不许分叉。

两者是同一个问题的两种粒度：轴有五档、带时限；卡片那格只有四档、不看时限。所以
``claim_state`` 不自己排一遍优先级，而是把各阶段的状态交给 ``completion_status_for``。
这条用例把各阶段状态的每一种组合都过一遍，锁住这层关系；再锁住「一个阶段看它最新的
那一版」和「每个阶段都通过才算通过」。
"""

from itertools import product

import pytest

from app.domain.task.submission_state import (
    CLAIM_IN_PROGRESS,
    CLAIM_PASSED,
    CLAIM_REJECTED,
    CLAIM_SUBMITTED,
    COMPLETION_STATUS_FAILED,
    COMPLETION_STATUS_NOT_SUBMITTED,
    COMPLETION_STATUS_PENDING_REVIEW,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
    COMPLETION_STATUS_SUCCESS,
    STAGE_NOT_SUBMITTED,
    STAGE_PASSED,
    STAGE_PENDING_REVIEW,
    STAGE_REJECTED,
    STAGE_STATUSES,
    HandedIn,
    claim_state,
    completion_status_for,
    stage_states,
)

#: 完成状态的取值 → 卡片那格。``FAILED`` 不在这里：卡片那格不看时限。
CLAIM_BY_STATUS = {
    COMPLETION_STATUS_SUCCESS: CLAIM_PASSED,
    COMPLETION_STATUS_PENDING_REVIEW: CLAIM_SUBMITTED,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE: CLAIM_REJECTED,
    COMPLETION_STATUS_NOT_SUBMITTED: CLAIM_IN_PROGRESS,
}


@pytest.mark.parametrize(
    "states",
    [list(c) for size in range(1, 4) for c in product(STAGE_STATUSES, repeat=size)],
    ids=lambda states: ",".join(states),
)
def test_claim_state_is_the_axis_read_at_four_levels(states):
    """同一组阶段状态，两边算出来必须是同一档。"""
    status = completion_status_for(states, out_of_time=False)
    assert claim_state(states) == CLAIM_BY_STATUS[status]


def _one_stage(*verdicts):
    return stage_states(
        [HandedIn(stage=0, version=v, verdict=x) for v, x in enumerate(verdicts, 1)],
        1,
    )


def test_a_stage_stands_where_its_latest_version_stands():
    """退回后重交是「待批改」；通过之后又交了一版，等的是那一版的批改。"""
    assert _one_stage() == [STAGE_NOT_SUBMITTED]
    assert _one_stage(False) == [STAGE_REJECTED]
    assert _one_stage(True) == [STAGE_PASSED]
    assert _one_stage(False, None) == [STAGE_PENDING_REVIEW]
    assert _one_stage(False, True) == [STAGE_PASSED]
    assert _one_stage(True, None) == [STAGE_PENDING_REVIEW]


def test_a_claim_passes_only_when_every_stage_has():
    states = stage_states(
        [
            HandedIn(stage=0, version=1, verdict=True),
            HandedIn(stage=1, version=2, verdict=False),
            HandedIn(stage=1, version=3, verdict=True),
        ],
        3,
    )
    assert states == [STAGE_PASSED, STAGE_PASSED, STAGE_NOT_SUBMITTED]
    assert claim_state(states) == CLAIM_IN_PROGRESS
    assert claim_state([STAGE_PASSED] * 3) == CLAIM_PASSED


def test_out_of_time_fails_a_claim_with_nothing_in_hand_but_not_one_waiting():
    assert (
        completion_status_for([STAGE_PASSED, STAGE_NOT_SUBMITTED], out_of_time=True)
        == COMPLETION_STATUS_FAILED
    )
    assert (
        completion_status_for([STAGE_PASSED, STAGE_PENDING_REVIEW], out_of_time=True)
        == COMPLETION_STATUS_PENDING_REVIEW
    )
    assert (
        completion_status_for([STAGE_PASSED, STAGE_PASSED], out_of_time=True)
        == COMPLETION_STATUS_SUCCESS
    )
