"""The model a turn runs on belongs to the AGENT before it belongs to the project.

An agent type that names a model is making a claim about how that agent thinks —
"this one runs on Opus" — and it has to hold in every room that agent works in,
or the model box on the agent editor is decoration. Equally: a type that names no
model is *declining* to choose, so the project's pick must still apply under it
rather than being overwritten by a blank.

Both directions are tested because the bug is silent either way: the wrong model
runs, the turn still succeeds, and nothing in the product says which one it used.
"""

import pytest

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_type.services import AgentTypeService

pytestmark = pytest.mark.anyio


async def _type_named(session, *, name: str, model: str | None):
    return await AgentTypeService(session).create(
        name=name,
        title=name,
        description="",
        body="you are a test agent",
        skills=[],
        mcp_servers=[],
        model=model,
        effort=None,
        harness=None,
        space_id=None,
        created_by="tester",
    )


async def test_type_that_names_a_model_reports_it(db_session):
    await _type_named(db_session, name="opus-worker", model="opus")
    await db_session.flush()

    types = AgentTypeService(db_session)
    assert await types.model("opus-worker") == "opus"


async def test_type_that_names_no_model_declines_rather_than_defaults(db_session):
    """None, not "sonnet": the caller falls back to the PROJECT's pick, and a
    type returning a concrete default here would silently outrank it."""
    await _type_named(db_session, name="quiet-worker", model=None)
    await db_session.flush()

    types = AgentTypeService(db_session)
    assert await types.model("quiet-worker") is None


async def test_unknown_type_declines_too(db_session):
    """A type that was deleted while an agent still points at it must not break
    a turn — the project's model carries it."""
    types = AgentTypeService(db_session)
    assert await types.model("no-such-type") is None
    assert await types.model(None) is None


async def test_the_agent_carries_its_types_model(db_session):
    """The lookup the run path actually makes: agent → its type → the model."""
    await _type_named(db_session, name="fable-worker", model="fable")
    await db_session.flush()

    agents = AgentInstanceService(db_session)

    class _Agent:
        type_name = "fable-worker"

    assert await agents.model(_Agent()) == "fable"  # type: ignore[arg-type]
