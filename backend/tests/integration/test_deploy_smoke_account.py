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
- an account the deploy made and that was closed is replaced, not a traceback.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.common.auth import verify_access_token
from app.domain.dashboard.user_statistics import UserStatisticsRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository
from app.domain.user.services import UserAuthService
from app.domain.user.sessions import SessionService

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "release-smoke.py"


@pytest.fixture
def smoke(db_factory, monkeypatch):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", db_factory)
    spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
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
