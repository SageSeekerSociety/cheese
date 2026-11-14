"""What a rule may wait for, and what the reader is told it waits for.

Both sides say which event a rule waits on with the same words — a filing, a
decision and its result, the project's own changes — so the two forms offer
the same list and the two lists read the same. This pins the vocabulary: a
trigger the enum does not name cannot be saved, and a result a decision rule
cannot have is refused rather than stored and never matched.
"""

import pytest

from app.core.errors import ValidationError
from app.domain.routine.models import RoutineTrigger
from app.domain.routine.service._shared import EVENT_TRIGGERS, TRIGGER_LABELS
from app.domain.routine.service.rules import validate_spec


@pytest.mark.parametrize(
    "trigger",
    [
        RoutineTrigger.library_file_added,
        RoutineTrigger.task_closed,
        RoutineTrigger.card_accepted,
        RoutineTrigger.task_filed,
        RoutineTrigger.task_decided,
    ],
)
def test_every_event_trigger_is_an_event_and_has_a_label(trigger):
    """A trigger in ``EVENT_TRIGGERS`` is one the sweep looks for; one without
    a label is a rule whose line cannot say what it waits for."""
    assert trigger in EVENT_TRIGGERS
    assert TRIGGER_LABELS[trigger]


def test_a_decision_rule_may_name_one_result_or_both():
    assert validate_spec("task_decided", {"scope": "room"}, "Asia/Shanghai") == {
        "scope": "room"
    }
    assert validate_spec(
        "task_decided", {"scope": "project", "outcome": "accepted"}, "Asia/Shanghai"
    ) == {"scope": "project", "outcome": "accepted"}
    assert validate_spec(
        "task_decided", {"scope": "room", "outcome": "rejected"}, "Asia/Shanghai"
    ) == {"scope": "room", "outcome": "rejected"}


@pytest.mark.parametrize("outcome", ["maybe", "Accepted", "merged", "both"])
def test_a_result_a_decision_cannot_have_is_refused(outcome):
    with pytest.raises(ValidationError):
        validate_spec(
            "task_decided", {"scope": "room", "outcome": outcome}, "Asia/Shanghai"
        )


def test_a_filing_rule_keeps_its_scope_and_nothing_else():
    """A filing has no result to name: an ``outcome`` on it is dropped, not
    stored and never matched."""
    assert validate_spec(
        "task_filed", {"scope": "project", "outcome": "accepted"}, "Asia/Shanghai"
    ) == {"scope": "project"}
