"""A team's live feed: who may listen, and what a machine change sends.

The compute page reads a team's machines when this feed names a project, and
polls nothing, so a change that sends no signal is a page that shows a stale
machine until its resync. The refusal codes are the room socket's: the client's
refusal latch only stops retrying for codes it recognises.
"""

import uuid

from app.domain.machine.live import announce_changes
from app.domain.machine.models import MachineStatus
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.project.repositories import ProjectRepository
from tests.integration.conftest import a_team, registered, session_token


def _team_with_project(client, owner: str) -> tuple[int, str]:
    async def seed():
        async with client.test_request_factory() as session:
            team_id = await a_team(session, owner_handle=owner)
            project = await ProjectRepository(session).add(name="Live", team_id=team_id)
            await session.commit()
            return team_id, str(project.id)

    return client.portal.call(seed)


def _refusal(client, url: str) -> str:
    with client.websocket_connect(url) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    return frame["code"]


def test_a_socket_without_a_token_is_refused(client):
    team_id, _ = _team_with_project(client, "alice")
    assert _refusal(client, f"/teams/{team_id}/live") == "auth_required"


def test_an_expired_token_is_refused_as_expired(client):
    team_id, _ = _team_with_project(client, "alice")
    stale = session_token("alice", ttl_s=-60)
    assert _refusal(client, f"/teams/{team_id}/live?token={stale}") == "auth_expired"


def test_someone_outside_the_team_is_refused_as_forbidden(client):
    team_id, _ = _team_with_project(client, "alice")

    async def outsider():
        async with client.test_request_factory() as session:
            await registered(session, "mallory")
            await session.commit()

    client.portal.call(outsider)
    token = session_token("mallory")
    assert _refusal(client, f"/teams/{team_id}/live?token={token}") == "forbidden"


def test_a_member_hears_which_project_had_a_machine_change(client):
    team_id, project_id = _team_with_project(client, "alice")
    token = session_token("alice")

    with client.websocket_connect(f"/teams/{team_id}/live?token={token}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        async def change():
            async with client.test_request_factory() as session:
                await ProjectMachineRepository(session).add(
                    project_id=uuid.UUID(project_id),
                    machine_id=91,
                    customer_id=7,
                    account_id=9,
                    offering_id=1,
                    hostname="live-91",
                    login_user="cheese",
                    cores=2,
                    memory_mb=4096,
                    disk_gb=20,
                    status=MachineStatus.provisioning,
                    ip=None,
                    requested_by="alice",
                )
                await session.commit()
                await announce_changes(session)

        client.portal.call(change)
        assert ws.receive_json() == {
            "type": "state",
            "resource": "machines",
            "project_ids": [project_id],
        }
