"""A document's read-only share link (#3774).

The rules, as a person would state them:

- whoever holds the link reads the document's current text without signing in;
- taking the link back stops it at once, and sharing again makes a new link;
- a project's document is shared by whoever may publish the project's site,
  not by every member;
- a team's own document is shared by its author or a team admin, not by
  every member;
- a person who cannot read a document cannot share it.
"""

import asyncio

from tests.integration.conftest import (
    a_team,
    join_project_team,
    new_project,
    session_auth_headers,
)

OWNER = session_auth_headers("owner")


def _project_doc(client) -> tuple[str, str]:
    project = new_project(client)["id"]
    made = client.post(
        f"/projects/{project}/documents", json={"title": "组会纪要"}, headers=OWNER
    )
    assert made.status_code == 200, made.text
    doc = made.json()["data"]["id"]
    written = client.put(
        f"/documents/{doc}",
        json={"content": "本周进展：数据清洗完成。", "expected_version": 0},
        headers=OWNER,
    )
    assert written.status_code == 200, written.text
    return project, doc


def _share(client, doc, headers=OWNER):
    return client.put(f"/documents/{doc}/share", headers=headers)


def test_anyone_with_the_link_reads_the_current_text(client):
    _project, doc = _project_doc(client)
    made = _share(client, doc)
    assert made.status_code == 200, made.text
    token = made.json()["data"]["link"]["token"]

    read = client.get(f"/shared/{token}")
    assert read.status_code == 200, read.text
    body = read.json()["data"]
    assert body["title"] == "组会纪要"
    assert "数据清洗完成" in body["content"]
    assert "noindex" in read.headers.get("x-robots-tag", "")


def test_taking_the_link_back_stops_it_and_sharing_again_makes_a_new_one(client):
    _project, doc = _project_doc(client)
    first = _share(client, doc).json()["data"]["link"]["token"]

    taken = client.delete(f"/documents/{doc}/share", headers=OWNER)
    assert taken.status_code == 200, taken.text
    assert client.get(f"/shared/{first}").status_code == 404

    second = _share(client, doc).json()["data"]["link"]["token"]
    assert second != first
    assert client.get(f"/shared/{second}").status_code == 200


def test_a_member_who_cannot_publish_the_site_does_not_share(client):
    project, doc = _project_doc(client)
    join_project_team(client, project, "member")
    member = session_auth_headers("member")

    asked = client.get(f"/documents/{doc}/share", headers=member)
    assert asked.status_code == 200, asked.text
    assert asked.json()["data"]["may_share"] is False
    assert _share(client, doc, member).status_code == 403


def test_a_teams_document_is_shared_by_its_author_or_an_admin(client):
    async def make() -> int:
        async with client.test_factory() as session:
            team_id = await a_team(session, "owner")
            await session.commit()
            return team_id

    team_id = asyncio.run(make())
    project = new_project(client, team_id=team_id)["id"]
    join_project_team(client, project, "member")
    join_project_team(client, project, "admin", admin=True)
    member = session_auth_headers("member")
    doc = client.post(
        f"/teams/{team_id}/documents", json={"title": "新人须知"}, headers=member
    ).json()["data"]["id"]

    other = session_auth_headers("other")
    join_project_team(client, project, "other")
    assert _share(client, doc, other).status_code == 403
    assert _share(client, doc, member).status_code == 200
    assert _share(client, doc, session_auth_headers("admin")).status_code == 200


def test_a_person_who_cannot_read_it_does_not_share_it(client):
    _project, doc = _project_doc(client)
    stranger = session_auth_headers("stranger")
    new_project(client, owner="stranger")
    assert _share(client, doc, stranger).status_code in (403, 404)
