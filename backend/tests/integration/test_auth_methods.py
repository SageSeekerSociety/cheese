"""The sign-in methods an account has, as the identity confirmation reads
them to decide what to offer."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.user.models import User
from tests.integration.conftest import UserCreator


def _methods(client: TestClient, username: str) -> dict:
    resp = client.get(f"/users/auth/methods/{username}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_an_account_with_a_password_says_so(
    user_client: UserCreator, api_client: TestClient
):
    created = user_client.create_user()

    assert _methods(api_client, created.username)["supports_password"] is True


def test_an_account_without_a_password_says_so(
    db_session, _portal, user_client: UserCreator, api_client: TestClient
):
    created = user_client.create_user()

    async def drop_password() -> None:
        await db_session.execute(
            update(User).where(User.id == created.user_id).values(hashed_password=None)
        )
        await db_session.flush()

    _portal.call(drop_password)

    assert _methods(api_client, created.username)["supports_password"] is False


def test_an_unknown_name_looks_like_an_account_with_only_a_password(
    user_client: UserCreator, api_client: TestClient
):
    created = user_client.create_user()

    unknown = _methods(api_client, f"nobody-{uuid.uuid4().hex[:12]}")

    assert unknown == _methods(api_client, created.username)
