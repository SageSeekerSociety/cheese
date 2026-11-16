"""Every kind of session the platform runs has a prompt it can be started with,
and none is told to use a tool it does not hold.

A rule that names a tool a session lacks sends it to do the one thing it
cannot: the call fails, or the session gives up on what the rule was for. The
profiles refuse that when they render, so these hold it for every kind and
every switch.
"""

import itertools
from dataclasses import replace

import pytest

from app.domain.agent.skills import load_skill
from app.domain.prompts import (
    PROFILES,
    Facts,
    Kind,
    ProfileError,
    check,
    render_notice,
    render_system,
)
from app.domain.prompts import profiles as profiles_module

#: What the document session is given by its caller (`document/question.py`).
VALUES = {
    Kind.document: {"agent": "芝士", "place": "这里", "answer": "回答", "machine": "无"}
}

SWITCHES = list(itertools.product((True, False), (False, True), (False, True)))


@pytest.mark.parametrize("kind", list(Kind))
@pytest.mark.parametrize(("working_directory", "has_doc", "memory_synced"), SWITCHES)
def test_every_kind_renders_with_every_switch(
    kind, working_directory, has_doc, memory_synced
):
    facts = Facts(
        working_directory=working_directory,
        has_doc=has_doc,
        memory_synced=memory_synced,
        name="芝士",
    )
    system = render_system(
        kind, facts, skill=load_skill, base="底稿", values=VALUES.get(kind)
    )
    assert system.strip()
    assert "⟦" not in system
    check(kind)
    for language in (None, "en"):
        assert "⟦" not in render_notice(kind, "话", language)


def test_a_piece_naming_a_tool_the_kind_lacks_is_refused(monkeypatch):
    """The task profile without `cheese_doc`: its rules name it (the document
    section), so rendering a task session with a document is refused rather
    than handing it a rule it cannot follow."""
    task = PROFILES[Kind.task]
    lacking = replace(task, tools=frozenset(t for t in task.tools if t != "cheese_doc"))
    monkeypatch.setitem(profiles_module.PROFILES, Kind.task, lacking)

    with pytest.raises(ProfileError) as refused:
        render_system(Kind.task, Facts(has_doc=True), skill=load_skill)
    assert refused.value.tool.startswith("cheese_doc")
    with pytest.raises(ProfileError):
        check(Kind.task)
