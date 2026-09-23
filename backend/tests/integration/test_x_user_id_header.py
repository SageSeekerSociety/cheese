"""`X-User-Id` identifies the caller on a local run and nowhere else."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import UserCreator


@pytest.mark.parametrize(
    ("deployed_via_compose", "environment", "status"),
    [
        (False, "development", 200),
        (True, "development", 401),
        (True, "test", 401),
        (False, "production", 401),
    ],
)
def test_x_user_id_is_accepted_only_off_deployments(
    api_client: TestClient,
    user_client: UserCreator,
    monkeypatch,
    deployed_via_compose: bool,
    environment: str,
    status: int,
):
    user = user_client.create_user()
    monkeypatch.setattr(settings, "deployed_via_compose", deployed_via_compose)
    monkeypatch.setattr(settings, "environment", environment)

    resp = api_client.get("/users/me", headers={"X-User-Id": str(user.user_id)})

    assert resp.status_code == status, resp.text
