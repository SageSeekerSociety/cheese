"""进度层 folding rules (no DB): which Task events change the checklist, when a
saved checklist may still be appended to, and what the next turn is told.

The subtle part is ownership of the item ids: Claude numbers its tasks per
session, so a list may only keep growing while that same session is being
resumed. Get this wrong and a fresh session's "task 1" silently ticks a line
from the previous session's list.
"""

from app.domain.agent.chat import _Progress, _progress_lines


def _create(subject: str) -> tuple[str, dict]:
    return "TaskCreate", {"subject": subject}


def test_events_fold_into_a_checklist() -> None:
    p = _Progress(session_id="s1")
    assert p.fold(*_create("修登录"), "e1") is True
    assert p.fold(*_create("加测试"), "e2") is True
    assert p.fold("TaskUpdate", {"taskId": "1", "status": "completed"}, "e3") is True

    assert [(i["id"], i["subject"], i["status"]) for i in p.items] == [
        ("1", "修登录", "completed"),
        ("2", "加测试", "pending"),
    ]


def test_a_replayed_event_id_is_folded_once() -> None:
    """The live hook path and the durable spool can both deliver the same event."""
    p = _Progress(session_id="s1")
    assert p.fold(*_create("只该有一条"), "dup") is True
    assert p.fold(*_create("只该有一条"), "dup") is False
    assert len(p.items) == 1


def test_an_update_for_an_unknown_task_changes_nothing() -> None:
    p = _Progress(session_id="s1")
    assert p.fold("TaskUpdate", {"taskId": "7", "status": "completed"}, "e1") is False
    assert p.items == []


def test_saved_list_is_adopted_only_when_that_session_is_resumed() -> None:
    stored = {
        "session_id": "s1",
        "items": [{"id": "1", "subject": "修登录", "status": "completed"}],
        "eids": ["e1"],
    }
    kept = _Progress.load(stored, resume_session_id="s1")
    assert [i["subject"] for i in kept.items] == ["修登录"]
    assert kept.eids == ["e1"]

    # A different session (or a first turn, with nothing to resume) may not
    # append to ids it did not assign.
    for resume in ("s2", None):
        dropped = _Progress.load(stored, resume_session_id=resume)
        assert dropped.items == []
        assert dropped.eids == []


def test_a_session_nobody_asked_for_restarts_the_numbering() -> None:
    """换机器: the session file is gone, Claude starts over from task 1."""
    p = _Progress(session_id="s1", items=[{"id": "1", "subject": "旧", "status": "x"}])
    p.restart_if_new_session("s1")
    assert len(p.items) == 1  # same session announced back — keep going

    p.restart_if_new_session("s2-fresh")
    assert p.items == []
    assert p.session_id == "s2-fresh"


def test_folded_event_ids_stay_bounded() -> None:
    p = _Progress(session_id="s1")
    for n in range(600):
        p.fold(*_create(f"任务{n}"), f"e{n}")
    assert len(p.eids) == 500
    assert p.eids[-1] == "e599"  # the newest are the ones a replay could repeat


def test_next_turn_is_handed_the_list_with_what_is_done() -> None:
    lines = _progress_lines(
        {
            "items": [
                {"id": "1", "subject": "修登录", "status": "completed"},
                {"id": "2", "subject": "加测试", "status": "in_progress"},
                {"id": "3", "subject": "写文档", "status": "pending"},
            ]
        }
    )
    joined = "\n".join(lines)
    assert "✅ 修登录" in joined
    assert "⏳ 加测试" in joined
    assert "⬜ 写文档" in joined
    assert "别重做" in joined


def test_nothing_saved_adds_no_prompt_lines() -> None:
    assert _progress_lines(None) == []
    assert _progress_lines({}) == []
    assert _progress_lines({"items": []}) == []
