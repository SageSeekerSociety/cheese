"""The credential a 芝士 answering someone's question acts with.

It is minted for one question and stands for the person who asked, through the
agent that answers:

* what it reads is what the asker may read, and no more;
* it opens only the routes that declare they accept it, and acts only where it
  was minted for;
* a question that may only be answered changes nothing;
* what it changes is authored by the agent at the asker's request, and noted
  under the answer;
* an expired or forged one is no credential, and the routes that read
  credentials themselves (the model route) refuse it.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from redis.asyncio import from_url

from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.living_doc import work_edits
from app.domain.task.models import TaskMembership
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers
from tests.integration.test_assistant import _ledger_user, _task
from tests.integration.test_doc_edits import ALICE_PARAGRAPH, _doc, _document
from tests.support.living_doc import document_of


def _credential(
    client,
    room: str,
    *,
    asker: str = "bob",
    agent: str | None = None,
    read_only: bool = True,
    ttl_s: int = 300,
    work: str | None = None,
) -> str:
    project = client.get(
        f"/topics/{room}", headers=session_auth_headers("alice")
    ).json()["data"]["project_id"]
    return mint_delegated_credential(
        user_id=None,
        handle=asker,
        agent=agent,
        project_id=project,
        topic_id=room,
        work=work or str(uuid.uuid4()),
        read_only=read_only,
        ttl_s=ttl_s,
    )


def _path(client, room: str) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


def _as(token: str) -> dict:
    # In place of the platform's secret the test client otherwise sends: a
    # session answering a question has no such thing.
    return {"X-Cheese-Token": token}


def _edit(client, room: str, token: str):
    return client.post(
        f"{_path(client, room)}/edits",
        json={"edits": [{"old": "讲范围", "new": "讲边界"}]},
        headers=_as(token),
    )


def test_it_reads_what_the_asker_may_read(client):
    room, seat = _document(client)

    own = client.get(_path(client, room), headers=_as(_credential(client, room)))
    stranger = client.get(
        _path(client, room),
        headers=_as(_credential(client, room, asker="mallory")),
    )

    assert own.status_code == 200, own.text
    assert ALICE_PARAGRAPH in own.json()["data"]["content"]
    assert stranger.status_code in (403, 404)


def test_it_lists_only_the_askers_own_tasks(client):
    seed_user(client, "asker")
    seed_user(client, "other")
    me, _, _ = _ledger_user(client, "asker")
    other, _, _ = _ledger_user(client, "other")
    mine, theirs = _task(client, name="我领的那道"), _task(client, name="别人领的那道")

    async def join() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            for user, task in ((me, mine), (other, theirs)):
                s.add(
                    TaskMembership(
                        task_id=task,
                        member_id=user,
                        approved=0,
                        is_team=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await s.commit()

    asyncio.run(join())
    token = mint_delegated_credential(
        user_id=me, handle="asker", work=str(uuid.uuid4()), ttl_s=300
    )

    r = client.get("/tasks/joined", headers=_as(token))

    assert r.status_code == 200, r.text
    titles = [task["title"] for task in r.json()["data"]]
    assert "我领的那道" in titles and "别人领的那道" not in titles


def test_it_opens_only_the_routes_that_accept_it(client):
    room, _ = _document(client)
    token = _credential(client, room, read_only=False)

    message = client.post(
        f"/topics/{room}/messages",
        json={"content": "我替 bob 说一句", "request_id": str(uuid.uuid4())},
        headers=_as(token),
    )
    members = client.get(f"/topics/{room}/members", headers=_as(token))

    assert message.status_code == 403
    assert members.status_code == 403


def test_it_acts_only_where_it_was_minted(client):
    room, _ = _document(client)
    elsewhere, _ = _document(client)
    nowhere = mint_delegated_credential(
        user_id=None, handle="bob", work=str(uuid.uuid4()), ttl_s=300
    )

    other_room = client.get(
        _path(client, elsewhere), headers=_as(_credential(client, room))
    )
    no_room = client.get(_path(client, room), headers=_as(nowhere))
    unbound = client.get("/tasks/joined", headers=_as(_credential(client, room)))

    assert other_room.status_code == 403
    assert no_room.status_code == 403
    assert unbound.status_code == 403


def test_a_question_that_may_only_be_answered_changes_nothing(
    client,
):
    room, seat = _document(client)

    r = _edit(client, room, _credential(client, room, agent=seat, read_only=True))

    assert r.status_code == 403
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_an_edit_is_the_agents_at_the_askers_request_and_noted_under_the_answer(
    client,
):
    room, seat = _document(client)
    work = str(uuid.uuid4())

    r = _edit(
        client, room, _credential(client, room, agent=seat, read_only=False, work=work)
    )

    assert r.status_code == 200, r.text
    assert "李老师写的第二段，讲边界。" in _doc(client, room)["content"]
    latest = client.get(
        f"{_path(client, room)}/history", headers=session_auth_headers("alice")
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == seat and latest["requested_by"] == "bob"

    async def noted() -> list[dict]:
        redis = from_url(settings.redis_url)
        try:
            return await work_edits.take(redis, work)
        finally:
            await redis.aclose()

    assert client.portal.call(noted) == [{"old": "讲范围", "new": "讲边界"}]


def test_an_expired_or_forged_credential_is_none(client):
    room, seat = _document(client)
    expired = _credential(client, room, ttl_s=-1)
    live = _credential(client, room)
    body, signature = live.split(".", 1)
    forged = f"{body}.{signature[::-1]}"

    for token in (expired, forged):
        r = client.get(_path(client, room), headers=_as(token))
        assert r.status_code == 401, token
    model = client.post(
        "/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {live}", "X-Cheese-Token": ""},
        content=b'{"model":"m","messages":[]}',
    )
    assert model.status_code in (401, 403)
