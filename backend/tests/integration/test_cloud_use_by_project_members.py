"""Who may run a room on the team's cloud: anyone on the project, nobody else."""

import asyncio

import pytest

from app.core.errors import ForbiddenError
from app.domain.identity.actor import Actor
from app.domain.machine.services import MachineService
from tests.conftest import seed_user
from tests.integration.conftest import add_external_member, post_project


def _may_use(client, project_id: str, handle: str) -> bool:
    user_id = asyncio.run(_user_id(client, handle))

    async def _check() -> bool:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            try:
                await MachineService(session).require_use_authority(
                    project_id, Actor(handle=handle, user_id=user_id, via="token")
                )
            except ForbiddenError:
                return False
            return True

    return asyncio.run(_check())


async def _user_id(client, handle: str) -> int:
    from app.domain.user.repositories import UserRepository

    async with client.test_factory() as session:  # type: ignore[attr-defined]
        return (await UserRepository(session).get_by_username(handle)).id


@pytest.fixture
def project(client) -> str:
    return post_project(client, json={"name": "P", "owner_handle": "owner"}).json()[
        "data"
    ]["id"]


def test_the_owner_may_use_cloud(client, project):
    assert _may_use(client, project, "owner")


def test_an_external_member_may_use_cloud(client, project):
    add_external_member(client, project, "guest")
    assert _may_use(client, project, "guest")


def test_someone_not_on_the_project_may_not(client, project):
    seed_user(client, "stranger")
    assert not _may_use(client, project, "stranger")
