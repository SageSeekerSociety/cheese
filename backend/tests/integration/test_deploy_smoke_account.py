"""The deploy's smoke account is its own, cannot be signed into, and never
touches anyone else's account.

Before a release takes traffic, deploy/release-smoke.py uses it on the new
slots (`provision`, run in the release's image against the deployment's
database, opens its session). These pin what provisioning may do:

- it makes the account the first time and finds the same one afterwards, and
  the session it opens is that account's;
- no password signs into it, so nobody can hold releases back by sending it
  wrong ones;
- a person's account is never the one it uses, even one holding the handle
  `deploy-smoke` with a password of its own, and that account keeps working;
- an account the deploy made and that was closed is replaced, not a traceback;
- nobody can hold its task page back by creating a project first: its project
  is the one it owns by name, with an id the server picks.
"""

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.common.auth import verify_access_token
from app.domain.dashboard.user_statistics import UserStatisticsRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository
from app.domain.user.services import UserAuthService
from app.domain.user.sessions import SessionService

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "release-smoke.py"


def load_script():
    spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke(db_factory, monkeypatch):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", db_factory)
    yield load_script()
    sys.modules.pop("release_smoke", None)


def auth_service(db) -> UserAuthService:
    return UserAuthService(
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
        stats_repo=UserStatisticsRepository(session=db),
    )


async def signs_in(db_factory, handle: str, password: str) -> bool:
    async with db_factory() as db:
        return (
            await auth_service(db).authenticate(username=handle, password=password)
            is not None
        )


async def test_the_session_is_the_smoke_accounts_and_no_password_opens_it(
    smoke, db_factory
):
    user_id, token, refresh = await smoke.provision()
    again, _, _ = await smoke.provision()
    assert again == user_id

    claims = verify_access_token(token)
    assert claims.user_id == user_id
    async with db_factory() as db:
        user = await UserRepository(session=db).get_by_id(user_id)
        assert await SessionService(db).started_at(user_id, claims.sid) is not None
    for password in ("", "password", user.username, "deploy-smoke"):
        assert not await signs_in(db_factory, user.username, password)


async def test_a_persons_account_is_never_used_or_changed(smoke, db_factory):
    async with db_factory() as db:
        person, _ = await auth_service(db).register_with_password(
            username="deploy-smoke",
            nickname="someone",
            email="someone@example.com",
            password="their-own-password-1",
        )
        await db.commit()

    user_id, token, _ = await smoke.provision()

    assert user_id != person.id
    assert verify_access_token(token).user_id != person.id
    assert await signs_in(db_factory, "deploy-smoke", "their-own-password-1")


async def test_a_closed_smoke_account_is_replaced(smoke, db_factory):
    first, _, _ = await smoke.provision()
    async with db_factory() as db:
        user = await UserRepository(session=db).get_by_id(first)
        user.deleted_at = datetime.now(UTC)
        await db.commit()

    second, _, _ = await smoke.provision()

    assert second != first


def test_a_project_someone_else_made_first_does_not_block_the_task(client):
    from tests.conftest import seed_user

    smoke = load_script()
    token = seed_user(client, "smoke-owner")
    auth = {"Authorization": f"Bearer {token}"}
    user_id = verify_access_token(token).user_id
    # The id an earlier version derived from the account's public user id,
    # and the same name, taken first by another person.
    derived = uuid.uuid5(
        uuid.NAMESPACE_URL, f"https://okcheese.com/deploy-smoke/{user_id}"
    )
    taken = client.post(
        "/projects",
        json={"id": str(derived), "name": smoke.PROJECT_NAME},
        headers={"Authorization": f"Bearer {seed_user(client, 'someone-else')}"},
    )
    assert taken.status_code == 200, taken.text

    task_id = smoke.own_task(client, "", auth, "smoke-owner")
    assert smoke.own_task(client, "", auth, "smoke-owner") == task_id

    task = client.get(f"/topics/{task_id}/task", headers=auth)
    assert task.status_code == 200, task.text
    assert task.json()["data"]["project_id"] != str(derived)
