"""The deploy's smoke account is its own, and is never anyone else's.

Before a release takes traffic, deploy/release-smoke.py signs in as an account
the deploy provisions on every release (`provision`, run in the release's image
against the deployment's database). These pin what that provisioning may do:

- it makes the account the first time, and that account signs in with the
  password from the box's secret, at an address the platform never mails;
- when the secret's password changed, the account follows it;
- an account by that handle that the deploy did not make is refused and left
  exactly as it was: never signed into, never given the deploy's password;
- a handle a closed account still holds is refused with a message that says
  what to do, not a traceback.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.email import is_placeholder_email
from app.domain.dashboard.user_statistics import UserStatisticsRepository
from app.domain.user.passwords import hash_password
from app.domain.user.repositories import UserProfileRepository, UserRepository
from app.domain.user.services import UserAuthService

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "release-smoke.py"
HANDLE = "a3f09c1d7be24e5f8a61"


@pytest.fixture
def smoke(db_factory, monkeypatch):
    monkeypatch.setenv("DEPLOY_SMOKE_USER", HANDLE)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", db_factory)
    spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("release_smoke", None)


async def signs_in(db_factory, handle: str, password: str) -> bool:
    async with db_factory() as db:
        auth = UserAuthService(
            user_repo=UserRepository(session=db),
            profile_repo=UserProfileRepository(session=db),
            stats_repo=UserStatisticsRepository(session=db),
        )
        return await auth.authenticate(username=handle, password=password) is not None


async def test_makes_the_account_and_follows_the_secret(smoke, db_factory, capsys):
    await smoke.provision("first-password-0123456789")
    assert await signs_in(db_factory, HANDLE, "first-password-0123456789")
    async with db_factory() as db:
        user = await UserRepository(session=db).get_by_username(HANDLE)
    assert is_placeholder_email(user.email)

    await smoke.provision("first-password-0123456789")
    await smoke.provision("rotated-password-0123456789")
    assert await signs_in(db_factory, HANDLE, "rotated-password-0123456789")
    assert not await signs_in(db_factory, HANDLE, "first-password-0123456789")
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"smoke provision ok account {HANDLE} created",
        f"smoke provision ok account {HANDLE} present",
        f"smoke provision ok account {HANDLE} password set from the secret file",
    ]


async def test_an_account_it_did_not_make_is_refused_and_left_alone(smoke, db_factory):
    async with db_factory() as db:
        await UserAuthService(
            user_repo=UserRepository(session=db),
            profile_repo=UserProfileRepository(session=db),
            stats_repo=UserStatisticsRepository(session=db),
        ).register_with_password(
            username=HANDLE,
            nickname="someone",
            email="someone@example.com",
            password="their-own-password-1",
        )
        await db.commit()

    with pytest.raises(smoke.Failed) as refused:
        await smoke.provision("deploy-password-0123456789")

    assert refused.value.check == "provision"
    assert "deploy-smoke.secret" in refused.value.detail
    assert await signs_in(db_factory, HANDLE, "their-own-password-1")
    assert not await signs_in(db_factory, HANDLE, "deploy-password-0123456789")


async def test_a_handle_a_closed_account_holds_is_refused_plainly(smoke, db_factory):
    async with db_factory() as db:
        user = await UserRepository(session=db).create_user(
            username=HANDLE,
            email="closed@example.com",
            hashed_password=await hash_password("closed-password-1"),
        )
        user.deleted_at = datetime.now(UTC)
        await db.commit()

    with pytest.raises(smoke.Failed) as refused:
        await smoke.provision("deploy-password-0123456789")

    assert refused.value.check == "provision"
    assert "deploy-smoke.secret" in refused.value.detail
