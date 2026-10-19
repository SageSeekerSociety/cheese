"""Which repository calls its main gate what, read off the project row.

The rule, stated before the code: a project gets a batched main gate only if
its repository has one. The repository that hosts the platform gets the
platform's own names without configuring anything; every other repository gets
none, so no probe is ever dispatched there.
"""

from app.core.config import settings as platform
from app.domain.project.models import Project
from app.domain.review.main_gate import PLATFORM_GATE_NAMES, MainGate, main_gate_for
from tests.integration.conftest import a_team

OTHER = "someone/another-repository"
THEIR_GATE = {
    "check": "their gate",
    "green_ref": "their-green",
    "probe_workflow": "their-probe.yml",
}


async def _project(db_factory, settings: dict | None = None):
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Main gate")
        project.settings = settings or {}
        session.add(project)
        await session.commit()
        return project.id


async def test_a_project_that_configures_its_gate_is_read_that_way(db_factory):
    project_id = await _project(db_factory, {"main_gate": THEIR_GATE})
    async with db_factory() as session:
        gate = await main_gate_for(project_id, session, repo=OTHER)
    assert gate == MainGate(**THEIR_GATE)


async def test_the_platforms_repository_gets_the_platform_names(db_factory):
    project_id = await _project(db_factory)
    async with db_factory() as session:
        gate = await main_gate_for(
            project_id, session, repo=platform.platform_repository
        )
    assert gate == PLATFORM_GATE_NAMES


async def test_another_repository_gets_no_gate(db_factory):
    """Nothing to batch on: no gate name, no green mark, no probe workflow."""
    project_id = await _project(db_factory)
    async with db_factory() as session:
        gate = await main_gate_for(project_id, session, repo=OTHER)
    assert gate == MainGate()
