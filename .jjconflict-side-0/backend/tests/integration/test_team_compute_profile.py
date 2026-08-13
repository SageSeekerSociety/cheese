"""Team compute ownership and default selection (execution architecture v4)."""

from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from tests.integration.conftest import UserCreator, unique_int


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _team(client: TestClient, owner) -> int:
    response = client.post(
        "/teams",
        json={
            "name": f"Compute Team {unique_int(100000, 999999)}",
            "intro": "",
            "description": "",
            "avatarId": 1,
        },
        headers=_headers(owner.token),
    )
    assert response.status_code == 201
    return int(response.json()["data"]["team"]["id"])


def test_team_default_is_member_visible_and_admin_managed(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
) -> None:
    owner = user_client.create_user()
    owner.token = user_client.login(api_client, owner.username, owner.password)
    member = user_client.create_user()
    member.token = user_client.login(api_client, member.username, member.password)
    admin = user_client.create_user()
    admin.token = user_client.login(api_client, admin.username, admin.password)
    outsider = user_client.create_user()
    outsider.token = user_client.login(api_client, outsider.username, outsider.password)
    team_id = _team(api_client, owner)

    async def _add_member() -> None:
        repo = TeamRepository(db_session)
        await repo.add_member(team_id, member.user_id, TeamMemberRole.MEMBER)
        await repo.add_member(team_id, admin.user_id, TeamMemberRole.ADMIN)

    _portal.call(_add_member)

    initial = api_client.get(
        f"/teams/{team_id}/compute-profile", headers=_headers(member.token)
    )
    assert initial.status_code == 200
    assert initial.json()["data"]["current"] == "local-docker"

    denied = api_client.put(
        f"/teams/{team_id}/compute-profile",
        json={"profile": "local-docker"},
        headers=_headers(member.token),
    )
    assert denied.status_code == 403

    saved = api_client.put(
        f"/teams/{team_id}/compute-profile",
        json={"profile": "local-docker"},
        headers=_headers(owner.token),
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["current"] == "local-docker"

    admin_saved = api_client.put(
        f"/teams/{team_id}/compute-profile",
        json={"profile": "local-docker"},
        headers=_headers(admin.token),
    )
    assert admin_saved.status_code == 200

    unavailable = api_client.put(
        f"/teams/{team_id}/compute-profile",
        json={"profile": "device"},
        headers=_headers(owner.token),
    )
    assert unavailable.status_code == 422

    hidden = api_client.get(
        f"/teams/{team_id}/compute-profile", headers=_headers(outsider.token)
    )
    assert hidden.status_code == 404
