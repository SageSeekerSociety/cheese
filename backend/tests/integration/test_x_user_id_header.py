"""An `X-User-Id` header names no one, even on a local run: a request is who
its credential says, and a bare user id is not a credential."""

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import UserCreator


def test_x_user_id_identifies_no_one(
    api_client: TestClient, user_client: UserCreator, monkeypatch
):
    user = user_client.create_user()
    monkeypatch.setattr(settings, "deployed_via_compose", False)
    monkeypatch.setattr(settings, "environment", "development")

    resp = api_client.get("/users/me", headers={"X-User-Id": str(user.user_id)})

    assert resp.status_code == 401, resp.text
