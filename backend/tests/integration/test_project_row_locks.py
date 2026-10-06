"""A project's first overview does not hold up the project's other writes.

A turn makes the project's overview the first time anyone needs it, inside the
transaction that assembles the turn. Whatever it locks on the project row stays
locked until that turn commits. Every block written in the project takes KEY
SHARE on that row (its foreign key), so a full row lock there made those writes
wait — and an archive that held its room's row while writing its note
deadlocked against a turn that went on to update the same room.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import new_project


def test_a_projects_first_overview_does_not_hold_up_its_rooms(client):
    project_id = uuid.UUID(new_project(client)["id"])

    async def scenario() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            # A project from before overviews existed: its first turn makes one.
            async with sessions() as setup:
                await setup.execute(
                    update(Project)
                    .where(Project.id == project_id)
                    .values(overview_document_id=None)
                )
                await setup.commit()

            async with sessions() as turn, sessions() as room:
                project = await turn.get(Project, project_id)
                assert project is not None and project.root_topic_id is not None
                await ProjectService(turn).overview_document(project)
                # The turn has not committed. The room writes a note meanwhile.
                await room.execute(text("SET LOCAL lock_timeout = '2s'"))
                try:
                    await BlockRepository(room).add(
                        project_id=project_id,
                        conversation_id=project.root_topic_id,
                        author="alice",
                        author_type=AuthorType.platform,
                        content="note",
                        kind=BlockKind.event,
                        meta={"platform": True},
                    )
                    await room.commit()
                except DBAPIError as exc:
                    pytest.fail(f"the room's write waited on the overview: {exc}")
                await turn.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
