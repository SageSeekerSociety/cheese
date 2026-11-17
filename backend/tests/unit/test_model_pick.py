"""Which model a teammate runs on, by where it works (`model_catalog.pick`).

The rule, as the product states it: a model is a teammate's setting; the
project keeps one main model as the default for teammates that chose none; with
none chosen anywhere (「自动」) the platform picks per scene — conversation, task,
subagent. A teammate may set what it uses in tasks and for subagents apart from
its model.

The plan exception: a model nobody chose (the platform's role) runs whatever
the team's plan lets a person pick; a model somebody chose is held to the plan.
"""

import pytest

from app.core import model_roles
from app.core.errors import ValidationError
from app.domain.agent.model_catalog import Scene, gate_call, pick
from app.domain.policy import gate


def _choice(model: str, tier: str = "included", *, default: bool = False) -> dict:
    return {
        "id": model,
        "label": model.upper(),
        "tier": tier,
        "overseas": False,
        "supply": "gateway",
        "wire": model,
        "default": default,
        "efforts": [],
    }


CHOICES = {
    "kimi": _choice("kimi", "premium", default=True),
    "flash": _choice("flash"),
    "pro": _choice("pro"),
    "glm": _choice("glm"),
    "own-sub": _choice("own-sub"),
    "own-task": _choice("own-task"),
}


@pytest.fixture(autouse=True)
def automatic_roles():
    """「自动」: conversation on kimi, tasks on flash, subagents on pro."""
    model_roles.install(
        {"agent_model": "kimi", "task_model": "flash", "subagent_model": "pro"}
    )
    yield
    model_roles.reset()


ALL_AUTOMATIC = ({}, {})
PROJECT_CHOSE = ({"default_model": "glm"}, {})
TEAMMATE_CHOSE = ({"default_model": "glm"}, {"model": "pro"})
TEAMMATE_SPLIT = (
    {"default_model": "glm"},
    {"model": "pro", "task_model": "own-task", "subagent_model": "own-sub"},
)


@pytest.mark.parametrize(
    ("setup", "scene", "runs_on", "automatic"),
    [
        (ALL_AUTOMATIC, Scene.conversation, "kimi", True),
        (ALL_AUTOMATIC, Scene.task, "flash", True),
        (ALL_AUTOMATIC, Scene.subagent, "pro", True),
        (PROJECT_CHOSE, Scene.conversation, "glm", False),
        (PROJECT_CHOSE, Scene.task, "glm", False),
        (PROJECT_CHOSE, Scene.subagent, "glm", False),
        (TEAMMATE_CHOSE, Scene.conversation, "pro", False),
        (TEAMMATE_CHOSE, Scene.task, "pro", False),
        (TEAMMATE_CHOSE, Scene.subagent, "pro", False),
        (TEAMMATE_SPLIT, Scene.conversation, "pro", False),
        (TEAMMATE_SPLIT, Scene.task, "own-task", False),
        (TEAMMATE_SPLIT, Scene.subagent, "own-sub", False),
    ],
)
def test_each_scene_runs_on_the_first_choice_anyone_made(
    setup, scene, runs_on, automatic
):
    project, teammate = setup
    picked = pick(scene, CHOICES, project_settings=project, teammate=teammate)
    assert (picked.model, picked.automatic) == (runs_on, automatic)


def test_a_teammates_task_choice_alone_leaves_conversation_automatic():
    teammate = {"task_model": "own-task"}
    assert pick(Scene.task, CHOICES, project_settings={}, teammate=teammate).model == (
        "own-task"
    )
    talk = pick(Scene.conversation, CHOICES, project_settings={}, teammate=teammate)
    assert (talk.model, talk.automatic) == ("kimi", True)
    sub = pick(Scene.subagent, CHOICES, project_settings={}, teammate=teammate)
    assert (sub.model, sub.automatic) == ("pro", True)


def test_a_job_without_a_teammate_starts_at_the_project_main_model():
    assert pick(Scene.subagent, CHOICES, project_settings={}).model == "pro"
    assert (
        pick(Scene.subagent, CHOICES, project_settings={"default_model": "glm"}).model
        == "glm"
    )


def test_an_unpointed_task_or_subagent_role_runs_on_the_conversation_role():
    model_roles.install({"agent_model": "kimi"})
    assert pick(Scene.task, CHOICES, project_settings={}).model == "kimi"
    assert pick(Scene.subagent, CHOICES, project_settings={}).model == "kimi"


def test_a_chosen_model_the_catalogue_cannot_serve_is_refused_not_swapped():
    with pytest.raises(ValidationError, match="gone"):
        pick(Scene.task, CHOICES, project_settings={}, teammate={"task_model": "gone"})


FREE_PLAN = gate.policy_of({}, frozenset({"included"}))


def test_the_automatic_model_runs_whatever_the_plan_lets_a_person_pick():
    """Free plan: kimi is premium, out of the plan, yet 「自动」 talks on it."""
    picked = pick(Scene.conversation, CHOICES, project_settings={})
    assert picked.model == "kimi"
    verdict = gate.check(gate_call(picked, CHOICES, approver="owner"), FREE_PLAN, "a")
    assert isinstance(verdict, gate.Allowed)


def test_the_same_model_chosen_by_a_person_is_held_to_the_plan():
    picked = pick(
        Scene.conversation, CHOICES, project_settings={}, teammate={"model": "kimi"}
    )
    with pytest.raises(gate.OverPlan):
        gate.check(gate_call(picked, CHOICES, approver="owner"), FREE_PLAN, "a")
