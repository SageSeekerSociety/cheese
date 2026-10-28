"""A document's read-only share link (#3774).

The rules, as a person would state them:

- whoever holds the link reads the document's current text without signing in;
- taking the link back stops it at once, and sharing again makes a new link;
- a project's document is shared by whoever's role shares documents: admins
  and members by default, not mentors;
- a team's own document is shared by the person it is theirs or a team admin,
  not by every member — and it stays theirs when others edit it;
- a document 芝士 wrote is the person's who asked for it;
- a person who cannot read a document cannot share it.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.models import AgentTurn
from tests.integration.conftest import (
    _register,
    a_team,
    join_project_team,
    new_project,
    room_agent_seat,
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


def test_a_member_shares_a_projects_document_and_a_mentor_does_not(client):
    project, doc = _project_doc(client)
    join_project_team(client, project, "member")
    member = session_auth_headers("member")
    _register(client, "teacher")
    invited = client.post(
        f"/projects/{project}/invitations",
        json={"user_handle": "teacher", "role": "mentor"},
        headers=OWNER,
    ).json()["data"]["id"]
    teacher = session_auth_headers("teacher")
    client.post(
        f"/invitations/{invited}/respond", json={"accept": True}, headers=teacher
    )

    asked = client.get(f"/documents/{doc}/share", headers=member)
    assert asked.status_code == 200, asked.text
    assert asked.json()["data"]["may_share"] is True
    assert _share(client, doc, member).status_code == 200
    assert (
        client.get(f"/documents/{doc}/share", headers=teacher).json()["data"][
            "may_share"
        ]
        is False
    )
    assert _share(client, doc, teacher).status_code == 403


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


def _team_project(client) -> tuple[int, str]:
    async def make() -> int:
        async with client.test_factory() as session:
            team_id = await a_team(session, "owner")
            await session.commit()
            return team_id

    team_id = asyncio.run(make())
    return team_id, new_project(client, team_id=team_id)["id"]


def test_a_teams_document_stays_its_creators_when_someone_else_edits_it(client):
    team_id, project = _team_project(client)
    join_project_team(client, project, "member")
    join_project_team(client, project, "other")
    member = session_auth_headers("member")
    other = session_auth_headers("other")
    doc = client.post(
        f"/teams/{team_id}/documents", json={"title": "新人须知"}, headers=member
    ).json()["data"]["id"]
    edited = client.put(
        f"/documents/{doc}",
        json={"content": "第一周：熟悉组里的代码。", "expected_version": 0},
        headers=other,
    )
    assert edited.status_code == 200, edited.text

    assert _share(client, doc, other).status_code == 403
    assert _share(client, doc, member).status_code == 200
    about = client.get(f"/documents/{doc}/about", headers=member).json()["data"]
    assert (about["created_by"], about["drafted_by"]) == ("member", None)


def test_a_document_ai_wrote_is_the_person_who_asked_for_it(client):
    team_id, project = _team_project(client)
    join_project_team(client, project, "alice")
    join_project_team(client, project, "bob")
    alice = session_auth_headers("alice")
    general = client.get(f"/projects/{project}", headers=OWNER).json()["data"][
        "root_topic_id"
    ]
    seat = room_agent_seat(client, general)

    async def alice_asks() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:
            session.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(general),
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    content="帮我写一份新人须知",
                    started_at=now,
                    delivered_at=now,
                    agent_handle=seat,
                )
            )
            await session.commit()

    asyncio.run(alice_asks())
    written = client.post(
        f"/projects/{project}/documents",
        json={"title": "新人须知", "content": "第一周：熟悉组里的代码。"},
        headers={
            "Authorization": f"Bearer {mint_session_token(project, general, seat)}"
        },
    )
    assert written.status_code == 200, written.text
    doc = written.json()["data"]["id"]
    # Its authors are alice and the teammate who wrote it, side by side.
    about = client.get(f"/documents/{doc}/about", headers=alice).json()["data"]
    assert (about["created_by"], about["drafted_by"]) == ("alice", seat)
    assert client.post(f"/documents/{doc}/keep", headers=alice).status_code == 200
    moved = client.put(
        f"/documents/{doc}/place", json={"team_id": team_id}, headers=alice
    )
    assert moved.status_code == 200, moved.text

    assert _share(client, doc, session_auth_headers("bob")).status_code == 403
    assert _share(client, doc, alice).status_code == 200
