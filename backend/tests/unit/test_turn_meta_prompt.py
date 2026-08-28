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


def test_no_countdown_is_ever_claimed() -> None:
    """turn 活跃度检测 (2026-08-09): the line must not claim a fixed minute
    budget or "到点会被中断" — that wording was observed making the agent rush
    against what is only meant to be a wedged-turn safety net. A session ends on
    real idleness or a many-hours ceiling, never on a minute count."""
    lines = _turn_meta_lines(is_resume=False, disk=None, open_cards=None)
    joined = "\n".join(lines)
    assert "分钟" not in joined
    assert "到点会被中断" not in joined
    assert "活跃度检测" in joined
    assert "cheese status" in joined  # the unconditional hint still applies
    assert "本轮是自动续跑" not in joined


def test_resume_line_present_on_resumed_turn() -> None:
    lines = _turn_meta_lines(is_resume=True, disk=None, open_cards=None)
    assert any("本轮是自动续跑" in ln for ln in lines)


def test_disk_line_and_pressure_warning() -> None:
    total = 100 * 2**30
    relaxed = _turn_meta_lines(
        is_resume=False,
        disk=(50 * 2**30, total),
        open_cards=None,
    )
    assert any("已用 50%" in ln for ln in relaxed)
    assert not any("空间紧张" in ln for ln in relaxed)
    tight = _turn_meta_lines(
        is_resume=False,
        disk=(5 * 2**30, total),
        open_cards=None,
    )
    assert any("空间紧张" in ln for ln in tight)


def test_open_card_lines() -> None:
    cards = [
        SimpleNamespace(status=AcceptStatus.gate_failed, reviewer_handle="alice"),
        SimpleNamespace(status=AcceptStatus.pending, reviewer_handle="bob"),
    ]
    joined = "\n".join(
        _turn_meta_lines(
            is_resume=False,
            disk=None,
            open_cards=cards,
        )
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


def test_the_machines_size_is_stated_when_the_backend_knows_it() -> None:
    """An agent cannot read its own cgroup limit. Without being told, a build the
    kernel OOM-kills reads as a broken toolchain rather than a small box — and
    the usual reaction, retuning --max-old-space-size, cannot help, because V8
    just climbs until the cgroup kills it again."""
    lines = _turn_meta_lines(
        is_resume=False,
        disk=None,
        open_cards=None,
        sandbox=(2048, 2),
    )
    joined = "\n".join(lines)
    assert "2GB" in joined
    assert "2 核" in joined
    assert "OOM" in joined


def test_a_backend_that_does_not_know_its_size_says_nothing() -> None:
    """An enrolled machine belongs to someone else and the platform does not set
    its limits. Inventing a number there would be worse than staying quiet: the
    agent would skip work it could actually have done."""
    lines = _turn_meta_lines(is_resume=False, disk=None, open_cards=None)
    joined = "\n".join(lines)
    assert "内存" not in joined
    assert "OOM" not in joined


def test_a_fractional_gigabyte_is_not_rounded_to_a_lie() -> None:
    """512m must not print as '1GB' — the number is only useful if an agent can
    weigh a command against it."""
    lines = _turn_meta_lines(
        is_resume=False,
        disk=None,
        open_cards=None,
        sandbox=(1536, 1),
    )
    assert "1.5GB" in "\n".join(lines)


def test_a_machine_that_is_not_ours_states_no_size() -> None:
    """A backend that does not know its own size must say nothing rather than
    guess: an enrolled machine belongs to someone else, and an invented limit
    would make the agent skip work the machine could actually do.

    Asked of the backend the pool hands the turn, not of a class — the answer
    has to survive every layer between the machine and the prompt."""
    from app.domain.agent.chat import _sandbox_limits
    from app.domain.agent.compute import build_compute_pool

    pool = build_compute_pool()
    assert _sandbox_limits(pool.select(provider_id="device")) is None


def test_no_machine_line_when_the_size_is_unknown() -> None:
    """And the prompt then carries no size sentence at all — silence, not a
    sentence with a blank in it."""
    lines = _turn_meta_lines(
        is_resume=False,
        disk=None,
        open_cards=None,
        sandbox=None,
    )
    assert not [line for line in lines if "这台机器" in line]
