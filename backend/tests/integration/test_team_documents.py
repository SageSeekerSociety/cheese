"""A team's own documents, a person's own, and moving a document (#3774).

The rules, as a person would state them:

- a document of no project is its team's: the team's members list, open and
  write it, and nobody outside the team does;
- a person's own documents are their personal team's, so only theirs;
- a team's own document is deleted or moved by its author or a team admin;
- moving changes who reads it, and lands only where the mover is a member;
- an AI teammate reads no team document — except one the course its project
  belongs to points it at.
"""

import asyncio

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import seed_task_with_protocol, start_task_project
from tests.integration.conftest import (
    a_team,
    join_project_team,
    new_project,
    room_agent_seat,
    session_auth_headers,
)

OWNER = session_auth_headers("owner")


def _team(client) -> tuple[int, str]:
    """A shared team owned by ``owner``, and a project of it."""

    async def make() -> int:
        async with client.test_factory() as session:
            team_id = await a_team(session, "owner")
            await session.commit()
            return team_id

    team_id = asyncio.run(make())
    project = new_project(client, team_id=team_id)
    return team_id, project["id"]


def _create(client, team_id: int, headers=OWNER, title="实验室新人须知"):
    return client.post(
        f"/teams/{team_id}/documents", json={"title": title}, headers=headers
    )


def _listed(client, team_id: int, headers=OWNER) -> list[str]:
    found = client.get(f"/teams/{team_id}/documents", headers=headers)
    assert found.status_code == 200, found.text
    return [d["title"] for d in found.json()["data"]["data"]]


def test_a_team_document_is_the_teams_members(client):
    team_id, project_id = _team(client)
    join_project_team(client, project_id, "member")
    made = _create(client, team_id)
    assert made.status_code == 200, made.text
    doc = made.json()["data"]
    assert (doc["team_id"], doc["project_id"]) == (team_id, None)

    member = session_auth_headers("member")
    assert _listed(client, team_id, member) == ["实验室新人须知"]
    written = client.put(
        f"/documents/{doc['id']}",
        json={"content": "第一周：领工位。", "expected_version": 0},
        headers=member,
    )
    assert written.status_code == 200, written.text

    stranger = session_auth_headers("stranger")
    join_project_team(client, new_project(client, owner="stranger")["id"], "stranger")
    assert client.get(f"/documents/{doc['id']}", headers=stranger).status_code == 403
    listing = client.get(f"/teams/{team_id}/documents", headers=stranger)
    assert listing.status_code == 403


def test_a_project_document_is_not_among_the_teams_own(client):
    team_id, project_id = _team(client)
    made = client.post(
        f"/projects/{project_id}/documents", json={"title": "开题报告"}, headers=OWNER
    )
    assert made.status_code == 200, made.text
    assert made.json()["data"]["team_id"] == team_id
    assert _listed(client, team_id) == []


def test_a_team_document_is_deleted_by_its_author_or_an_admin(client):
    team_id, project_id = _team(client)
    join_project_team(client, project_id, "member")
    join_project_team(client, project_id, "admin", admin=True)
    doc = _create(client, team_id).json()["data"]["id"]

    refused = client.delete(f"/documents/{doc}", headers=session_auth_headers("member"))
    assert refused.status_code == 403, refused.text
    deleted = client.delete(f"/documents/{doc}", headers=session_auth_headers("admin"))
    assert deleted.status_code == 200, deleted.text
    assert _listed(client, team_id) == []


def test_moving_a_document_changes_who_reads_it(client):
    team_id, project_id = _team(client)
    join_project_team(client, project_id, "member")
    doc = _create(client, team_id).json()["data"]["id"]
    member = session_auth_headers("member")

    places = client.get(f"/documents/{doc}/places", headers=OWNER)
    assert places.status_code == 200, places.text
    shown = {p["team_id"]: p for p in places.json()["data"]["places"]}
    assert project_id in [p["id"] for p in shown[team_id]["projects"]]
    personal = next(p for p in shown.values() if p["personal"])

    to_me = client.put(
        f"/documents/{doc}/place", json={"team_id": personal["team_id"]}, headers=OWNER
    )
    assert to_me.status_code == 200, to_me.text
    assert client.get(f"/documents/{doc}", headers=member).status_code == 403

    to_project = client.put(
        f"/documents/{doc}/place",
        json={"team_id": team_id, "project_id": project_id},
        headers=OWNER,
    )
    assert to_project.status_code == 200, to_project.text
    assert to_project.json()["data"]["number"] is not None
    assert client.get(f"/documents/{doc}", headers=member).status_code == 200


def test_only_the_author_or_an_admin_moves_and_only_where_they_belong(client):
    team_id, project_id = _team(client)
    join_project_team(client, project_id, "member")
    doc = _create(client, team_id).json()["data"]["id"]
    other_team, _ = _team(client)

    by_member = client.put(
        f"/documents/{doc}/place",
        json={"team_id": team_id, "project_id": project_id},
        headers=session_auth_headers("member"),
    )
    assert by_member.status_code == 403, by_member.text

    mine = session_auth_headers("member")
    own = _create(client, team_id, headers=mine, title="我的草稿").json()["data"]["id"]
    elsewhere = client.put(
        f"/documents/{own}/place", json={"team_id": other_team}, headers=mine
    )
    assert elsewhere.status_code == 403, elsewhere.text


def _agent_headers(client, project_id: str) -> dict:
    """The credential a session in a channel of this project runs with: it
    reaches across the project, as the project's library documents need."""
    room = client.post(
        "/topics", json={"project_id": project_id, "title": "讨论"}, headers=OWNER
    )
    assert room.status_code == 200, room.text
    topic_id = room.json()["data"]["id"]
    return {
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=project_id,
                topic_id=topic_id,
                agent_handle=room_agent_seat(client, topic_id),
                access_scope='project',
            )
        }"
    }


def test_an_ai_teammate_reads_only_the_team_documents_its_course_names(client):
    team_id, _ = _team(client)
    named = _create(client, team_id, title="本周阅读").json()["data"]["id"]
    other = _create(client, team_id, title="组内机密").json()["data"]["id"]
    plain = new_project(client)["id"]
    assert (
        client.get(
            f"/documents/{named}", headers=_agent_headers(client, plain)
        ).status_code
        == 403
    )

    task = seed_task_with_protocol(
        client, override={"teaching": {"document_ids": [named]}}
    )
    course = start_task_project(client, task, handle="owner")
    agent = _agent_headers(client, course)

    got = client.get(f"/documents/{named}", headers=agent)
    assert got.status_code == 200, got.text
    assert client.get(f"/documents/{other}", headers=agent).status_code == 403
