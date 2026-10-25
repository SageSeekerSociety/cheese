"""谁带哪些技能进提示词：队友自己的 userskill 优先，没带就回落到房间点亮的。

`carried_skills` 是这一层唯一的判据（`_TurnContext.skills` 是它的结果），
所以判据钉在这里，而不是钉在拼提示词的那段代码上。
"""

import uuid

from app.domain.agent.turn.intake.preparation import carried_skills
from app.domain.agent_instance.services import ResolvedAgent
from app.domain.topic.models import Topic


def _room(skills: list | None = None) -> Topic:
    return Topic(id=uuid.uuid4(), project_id=uuid.uuid4(), title="房", skills=skills)


def _agent(skills: list | None = None) -> ResolvedAgent:
    return ResolvedAgent(
        instance_id=uuid.uuid4(),
        handle="cheese",
        type_name=None,
        display_name="芝士",
        configuration={} if skills is None else {"skills": skills},
    )


def test_a_teammate_carrying_its_own_skills_does_not_inherit_the_rooms():
    assert carried_skills(_agent(["wolfram"]), _room(["documents"])) == ["wolfram"]


def test_a_teammate_carrying_nothing_falls_back_to_the_room():
    assert carried_skills(_agent(), _room(["documents"])) == ["documents"]


def test_a_userskill_of_nothing_usable_falls_back_to_the_room_too():
    assert carried_skills(_agent([3]), _room(["documents"])) == ["documents"]


def test_nothing_anywhere_is_an_empty_list():
    # 这条空名单就是提示词逐字节不变的那一条：`native_skill_bodies([])` 是空串。
    assert carried_skills(_agent(), _room()) == []
    assert carried_skills(_agent([]), _room(None)) == []


def test_names_that_are_not_strings_are_dropped_rather_than_passed_on():
    assert carried_skills(_agent(["wolfram", 3]), _room()) == ["wolfram"]


def test_the_tasks_skills_are_added_on_top_of_what_the_teammate_carried():
    """The 题目's skills come on top: a teammate falling back to the room keeps
    the room's, one carrying its own keeps its own."""
    assert carried_skills(_agent(), _room(["documents"]), ["wolfram"]) == [
        "documents",
        "wolfram",
    ]
    assert carried_skills(_agent(["showcase"]), _room(["documents"]), ["wolfram"]) == [
        "showcase",
        "wolfram",
    ]
