"""进度层 → prompt (#187): how a stored checklist is handed to the next turn.

The turn-meta block is the only place the platform can state facts the agent has
no other way to see. "做到哪了" is one of those: on a new machine the repo, the
doc and the decision log all survive, but the half-finished checklist does not.
"""

from app.domain.agent.chat import _progress_lines, _turn_meta_lines

ITEMS = [
    {"id": "1", "subject": "核实 issue 论断", "status": "completed"},
    {"id": "2", "subject": "写实现", "status": "in_progress"},
    {"id": "3", "subject": "补测试", "status": "pending"},
]


def _meta(**kw) -> list[str]:
    base = {
        "budget_s": 600,
        "activity_aware": False,
        "is_resume": False,
        "disk": None,
        "open_cards": None,
    }
    return _turn_meta_lines(**{**base, **kw})


def test_no_checklist_produces_no_lines():
    assert _progress_lines([]) == []


def test_each_status_gets_its_own_mark():
    text = "\n".join(_progress_lines(ITEMS))
    assert "- [x] 核实 issue 论断" in text
    assert "- [~] 写实现" in text
    assert "- [ ] 补测试" in text


def test_unknown_status_degrades_to_unfinished():
    """A status the platform does not know must never read as done — claiming
    finished work that isn't is the one wrong answer here."""
    lines = _progress_lines([{"id": "1", "subject": "x", "status": "wat"}])
    assert "- [ ] x" in "\n".join(lines)


def test_blank_subject_still_renders_a_row():
    assert "（任务）" in "\n".join(
        _progress_lines([{"id": "1", "subject": "  ", "status": "pending"}])
    )


def test_agent_is_told_to_carry_finished_items_into_a_new_checklist():
    # Load-bearing: the stored row is overwritten by the next turn's first
    # TaskCreate, so a re-plan that lists only what's left erases the rest.
    text = "\n".join(_progress_lines(ITEMS))
    assert "completed" in text
    assert "只列剩下的" in text


def test_turn_meta_carries_the_checklist():
    assert any("核实 issue 论断" in line for line in _meta(progress=ITEMS))


def test_turn_meta_without_progress_is_unchanged():
    assert _meta(progress=[]) == _meta()


def test_checklist_is_offered_on_every_turn_not_just_resumes():
    """A topic picked up days later on a different machine has the same problem
    as an auto-resume, and gets no `is_resume` flag to say so."""
    assert any("上次的任务清单" in line for line in _meta(progress=ITEMS))
