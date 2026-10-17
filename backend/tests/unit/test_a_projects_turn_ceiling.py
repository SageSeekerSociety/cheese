"""How many of a project's turns run at once, and where that number comes from.

The deployment's number is what every project is admitted under until it sets
one of its own; its own is what `PUT /projects/{id}/compute-configs` writes
(`app/api/routes/projects_run_config.py`), read back by `GET` of the same path
and enforced by admission. So these drive `work_policy` — the reader a turn is
actually gated on — rather than the two helpers on their own.
"""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.turn_ceiling import (
    TURN_CEILING_MAX,
    max_concurrent_turns_for,
    project_max_concurrent_turns,
)
from app.domain.agent.compute import build_compute_pool
from app.domain.agent.work_policy import work_policy
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from tests.turn_log import a_topic

pytestmark = pytest.mark.anyio


async def _project_settings(db_factory, topic_id) -> dict:
    """What one project's settings hold, as the write path stored them."""
    async with db_factory() as session:
        project = await _project(session, topic_id)
        return dict(project.settings or {})


async def _project(session, topic_id) -> Project:
    topic = await session.get(Topic, topic_id)
    assert topic is not None
    project = await session.scalar(
        select(Project).where(Project.id == topic.project_id)
    )
    assert project is not None
    return project


async def _store(db_factory, topic_id, key: str, value: object) -> None:
    """One project setting, stored the way the route stores it (`None` clears)."""
    async with db_factory() as session:
        project = await _project(session, topic_id)
        stored = dict(project.settings or {})
        if value is None:
            stored.pop(key, None)
        else:
            stored[key] = value
        project.settings = stored
        await session.commit()


async def _admitted(db_factory, topic_id) -> int:
    """The ceiling a turn of that project is admitted under."""
    policy = await work_policy(db_factory, build_compute_pool(), topic_id)
    assert policy is not None
    return policy["max_concurrent_turns"]


async def test_a_project_runs_at_the_deployments_number_until_it_sets_its_own(
    db_factory,
):
    """20: at 16, a project running sixteen or more task lines at once queued
    work of its own behind the ceiling."""
    assert settings.max_concurrent_turns == 20
    topic_id = await a_topic(db_factory)

    assert await _admitted(db_factory, topic_id) == 20
    assert max_concurrent_turns_for(await _project_settings(db_factory, topic_id)) == 20
    assert project_max_concurrent_turns({}) is None


async def test_a_project_that_sets_its_own_is_admitted_under_it(db_factory):
    topic_id = await a_topic(db_factory)
    await _store(db_factory, topic_id, "max_concurrent_turns", 12)

    assert await _admitted(db_factory, topic_id) == 12
    assert max_concurrent_turns_for({"max_concurrent_turns": 12}) == 12
    # Both ends of the range a person may set are read back as set.
    assert project_max_concurrent_turns({"max_concurrent_turns": 1}) == 1
    assert (
        project_max_concurrent_turns({"max_concurrent_turns": TURN_CEILING_MAX})
        == TURN_CEILING_MAX
    )


async def test_clearing_it_puts_the_project_back_on_the_deployments_number(db_factory):
    topic_id = await a_topic(db_factory)
    await _store(db_factory, topic_id, "max_concurrent_turns", 12)
    await _store(db_factory, topic_id, "max_concurrent_turns", None)

    assert await _admitted(db_factory, topic_id) == settings.max_concurrent_turns
    assert project_max_concurrent_turns({"max_concurrent_turns": None}) is None


@pytest.mark.parametrize("stored", [0, -4, True, "32", 1.5, TURN_CEILING_MAX + 1, 640])
async def test_a_stored_value_that_is_no_ceiling_is_the_deployments_number(
    db_factory, stored
):
    """What the reader has always done with junk in that key: it is no ceiling
    at all, so the project runs at the deployment's number rather than at a
    number nobody set, at zero — which no turn would ever pass — or past the
    range a person could have asked for."""
    topic_id = await a_topic(db_factory)
    await _store(db_factory, topic_id, "max_concurrent_turns", stored)

    assert await _admitted(db_factory, topic_id) == settings.max_concurrent_turns
