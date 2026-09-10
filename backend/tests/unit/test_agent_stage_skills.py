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


def _stage(finished=False, cards=()) -> TopicStage:
    return resolve_stage(finished=finished, card_statuses=cards)


# --- 阶段判定 ---------------------------------------------------------------


def test_a_place_with_no_card_is_delegating():
    """派活和干活是同一段：跑轮次的只有房间，它同时在做这两件事。

    分开注入过一次，那是每条活各有一个会话的时候。现在一条活是房间会话里的一个
    分身，没有 system prompt 可注入，而房间在派活时读不到该怎么交付——递卡、
    采纳者 token 这些它非知道不可的东西，就落在没人拿得到的那一段里。
    """
    assert _stage() is TopicStage.delegating


def test_finished_work_is_merged():
    assert _stage(finished=True) is TopicStage.archived


@pytest.mark.parametrize(
    ("card_status", "expected"),
    [
        (AcceptStatus.pending_gate, TopicStage.gate),
        (AcceptStatus.gate_failed, TopicStage.gate),
        (AcceptStatus.pending, TopicStage.awaiting),
        (AcceptStatus.conflict, TopicStage.conflict),
    ],
)
def test_open_card_drives_the_stage(card_status, expected):
    assert _stage(cards=[card_status]) is expected


def test_a_room_holding_an_open_card_reports_the_card_stage():
    """卡状态比话题形态更具体：房间里真有一张活卡时，说卡的事。"""
    assert _stage(cards=[AcceptStatus.pending]) is TopicStage.awaiting


def test_needs_my_hands_wins_over_waiting_on_a_human():
    """同时有多张卡时，要芝士动手的那张优先于纯等待的那张。"""
    assert _stage(cards=[AcceptStatus.pending, AcceptStatus.gate_failed]) is (
        TopicStage.gate
    )
    assert _stage(cards=[AcceptStatus.pending, AcceptStatus.conflict]) is (
        TopicStage.conflict
    )


# --- 每个阶段都真的有内容可注入 ---------------------------------------------


@pytest.mark.parametrize("stage", list(TopicStage))
def test_every_stage_resolves_to_a_non_empty_guide(stage):
    """一个阶段没有对应 skill = 芝士在那一段里静默地什么都收不到。"""
    guide = load_scenario(stage_scenario(stage))
    assert guide.strip(), f"stage {stage} has no skill tagged {stage_scenario(stage)}"


@pytest.mark.parametrize("stage", list(TopicStage))
def test_stage_guides_stay_small(stage):
    """渐进式披露的意义就在于每段都小。

    上限比单份 skill 宽，因为「还没递卡」那一段是两份拼的——派活和交付本来就是
    房间同时在做的两件事，分开注入等于让它读不到其中一件。
    """
    guide = load_scenario(stage_scenario(stage))
    assert len(guide.encode()) < 12000, f"stage {stage} guide is too fat"


def test_scenario_selection_uses_the_frontmatter_field():
    """`scenarios:` 从装饰性字段变成真正的选择器。"""
    # The pre-existing accept-routing skill is tagged `[accept]` and always was.
    assert "accept-routing" in skills_for_scenario("accept")
    assert "accept-routing" not in skills_for_scenario(stage_scenario(TopicStage.gate))


def test_one_scenario_can_be_served_by_several_skills():
    """多对多是选 scenario 而不是硬编码名字列表的理由。

    「还没递卡」这一段是两份 skill 拼出来的——派活怎么派，以及这批活怎么交付。
    """
    serving = skills_for_scenario(stage_scenario(TopicStage.delegating))
    assert {"stage-delegating"} == set(serving), serving


def test_unknown_scenario_is_empty_not_an_error():
    assert load_scenario("stage:does-not-exist") == ""


def test_every_stage_skill_is_registered_by_name():
    """文件名和 frontmatter 的 name 对不上会让 load_skills 静默丢内容。"""
    known = available_skills()
    for stage in TopicStage:
        for name in skills_for_scenario(stage_scenario(stage)):
            assert name in known


# --- 两条硬要求的内容真的在「还没递卡」这一段里 ------------------------------


def test_the_pre_card_stage_covers_when_to_hand_off_and_the_github_channel():
    """这几条读完就得知道该怎么做，不能散落在别处。"""
    guide = load_scenario(stage_scenario(TopicStage.delegating))
    assert "accept-request" in guide  # 怎么递
    assert "独立" in guide
    assert "cheese ready" in guide
    assert "永远不能" not in guide


def test_awaiting_stage_tells_the_agent_how_prs_move_now():
    guide = load_scenario(stage_scenario(TopicStage.awaiting))
    assert "push-fix" in guide  # 提交不会自己上 PR，得说怎么上
    assert "实际授权" in guide
    assert "token 是只读的" not in guide


# --- 注入进 system prompt ---------------------------------------------------


def test_stage_guide_lands_in_the_system_prompt():
    prompt = build_prompt("base", "", None, [], stage_guide="阶段内容XYZ")
    assert "阶段内容XYZ" in prompt
    assert "当前阶段的操作说明" in prompt


def test_no_stage_guide_adds_no_section():
    prompt = build_prompt("base", "", None, [])
    assert "当前阶段的操作说明" not in prompt


def test_every_open_card_status_has_a_hint():
    """漏一个就是那一段静默无提示。"""
    for status in _OPEN_CARD_STATUSES:
        assert status in _OPEN_CARD_HINTS
