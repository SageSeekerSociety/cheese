"""按阶段渐进式披露：阶段判定 + 阶段 skill 的选取与注入。

Functional only — these exercise the public behaviour (given a topic's shape and
its open cards, which stage is it in, and what actually lands in the system
prompt), not the internals of how the markdown is parsed.
"""

import pytest

from app.domain.agent.chat import _OPEN_CARD_HINTS, _OPEN_CARD_STATUSES
from app.domain.agent.chat import _build_system_prompt as build_prompt
from app.domain.agent.skills import (
    available_skills,
    load_scenario,
    skills_for_scenario,
)
from app.domain.agent.stages import TopicStage, resolve_stage, stage_scenario
from app.domain.review.models import AcceptStatus
from app.domain.topic.models import TopicKind, TopicStatus


def _stage(kind=TopicKind.task, status=TopicStatus.active, cards=()) -> TopicStage:
    return resolve_stage(kind=kind, status=status, card_statuses=cards)


# --- 阶段判定 ---------------------------------------------------------------


def test_room_without_cards_is_delegating():
    assert _stage(kind=TopicKind.topic) is TopicStage.delegating
    assert _stage(kind=TopicKind.root) is TopicStage.delegating


def test_task_without_cards_is_working():
    assert _stage(kind=TopicKind.task) is TopicStage.working
    # `subtopic` is task's historical value — same stage, not a hole.
    assert _stage(kind=TopicKind.subtopic) is TopicStage.working


def test_archived_task_is_merged():
    assert _stage(status=TopicStatus.archived) is TopicStage.merged


@pytest.mark.parametrize(
    ("card_status", "expected"),
    [
        (AcceptStatus.pending_gate, TopicStage.gate),
        (AcceptStatus.gate_failed, TopicStage.gate),
        (AcceptStatus.pending, TopicStage.awaiting),
        (AcceptStatus.pr_open, TopicStage.pr_open),
        (AcceptStatus.conflict, TopicStage.conflict),
    ],
)
def test_open_card_drives_the_stage(card_status, expected):
    assert _stage(cards=[card_status]) is expected


def test_a_room_holding_an_open_card_reports_the_card_stage():
    """卡状态比话题形态更具体：房间里真有一张活卡时，说卡的事。"""
    assert _stage(kind=TopicKind.topic, cards=[AcceptStatus.pr_open]) is (
        TopicStage.pr_open
    )


def test_needs_my_hands_wins_over_waiting_on_a_human():
    """同时有多张卡时，要芝士动手的那张优先于纯等待的那张。"""
    assert _stage(cards=[AcceptStatus.pending, AcceptStatus.gate_failed]) is (
        TopicStage.gate
    )
    assert _stage(cards=[AcceptStatus.pending, AcceptStatus.conflict]) is (
        TopicStage.conflict
    )
    assert _stage(cards=[AcceptStatus.pr_open, AcceptStatus.pending]) is (
        TopicStage.pr_open
    )


# --- 每个阶段都真的有内容可注入 ---------------------------------------------


@pytest.mark.parametrize("stage", list(TopicStage))
def test_every_stage_resolves_to_a_non_empty_guide(stage):
    """一个阶段没有对应 skill = 芝士在那一段里静默地什么都收不到。"""
    guide = load_scenario(stage_scenario(stage))
    assert guide.strip(), f"stage {stage} has no skill tagged {stage_scenario(stage)}"


@pytest.mark.parametrize("stage", list(TopicStage))
def test_stage_guides_stay_small(stage):
    """渐进式披露的意义就在于每段都小；现有 skill 上限 ~3.7KB，留一倍余量。"""
    guide = load_scenario(stage_scenario(stage))
    assert len(guide.encode()) < 8000, f"stage {stage} guide is too fat"


def test_scenario_selection_uses_the_frontmatter_field():
    """`scenarios:` 从装饰性字段变成真正的选择器。"""
    # The pre-existing accept-routing skill is tagged `[accept]` and always was.
    assert "accept-routing" in skills_for_scenario("accept")
    assert "accept-routing" not in skills_for_scenario(stage_scenario(TopicStage.gate))


def test_one_skill_can_serve_several_stages():
    """多对多是选 scenario 而不是硬编码名字列表的理由。"""
    serving = [
        stage
        for stage in TopicStage
        if "parent-link" in skills_for_scenario(stage_scenario(stage))
    ]
    assert len(serving) > 1


def test_unknown_scenario_is_empty_not_an_error():
    assert load_scenario("stage:does-not-exist") == ""


def test_every_stage_skill_is_registered_by_name():
    """文件名和 frontmatter 的 name 对不上会让 load_skills 静默丢内容。"""
    known = available_skills()
    for stage in TopicStage:
        for name in skills_for_scenario(stage_scenario(stage)):
            assert name in known


# --- 两条硬要求的内容真的在 working 阶段里 ----------------------------------


def test_working_stage_covers_when_to_hand_off_and_the_approver_token():
    """简报硬要求：这两条读完就得知道该怎么做，不能散落在别处。"""
    guide = load_scenario(stage_scenario(TopicStage.working))
    assert "accept-request" in guide  # 怎么递
    assert "只读" in guide  # 为什么自己推不了
    assert "cheese ask" in guide  # GitHub 账号校验回环


def test_pr_open_stage_tells_the_agent_to_just_keep_committing():
    guide = load_scenario(stage_scenario(TopicStage.pr_open))
    assert "本分支" in guide


# --- 注入进 system prompt ---------------------------------------------------


def test_stage_guide_lands_in_the_system_prompt():
    prompt = build_prompt("base", "", None, [], stage_guide="阶段内容XYZ")
    assert "阶段内容XYZ" in prompt
    assert "当前阶段的操作说明" in prompt


def test_no_stage_guide_adds_no_section():
    prompt = build_prompt("base", "", None, [])
    assert "当前阶段的操作说明" not in prompt


# --- pr_open 的盲飞防护回归 -------------------------------------------------


def test_pr_open_is_an_open_card_status_with_a_hint():
    """卡进入 PR 迭代后，芝士必须在 turn-meta 里看得到——否则它不知道
    自己已经有了一条通往 GitHub 的通道。"""
    assert AcceptStatus.pr_open in _OPEN_CARD_STATUSES
    assert AcceptStatus.pr_open in _OPEN_CARD_HINTS


def test_every_open_card_status_has_a_hint():
    """漏一个就是那一段静默无提示。"""
    for status in _OPEN_CARD_STATUSES:
        assert status in _OPEN_CARD_HINTS
