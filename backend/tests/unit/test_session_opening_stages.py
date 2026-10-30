"""A project from a staged task opens with the task's stages and where the
project stands on each; a project from an unstaged task, or none, hears
nothing about stages. When a stage's status moves, a running conversation is
told again."""

from datetime import UTC, datetime

from app.domain.agent.harness.opening import opening_changes
from app.domain.agent.harness.prompt import build_session_opening
from app.domain.task.claims import StageProgress


def _stages(second: str) -> list[StageProgress]:
    return [
        StageProgress(
            index=0,
            name="需求分析",
            description="交一份需求文档",
            due_at=datetime(2026, 11, 1, 15, 59, tzinfo=UTC),
            status="PASSED",
        ),
        StageProgress(index=1, name="实现", description="", due_at=None, status=second),
    ]


def test_each_stage_is_named_with_its_due_date_and_status() -> None:
    text = build_session_opening(stages=_stages("NOT_SUBMITTED")).text

    assert "1. 需求分析（截止 2026-11-01 15:59 UTC）：已通过" in text
    assert "交一份需求文档" in text
    assert "2. 实现（不设截止）：未提交" in text


def test_a_task_without_stages_adds_nothing() -> None:
    assert "阶段" not in build_session_opening(stages=[]).text


def test_a_running_conversation_hears_when_a_stage_moves() -> None:
    before = build_session_opening(stages=_stages("NOT_SUBMITTED"))
    after = build_session_opening(stages=_stages("PENDING_REVIEW"))

    assert opening_changes(before, before.digests()) == ""
    assert "2. 实现（不设截止）：待批改" in opening_changes(after, before.digests())
