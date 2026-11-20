"""An agent reads only what everyone its answer reaches may read.

Whatever 芝士 reads can end up in its answer, in front of everyone the answer is
shown to. So from a channel it reads that channel, the public channels, and the
private ones every person there is also in; from a DM, what that person may
read. Its seat in a room is not enough on its own.
"""

import asyncio
import uuid

from app.api.auth import ActorResolver
from app.api.doc_access import asked_of, reach
from app.auth.project_access import outsiders_reading
from app.core.sandbox_auth import mint_delegated_credential, mint_scoped_token
from app.domain.agent.document import question
from app.domain.living_doc.services import Documents
from app.domain.project.models import Project
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
    session_token,
)

REFUSED = (403, 404)


def _project(client) -> dict:
    """dave's project; alice, bob and carol are on its team."""
    for who in ("alice", "bob", "carol", "dave"):
        seed_user(client, who)
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, p["id"], handle)
    return p


def _private_channel(client, pid: str, by: str, *others: str) -> str:
    made = client.post(
        "/topics",
        json={"project_id": pid, "title": "机密频道", "members_only": True},
        headers=session_auth_headers(by),
    )
    assert made.status_code == 200, made.text
    tid = made.json()["data"]["id"]
    for handle in others:
        added = client.post(
            f"/topics/{tid}/members",
            json={"handle": handle},
            headers=session_auth_headers(by),
        )
        assert added.status_code == 200, added.text
    return tid


def _dm_with_cheese(client, pid: str, who: str) -> str:
    opened = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": who},
        headers=session_auth_headers(who),
    )
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["id"]


def _cheese(client, root: str) -> str:
    rows = client.get(
        f"/topics/{root}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


def _answering_in(pid: str, room: str, agent: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=pid,
                topic_id=room,
                access_scope='project',
                agent_handle=agent,
            )
        }"
    }


def _reads(client, room: str, headers: dict[str, str]) -> int:
    return client.get(f"/topics/{room}/blocks", headers=headers).status_code


def test_from_a_public_channel_it_reads_no_private_channel_and_no_dm(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    secret = _private_channel(client, p["id"], "alice")
    dm = _dm_with_cheese(client, p["id"], "alice")

    token = _answering_in(p["id"], root, cheese)

    assert _reads(client, root, token) == 200
    assert _reads(client, secret, token) in REFUSED
    assert _reads(client, dm, token) in REFUSED


def test_from_a_private_channel_it_reads_what_everyone_there_also_reads(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    here = _private_channel(client, p["id"], "alice", "bob")
    wider = _private_channel(client, p["id"], "alice", "bob", "carol")
    narrower = _private_channel(client, p["id"], "alice")

    token = _answering_in(p["id"], here, cheese)

    assert _reads(client, here, token) == 200
    assert _reads(client, root, token) == 200
    assert _reads(client, wider, token) == 200
    assert _reads(client, narrower, token) in REFUSED


def test_from_a_dm_it_reads_what_that_person_reads(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    dm = _dm_with_cheese(client, p["id"], "alice")
    hers = _private_channel(client, p["id"], "alice")
    not_hers = _private_channel(client, p["id"], "bob")

    token = _answering_in(p["id"], dm, cheese)

    assert _reads(client, root, token) == 200
    assert _reads(client, hers, token) == 200
    assert _reads(client, not_hers, token) in REFUSED


def test_a_project_credential_reads_no_dm(client):
    """It answers outside the platform, to whoever holds it: the project's
    people at most, so only what all of them may read."""
    p = _project(client)
    root = p["root_topic_id"]
    dm = _dm_with_cheese(client, p["id"], "alice")
    issued = client.post(
        f"/projects/{p['id']}/agent-credential",
        json={},
        headers=session_auth_headers("dave"),
    )
    assert issued.status_code == 200, issued.text
    token = {"Authorization": f"Bearer {issued.json()['data']['token']}"}

    assert _reads(client, root, token) == 200
    assert _reads(client, dm, token) in REFUSED


def test_a_document_thread_answer_searches_only_what_every_reader_may(client):
    """A question asked in a comment thread is answered in the thread, which every
    reader of the document reads; asked in the asker's own box, the answer is
    theirs alone. What 芝士 may search for it follows who reads the answer."""
    p = _project(client)
    secret = _private_channel(client, p["id"], "alice")
    post_message(client, secret, "alice", {"content": "紫色长颈鹿的预算"})

    def found(shown_to_readers: bool) -> set[str]:
        token = mint_delegated_credential(
            user_id=None,
            handle="alice",
            agent=None,
            project_id=p["id"],
            work=str(uuid.uuid4()),
            ttl_s=600,
            shown_to_readers=shown_to_readers,
        )
        r = client.get(
            f"/projects/{p['id']}/context/search",
            params={"q": "预算"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        return {h["room_id"] for h in r.json()["data"]["hits"]["records"]}

    assert secret in found(shown_to_readers=False)
    assert secret not in found(shown_to_readers=True)


def test_a_room_documents_thread_answer_reads_only_the_room(client):
    """A document that lives in a room and belongs to no task is read by whoever
    reads that room — including somebody the room seats but the team does not.
    Its thread's answer is read by those same people, so its tools read the
    room, not the project's public channels."""
    p = _project(client)
    root = p["root_topic_id"]
    post_message(client, root, "alice", {"content": "紫色长颈鹿的预算"})
    room = _private_channel(client, p["id"], "alice", "bob")
    post_message(client, room, "alice", {"content": "长颈鹿的预算还有第二页"})
    _off_the_team(client, p["id"], "bob")
    assert _outsider_shape(client, p["id"], room) == ["bob"], "shape not built"

    async def answered() -> str:
        async with client.test_request_factory() as db:
            doc = await Documents(db).create(
                project_id=uuid.UUID(p["id"]),
                room_id=uuid.UUID(room),
                title="一份说明",
                author="alice",
            )
            await db.commit()
            resolver = ActorResolver(
                session=db, bearer=session_token("alice"), cheese_token=""
            )
            reached = await reach(db, resolver, doc.id)
            asked = asked_of(reached)
            assert asked is not None
            return await question.credential(
                db,
                asked=asked,
                agent="芝士",
                asker="alice",
                work=uuid.uuid4(),
                may_edit=True,
                shown_to_readers=True,
            )

    found = client.get(
        f"/projects/{p['id']}/context/search",
        params={"q": "预算"},
        headers={"Authorization": f"Bearer {client.portal.call(answered)}"},
    )
    assert found.status_code == 200, found.text
    rooms = {h["room_id"] for h in found.json()["data"]["hits"]["records"]}
    assert room in rooms, "the room the document lives in was not read"
    assert root not in rooms, "a project-wide answer was read into the room's document"


def test_a_room_documents_thread_opens_with_the_project_only_for_the_room(client):
    """The same document's thread is opened with the project's shared memory,
    goals and recent messages only where everyone who reads the thread may read
    the project. A room seating somebody from outside it is not that."""
    p = _project(client)
    note = (
        "---\nname: pricing-floor\ndescription: 报价底线\ntype: feedback\n---\n\n"
        "报价不低于成本的 1.4 倍。\n"
    )
    put = client.put(
        "/memory/files",
        json={
            "project_id": p["id"],
            "scope": "team",
            "path": "pricing-floor.md",
            "content": note,
        },
        headers=session_auth_headers("dave"),
    )
    assert put.status_code == 200, put.text
    indexed = client.put(
        "/memory/files",
        json={
            "project_id": p["id"],
            "scope": "team",
            "path": "MEMORY.md",
            "content": "- [报价底线](pricing-floor.md) — 报价不低于成本的 1.4 倍\n",
        },
        headers=session_auth_headers("dave"),
    )
    assert indexed.status_code == 200, indexed.text
    room = _private_channel(client, p["id"], "alice", "bob")
    _off_the_team(client, p["id"], "bob")
    assert _outsider_shape(client, p["id"], room) == ["bob"], "shape not built"

    async def opening() -> dict:
        async with client.test_request_factory() as db:
            doc = await Documents(db).create(
                project_id=uuid.UUID(p["id"]),
                room_id=uuid.UUID(room),
                title="一份说明",
                author="alice",
            )
            await db.commit()
            resolver = ActorResolver(
                session=db, bearer=session_token("alice"), cheese_token=""
            )
            reached = await reach(db, resolver, doc.id)
            asked = asked_of(reached)
            assert asked is not None
            return await question.surroundings(db, asked, seat="芝士", asker=None)

    around = client.portal.call(opening)
    assert around.memory is None, (
        "the project's shared memory went into a room's document"
    )


def _off_the_team(client, project: str, handle: str) -> None:
    """Off the team, seats kept: what ``DELETE /teams/{id}/members/{uid}`` does,
    so the project stops being something they read while the room still is."""

    async def off():
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            p = await db.get(Project, uuid.UUID(project))
            person = await UserRepository(db).get_by_handle(handle)
            await TeamService(TeamRepository(db)).remove_team_member(
                team_id=p.team_id, target_user_id=person.id
            )
            await db.commit()

    client.portal.call(off)


def _outsider_shape(client, project: str, room: str) -> list[str]:
    """Who reads the room without reading the project — what makes the room's
    answer read the room and nothing of the project's own."""

    async def reading() -> list[str]:
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            return await outsiders_reading(
                db, project_id=uuid.UUID(project), room_id=uuid.UUID(room)
            )

    return client.portal.call(reading)


def test_the_document_box_opens_with_the_project_only_for_a_project_reader(client):
    """The box's answer is the asker's alone, so its opening context holds the
    project's shared memory only when the asker may read the project."""
    from app.domain.agent.document import question
    from app.domain.living_doc.comments import OnDocument

    p = _project(client)
    note = (
        "---\nname: answer-first\ndescription: 回答先给结论\ntype: feedback\n---\n\n"
        "有结论就先说结论。\n"
    )
    put = client.put(
        "/memory/files",
        json={
            "project_id": p["id"],
            "scope": "team",
            "path": "answer-first.md",
            "content": note,
        },
        headers=session_auth_headers("dave"),
    )
    assert put.status_code == 200, put.text
    indexed = client.put(
        "/memory/files",
        json={
            "project_id": p["id"],
            "scope": "team",
            "path": "MEMORY.md",
            "content": "- [Answer first](answer-first.md) — 回答先给结论\n",
        },
        headers=session_auth_headers("dave"),
    )
    assert indexed.status_code == 200, indexed.text
    doc = client.post(
        f"/projects/{p['id']}/documents",
        json={"title": "笔记"},
        headers=session_auth_headers("dave"),
    )
    assert doc.status_code == 200, doc.text
    asked = question.Asked(
        project_id=uuid.UUID(p["id"]),
        subject=OnDocument(uuid.UUID(doc.json()["data"]["id"])),
        task_id=None,
        conversation_id=None,
    )

    async def opening(asker: str):
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            return await question.surroundings(db, asked, seat="cheese", asker=asker)

    member = asyncio.run(opening("alice"))
    stranger = asyncio.run(opening("mallory"))

    assert member.memory is not None and "answer-first" in member.memory
    assert stranger.memory is None
