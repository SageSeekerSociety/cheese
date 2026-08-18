"""What the agent-type editor may offer, and what it must refuse to offer.

The catalog exists so a picker never promises more than the run path delivers.
Half these fields are stored and read by nobody, and the failure that matters is
silent: an editor renders an input, somebody sets a value, the agent runs exactly
as before, and the product says nothing. So the test worth having is not "the
shape is a dict" — it is "a field nothing consumes is NOT offered as a choice."
"""

from app.domain.agent.market import subscription_model_default, subscription_model_ids
from app.domain.agent_type.options import agent_type_options


def test_model_offers_exactly_the_models_a_turn_can_run_on():
    model = agent_type_options()["model"]
    assert model["state"] == "choosable"
    assert {c["id"] for c in model["choices"]} == subscription_model_ids()
    # The picker's default has to be the one a turn actually falls back to,
    # or the editor pre-selects a model the project would not have used.
    defaults = [c["id"] for c in model["choices"] if c["default"]]
    assert defaults == [subscription_model_default()]


def test_fields_with_no_consumer_are_not_offered_as_choices():
    options = agent_type_options()
    for name in ("effort", "harness", "skills", "mcp_servers"):
        assert options[name]["state"] == "unavailable", name
        # No choices, or the UI would render a picker anyway.
        assert options[name]["choices"] == [], name


def test_every_unavailable_field_names_why():
    """A boolean 'hidden' teaches nobody why. The reason is what whoever wires
    the field up later greps for — it is not shown to anyone, and the editor
    renders nothing at all for these fields."""
    for name, opts in agent_type_options().items():
        if opts["state"] == "unavailable":
            assert opts["reason"], name
            assert opts["note"], name
        else:
            assert not opts["reason"], name


def test_an_agent_can_be_created_without_writing_a_personality():
    """空的角色设定是一个真答案，不是没填完的表单。

    让一个 agent 成为「那个 agent」的是它攒下的记忆 —— 那份记忆每轮都注入，
    和这段文字写没写无关。要求先写一段人格，等于要求人在这个 agent 还什么都
    没做过的时候先编一个；而它自己之后可以改这一段。
    """
    from app.domain.agent_type.schemas import AgentTypeCreate, AgentTypeUpdate

    assert AgentTypeCreate(name="fresh").body == ""
    # 改成空 = 把它清掉，同样是一个真动作，不该被 422 挡回去。
    assert AgentTypeUpdate(body="").body == ""
