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

from app.core.sandbox_auth import mint_delegated_credential
from app.domain.assistant.changes import of_work
from app.domain.task.models import TaskMembership
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers
from tests.integration.test_assistant import _ledger_user, _task
from tests.integration.test_doc_edits import ALICE_PARAGRAPH, _doc, _document
from tests.support.living_doc import document_of


def _credential(
    client,
    task: str,
    *,
    asker: str = "bob",
    agent: str | None = None,
    read_only: bool = True,
    ttl_s: int = 300,
    work: str | None = None,
    user_id: int | None = None,
) -> str:
    project = client.get(
        f"/topics/{task}/task", headers=session_auth_headers("alice")
    ).json()["data"]["project_id"]
    return mint_delegated_credential(
        user_id=user_id,
        handle=asker,
        agent=agent,
        project_id=project,
        topic_id=task,
        work=work or str(uuid.uuid4()),
        read_only=read_only,
        ttl_s=ttl_s,
    )


def _path(client, task: str) -> str:
    """The task's document, as its routes address it."""
    return f"/documents/{document_of(client, task)}"


def _as(token: str) -> dict:
    # In place of the platform's secret the test client otherwise sends: a
    # session answering a question has no such thing.
    return {"Authorization": f"Bearer {token}"}


def _edit(client, task: str, token: str):
    return client.post(
        f"{_path(client, task)}/edits",
        json={"edits": [{"old": "讲范围", "new": "讲边界"}]},
        headers=_as(token),
    )


def test_it_reads_what_the_asker_may_read(client):
    task, seat, _ = _document(client)

    own = client.get(_path(client, task), headers=_as(_credential(client, task)))
    stranger = client.get(
        _path(client, task),
        headers=_as(_credential(client, task, asker="mallory")),
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


def test_it_acts_only_where_it_was_minted(client):
    """An agent's credential, minted for a room. A person's own 芝士, minted
    for no room, reads as the person does
    (`test_a_persons_own_cheese_reads_wherever_the_person_may`)."""
    task, _, _ = _document(client)
    elsewhere, _, _ = _document(client)

    other_room = client.get(
        _path(client, elsewhere), headers=_as(_credential(client, task))
    )
    unbound = client.get("/tasks/joined", headers=_as(_credential(client, task)))

    assert other_room.status_code == 403
    assert unbound.status_code == 403


def test_a_question_that_may_only_be_answered_changes_nothing(
    client,
):
    task, seat, _ = _document(client)

    r = _edit(client, task, _credential(client, task, agent=seat, read_only=True))

    assert r.status_code == 403
    assert ALICE_PARAGRAPH in _doc(client, task)["content"]


def test_an_edit_is_the_agents_at_the_askers_request_and_noted_under_the_answer(
    client,
):
    task, seat, _ = _document(client)
    work = str(uuid.uuid4())
    bob = _user_id(client, "bob")

    r = _edit(
        client,
        task,
        _credential(client, task, agent=seat, read_only=False, work=work, user_id=bob),
    )

    assert r.status_code == 200, r.text
    assert "李老师写的第二段，讲边界。" in _doc(client, task)["content"]
    latest = client.get(
        f"{_path(client, task)}/history", headers=session_auth_headers("alice")
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == seat and "requested_by" not in latest

    noted = _noted(client, work)
    assert [(c.user_id, c.kind) for c in noted] == [(bob, "doc")]
    assert noted[0].payload["edits"] == [{"old": "讲范围", "new": "讲边界"}]


def test_what_the_agent_changed_is_undone_and_redone_by_the_asker(client):
    """The asker puts the document back as it was before the answer, as
    themselves, and can put the answer's change back again."""
    task, seat, _ = _document(client)
    work = str(uuid.uuid4())
    bob = _user_id(client, "bob")
    _edit(
        client,
        task,
        _credential(client, task, agent=seat, read_only=False, work=work, user_id=bob),
    )
    change = _noted(client, work)[0].id

    r = client.post(
        f"/assistant/changes/{change}/undo",
        json={},
        headers=_signed_in(client, "bob"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["changes"][0]["undone"] == ["text"]
    assert "李老师写的第二段，讲范围。" in _doc(client, task)["content"]
    latest = client.get(
        f"{_path(client, task)}/history", headers=session_auth_headers("alice")
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == "bob"
    assert (
        client.post(
            f"/assistant/changes/{change}/undo",
            json={},
            headers=_signed_in(client, "alice"),
        ).status_code
        == 404
    )

    r = client.post(
        f"/assistant/changes/{change}/redo",
        json={},
        headers=_signed_in(client, "bob"),
    )
    assert r.status_code == 200, r.text
    assert "李老师写的第二段，讲边界。" in _doc(client, task)["content"]


def _signed_in(client, handle: str) -> dict[str, str]:
    """A token with the person's account id, which the assistant routes ask for."""
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _user_id(client, handle: str) -> int:
    return _ledger_user(client, handle)[0]


def _noted(client, work: str):
    async def read():
        async with client.test_factory() as db:
            return await of_work(db, work)

    return asyncio.run(read())


def test_an_expired_or_forged_credential_is_none(client):
    task, seat, _ = _document(client)
    expired = _credential(client, task, ttl_s=-1)
    live = _credential(client, task)
    body, signature = live.split(".", 1)
    forged = f"{body}.{signature[::-1]}"

    for token in (expired, forged):
        r = client.get(_path(client, task), headers=_as(token))
        assert r.status_code == 401, token
    model = client.post(
        "/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {live}"},
        content=b'{"model":"m","messages":[]}',
    )
    assert model.status_code in (401, 403)


def test_a_persons_own_cheese_reads_wherever_the_person_may(client):
    """A person's own 芝士 reads as the person: the project it was asked from,
    any other project the person is in, and the routes that name no project
    (their own drafts and 题目); a project the person is not in, it cannot. An
    agent's credential still reaches only its room."""
    from tests.integration.test_message_edit import _room

    room, project = _room(client)
    other, _ = _room(client)
    alice = _user_id(client, "alice")
    seed_user(client, "carol")
    carol = _user_id(client, "carol")

    def personal(user_id: int, handle: str, project_id: str) -> str:
        return mint_delegated_credential(
            user_id=user_id,
            handle=handle,
            work=str(uuid.uuid4()),
            project_id=project_id,
            ttl_s=300,
        )

    token = personal(alice, "alice", project)
    assert client.get(f"/topics/{room}/history", headers=_as(token)).status_code == 200
    assert client.get("/tasks/joined", headers=_as(token)).status_code == 200
    assert client.get(f"/topics/{other}/history", headers=_as(token)).status_code == 200
    outsider = personal(carol, "carol", project)
    assert client.get(f"/topics/{room}/history", headers=_as(outsider)).status_code in (
        403,
        404,
    )

    task, seat, _ = _document(client)
    agent = _credential(client, task, agent=seat)
    assert client.get("/tasks/joined", headers=_as(agent)).status_code == 403
