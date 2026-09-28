"""Every group read route must require a signed-in caller.

These four read routes used to be mounted without an auth dependency, so an
anonymous request reached the handler and got real data back. They now declare
``require_auth_user`` and must answer 401 without an ``Authorization`` header,
while staying 200 for a caller that carries a valid token.
"""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.avatars.models import Avatar
from tests.integration.conftest import UserCreator, unique_int


def _create_avatar(db_session: AsyncSession, portal: BlockingPortal) -> int:
    avatar = Avatar(
        url=f"/test/avatar_{unique_int(1000, 9999)}.jpg",
        name="test_avatar",
        created_at=datetime.now(UTC),
        avatar_type="upload",
        usage_count=0,
    )

    async def _do() -> int:
        db_session.add(avatar)
        await db_session.flush()
        return avatar.id

    return portal.call(_do)


class TestGroupReadRoutesRequireAuth:
    @pytest.fixture
    def setup_group(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ) -> dict:
        user = user_client.create_user()
        token = user_client.login(api_client, user.username, user.password)
        headers = {"Authorization": f"Bearer {token}"}

        avatar_id = _create_avatar(db_session, _portal)
        group_resp = api_client.post(
            "/groups",
            json={
                "name": f"Read Auth Group {unique_int(100000, 999999)}",
                "intro": "Group for read-route auth tests",
                "avatarId": avatar_id,
            },
            headers=headers,
        )
        assert group_resp.status_code == 201, (
            f"Group creation failed: {group_resp.status_code}: {group_resp.text}"
        )
        group_id = group_resp.json()["data"]["group"]["id"]

        now = int(datetime.now(UTC).timestamp() * 1000)
        target_resp = api_client.post(
            f"/groups/{group_id}/targets",
            json={
                "name": "Read Auth Target",
                "intro": "Target for read-route auth tests",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
            headers=headers,
        )
        assert target_resp.status_code == 201, (
            f"Target creation failed: {target_resp.status_code}: {target_resp.text}"
        )
        target_id = target_resp.json()["data"]["id"]

        return {
            "headers": headers,
            "paths": [
                f"/groups/{group_id}/members",
                f"/groups/{group_id}/targets",
                f"/groups/{group_id}/targets/{target_id}",
                f"/groups/{group_id}/questions",
            ],
        }

    def test_anonymous_is_401(self, setup_group: dict, api_client: TestClient):
        for path in setup_group["paths"]:
            resp = api_client.get(path)
            assert resp.status_code == 401, (
                f"{path} should be 401 anonymous, got {resp.status_code}: {resp.text}"
            )

    def test_authenticated_is_200(self, setup_group: dict, api_client: TestClient):
        for path in setup_group["paths"]:
            resp = api_client.get(path, headers=setup_group["headers"])
            assert resp.status_code == 200, (
                f"{path} should be 200 for a signed-in caller, "
                f"got {resp.status_code}: {resp.text}"
            )
