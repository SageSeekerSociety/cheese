"""Turn-meta prompt header (盲飞防护): at turn start the agent learns its time
budget, whether it's a resumed turn, disk headroom, and this topic's open
accept cards — facts it previously had no way to see."""

from types import SimpleNamespace

from app.domain.agent.chat import (
    _build_system_prompt,
    _turn_meta_lines,
    _workspace_disk,
)
from app.domain.review.models import AcceptStatus


def test_budget_and_status_hint_always_present() -> None:
    lines = _turn_meta_lines(budget_s=900, is_resume=False, disk=None, open_cards=None)
    joined = "\n".join(lines)
    assert "15 分钟" in joined
    assert "cheese status" in joined
    assert "本轮是自动续跑" not in joined


def test_resume_line_present_on_resumed_turn() -> None:
    lines = _turn_meta_lines(budget_s=900, is_resume=True, disk=None, open_cards=None)
    assert any("本轮是自动续跑" in ln for ln in lines)


def test_disk_line_and_pressure_warning() -> None:
    total = 100 * 2**30
    relaxed = _turn_meta_lines(
        budget_s=900, is_resume=False, disk=(50 * 2**30, total), open_cards=None
    )
    assert any("已用 50%" in ln for ln in relaxed)
    assert not any("空间紧张" in ln for ln in relaxed)
    tight = _turn_meta_lines(
        budget_s=900, is_resume=False, disk=(5 * 2**30, total), open_cards=None
    )
    assert any("空间紧张" in ln for ln in tight)


def test_open_card_lines() -> None:
    cards = [
        SimpleNamespace(status=AcceptStatus.gate_failed, reviewer_handle="alice"),
        SimpleNamespace(status=AcceptStatus.pending, reviewer_handle="bob"),
    ]
    joined = "\n".join(
        _turn_meta_lines(budget_s=900, is_resume=False, disk=None, open_cards=cards)
    )
    assert "闸门检查未过" in joined
    assert "@bob" in joined


def test_prompt_section_appended_only_when_meta_present() -> None:
    with_meta = _build_system_prompt("base", "", None, [], turn_meta=["- x"])
    assert "## 本轮运行环境" in with_meta
    without = _build_system_prompt("base", "", None, [])
    assert "## 本轮运行环境" not in without


def test_workspace_disk_missing_root_is_none() -> None:
    assert _workspace_disk("/definitely/not/a/real/path") is None
