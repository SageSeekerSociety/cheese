"""归档项目：所有者把一个项目收起来——谁都看不到它、谁都改不了它、它什么都不再跑，
而它的数据一样不少；取消归档时，它和跟它一起归档的房间原样回来。"""

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.memory.models import MemoryDreamRun
from app.domain.project.services import ProjectService
from app.domain.usage.repositories import UsageRepository
from tests.conftest import StubChannel
from tests.integration.conftest import (
    _register,
    join_project_team,
    post_project,
    registered,
    session_auth_headers,
)

OWNER = "archive-owner"
AS_OWNER = session_auth_headers(OWNER)


def _project(client) -> dict:
    r = post_project(client, json={"name": "要收起来的项目"}, owner=OWNER)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _room(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=AS_OWNER,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _listed(client, headers, **params) -> set[str]:
    r = client.get("/projects", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return {p["id"] for p in r.json()["data"]["data"]}


def _status(client, topic_id: str) -> str:
    r = client.get(f"/topics/{topic_id}", headers=AS_OWNER)
    assert r.status_code == 200, r.text
    return r.json()["data"]["status"]


def _refused_as_archived(response) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["error"]["name"] == "ProjectArchivedError"


def test_only_the_owner_archives_or_unarchives(client):
    project = _project(client)
    pid = project["id"]
    join_project_team(client, pid, "archive-mate")
    join_project_team(client, pid, "archive-admin", admin=True)
    _register(client, "archive-stranger")

    for path in ("archive", "unarchive"):
        # Without a person's credential: nothing done, and nothing learned.
        assert client.post(f"/projects/{pid}/{path}").status_code in {401, 404}
        for handle in ("archive-mate", "archive-admin"):
            r = client.post(
                f"/projects/{pid}/{path}", headers=session_auth_headers(handle)
            )
            assert r.status_code == 403, r.text
        r = client.post(
            f"/projects/{pid}/{path}",
            headers=session_auth_headers("archive-stranger"),
        )
        assert r.status_code == 404, r.text

    r = client.post(f"/projects/{pid}/archive", headers=AS_OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["archived_at"] is not None


def test_an_archived_project_is_hidden_frozen_and_comes_back_whole(client):
    project = _project(client)
    pid, root = project["id"], project["root_topic_id"]
    mate = session_auth_headers("archive-mate-2")
    join_project_team(client, pid, "archive-mate-2")
    working = _room(client, pid, "还在做的房间")
    done = _room(client, pid, "早就做完的房间")
    assert (
        client.post(f"/topics/{done}/archive", json={}, headers=AS_OWNER).status_code
        == 200
    )
    invited = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": "archive-guest"},
        headers=AS_OWNER,
    )
    assert invited.status_code == 200, invited.text

    archived = client.post(f"/projects/{pid}/archive", headers=AS_OWNER)
    assert archived.status_code == 200, archived.text

    # Gone from every member's list, the owner's included; the owner alone finds
    # it among the archived ones. A direct link still opens it and says so.
    assert pid not in _listed(client, AS_OWNER)
    assert pid not in _listed(client, mate)
    assert pid in _listed(client, AS_OWNER, archived="true")
    assert pid not in _listed(client, mate, archived="true")
    opened = client.get(f"/projects/{pid}", headers=mate)
    assert opened.status_code == 200, opened.text
    assert opened.json()["data"]["archived_at"] is not None

    # Every room went with it, the overview too, and each has its cleanup
    # scheduled: that is what stops the sessions and reclaims the machines.
    for room in (root, working):
        assert _status(client, room) == "archived"
        cleanup = client.get(f"/topics/{room}/cleanup", headers=AS_OWNER)
        assert cleanup.json()["data"]["state"] == "pending", cleanup.text

    # The invitation it had sent out is withdrawn, not left waiting on a project
    # nobody can enter.
    waiting = client.get(
        "/me/invitations", headers=session_auth_headers("archive-guest")
    )
    assert waiting.json()["data"]["data"] == []

    # Writes are refused — to the project, to its rooms, by anyone.
    _refused_as_archived(
        client.post(
            "/topics",
            json={"project_id": pid, "title": "新房间"},
            headers=AS_OWNER,
        )
    )
    _refused_as_archived(
        client.put(
            f"/projects/{pid}/task-naming", json={"mode": "auto"}, headers=AS_OWNER
        )
    )
    _refused_as_archived(
        client.post(
            f"/projects/{pid}/invitations",
            json={"user_handle": "archive-guest"},
            headers=AS_OWNER,
        )
    )
    _refused_as_archived(
        client.post(f"/topics/{working}/unarchive", json={}, headers=AS_OWNER)
    )
    _refused_as_archived(
        client.post(f"/topics/{root}/title", json={"title": "改名"}, headers=mate)
    )

    back = client.post(f"/projects/{pid}/unarchive", headers=AS_OWNER)
    assert back.status_code == 200, back.text
    assert back.json()["data"]["archived_at"] is None
    assert pid in _listed(client, AS_OWNER)
    assert pid in _listed(client, mate)
    assert pid not in _listed(client, AS_OWNER, archived="true")
    # The rooms archived with the project are back; the one archived by hand
    # before it stays where its owner put it.
    assert _status(client, root) == "active"
    assert _status(client, working) == "active"
    assert _status(client, done) == "archived"
    _room(client, pid, "取消归档后的新房间")


def test_archiving_twice_or_unarchiving_a_live_project_changes_nothing(client):
    pid = _project(client)["id"]
    r = client.post(f"/projects/{pid}/unarchive", headers=AS_OWNER)
    assert r.status_code == 200 and r.json()["data"]["archived_at"] is None
    first = client.post(f"/projects/{pid}/archive", headers=AS_OWNER).json()["data"]
    second = client.post(f"/projects/{pid}/archive", headers=AS_OWNER).json()["data"]
    assert second["archived_at"] == first["archived_at"]


def test_an_archived_project_is_not_given_a_memory_dream(client, tmp_path):
    """整理是一轮真会话：一个已经归档的项目，花销再够也不再起这一轮。"""
    screen = StubChannel()
    factory = client.test_request_factory
    chat = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )

    async def go() -> list:
        async with factory() as session:
            await registered(session, OWNER)
            project = await ProjectService(session).create(name="P", owner_handle=OWNER)
            project.settings = {
                **(project.settings or {}),
                "memory_dream": {"threshold_output_tokens": 1},
            }
            await UsageRepository(session).add(
                project_id=project.id,
                conversation_id=None,
                model="m",
                input_tokens=0,
                output_tokens=10,
                cost_usd=0.0,
                kind="chat",
                turn_id=None,
            )
            await ProjectService(session).archive(project.id, by=OWNER)
            project_id = project.id
            await session.commit()
        await chat.sweep_memory_dreams()
        async with factory() as session:
            return list(
                await session.scalars(
                    select(MemoryDreamRun).where(
                        MemoryDreamRun.project_id == project_id
                    )
                )
            )

    assert client.portal.call(go) == []
