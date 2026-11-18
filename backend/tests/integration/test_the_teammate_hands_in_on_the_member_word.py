"""A project's AI teammate hands the project's work in to its 题目 only on the
member's own word.

The rules, as a person would state them:

- The teammate prepares what goes into each submission item; nothing is handed
  in until the member, having been told, says yes. Then the 题目 holds an
  ordinary submission whose submitter is the member, with the files as they
  were when prepared, and the channel records that the teammate handed it in
  for them.
- Anyone else saying yes, or the member's own message from before the
  preparation, hands nothing in.
- A project not made from a 题目 has nothing to hand in to.
- A 题目 that takes one submission per stage, already handed in, is refused
  when the teammate prepares.
- Files that changed after the preparation are not what the member agreed to:
  nothing is handed in.
- Handing in on the 题目 page goes on as before (its own tests).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.sandbox_auth import mint_scoped_token
from app.core.storage import get_storage_backend
from app.domain.agent.models import AgentTurn
from app.domain.attachment.services import AttachmentService
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from tests.integration.conftest import (
    UserCreator,
    post_project,
    session_auth_headers,
)
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — the submitted files land there
)
from tests.integration.test_task_submission_code_sha import _commit

CSIM = "int main(void) { return 0; }\n"

FORM = [
    {"prompt": "csim.c", "type": "FILE"},
    {"prompt": "实验说明", "type": "TEXT"},
]


def _project_of_a_task(api_client, user_client, *, name: str, **task) -> dict:
    """A member who started a 题目 that asks for ``csim.c`` and a note, with
    ``csim.c`` committed in the project's repository."""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name=name, submissionSchema=FORM, **task)
    user, token = _member_of(user_client, api_client, board)
    started = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    ).json()["data"]
    project = started["project"]
    _commit(project["id"], "csim.c", CSIM)
    room = project["root_topic_id"]
    seats = [
        m["member_handle"]
        for m in api_client.get(f"/topics/{room}/members", headers=_auth(token)).json()[
            "data"
        ]["data"]
        if m["agent"]
    ]
    return {
        "task": task_id,
        "participant": started["participant"]["id"],
        "project": project["id"],
        "room": room,
        "seat": seats[0],
        "user": user,
        "token": token,
    }


def _wake(api_client, db_session, at: dict, author: str, words: str) -> None:
    """``author`` says ``words`` in the channel, and the teammate's turn for
    that message is running, named by it as the runtime names a turn a
    person's message starts. The line is written straight in: sending it
    through the channel would start a real turn this test does not run."""

    async def says():
        block = await BlockRepository(db_session).add(
            project_id=uuid.UUID(at["project"]),
            conversation_id=uuid.UUID(at["room"]),
            author=author,
            author_type=AuthorType.participant,
            content=words,
            kind=BlockKind.message,
        )
        await db_session.flush()
        return block.id

    said = api_client.portal.call(says)

    async def wake():
        db_session.add(
            AgentTurn(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(at["room"]),
                continuation_id=said,
                agent_handle=at["seat"],
                author=author,
                content=words,
                started_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    api_client.portal.call(wake)


def _as_teammate(api_client: TestClient, at: dict, step: str, body: dict):
    token = mint_scoped_token(
        project_id=at["project"], topic_id=at["room"], agent_handle=at["seat"]
    )
    return api_client.post(
        f"/topics/{at['room']}/hand-in/{step}",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def _prepare(api_client, at, *, file: str = "csim.c"):
    return _as_teammate(
        api_client,
        at,
        "prepare",
        {
            "items": [
                {"item": 1, "file": file},
                {"item": 2, "text": "用 LRU 替换策略"},
            ]
        },
    )


def _submissions(api_client, at) -> list[dict]:
    return api_client.get(
        f"/tasks/{at['task']}/participants/{at['participant']}/submissions",
        headers=_auth(at["token"]),
    ).json()["data"]["submissions"]


def _lines(api_client, at) -> list[dict]:
    return api_client.get(
        f"/topics/{at['room']}/blocks", headers=_auth(at["token"])
    ).json()["data"]["data"]


def test_the_member_saying_yes_hands_it_in_as_them(
    api_client: TestClient, user_client: UserCreator, db_session
):
    at = _project_of_a_task(api_client, user_client, name="CacheLab")
    _wake(api_client, db_session, at, at["user"].username, "帮我交作业")
    prepared = _prepare(api_client, at)
    assert prepared.status_code == 200, prepared.text
    assert _submissions(api_client, at) == []

    _wake(api_client, db_session, at, at["user"].username, "可以，交吧")
    confirmed = _as_teammate(api_client, at, "confirm", {})

    assert confirmed.status_code == 200, confirmed.text
    [submission] = _submissions(api_client, at)
    assert submission["submitter"]["id"] == at["user"].user_id
    file, note = submission["content"]
    assert note["contentText"] == "用 LRU 替换策略"
    assert file["contentAttachment"]["meta"]["name"] == "csim.c"

    async def handed_in_bytes():
        attachments = AttachmentService.from_session(
            session=db_session, storage=get_storage_backend()
        )
        data, _, _ = await attachments.download(file["contentAttachment"]["id"])
        return data

    assert api_client.portal.call(handed_in_bytes) == CSIM.encode()
    [line] = [
        b
        for b in _lines(api_client, at)
        if (b.get("meta") or {}).get("event_type") == "teammate_handed_in"
    ]
    assert line["content"] == (
        f"<@{at['seat']}>代 <@{at['user'].username}> 向「CacheLab」提交了第 1 版"
    )


def test_someone_else_saying_yes_hands_nothing_in(
    api_client: TestClient, user_client: UserCreator, db_session
):
    at = _project_of_a_task(api_client, user_client, name="别人说的不算")
    _wake(api_client, db_session, at, at["user"].username, "帮我交作业")
    assert _prepare(api_client, at).status_code == 200
    _wake(api_client, db_session, at, "bob", "交吧")

    refused = _as_teammate(api_client, at, "confirm", {})

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "handInMemberWordOnly"
    assert _submissions(api_client, at) == []


def test_the_member_word_from_before_the_preparation_hands_nothing_in(
    api_client: TestClient, user_client: UserCreator, db_session
):
    at = _project_of_a_task(api_client, user_client, name="先说的不算")
    _wake(api_client, db_session, at, at["user"].username, "帮我交作业，直接交")
    assert _prepare(api_client, at).status_code == 200

    refused = _as_teammate(api_client, at, "confirm", {})

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "handInWordBeforePrepare"
    assert _submissions(api_client, at) == []


def test_files_changed_after_the_preparation_are_not_handed_in(
    api_client: TestClient, user_client: UserCreator, db_session
):
    at = _project_of_a_task(api_client, user_client, name="文件变了")
    _wake(api_client, db_session, at, at["user"].username, "帮我交作业")
    assert _prepare(api_client, at).status_code == 200
    _commit(at["project"], "csim.c", "int main(void) { return 1; }\n")
    _wake(api_client, db_session, at, at["user"].username, "可以")

    refused = _as_teammate(api_client, at, "confirm", {})

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "handInFilesChanged"
    assert _submissions(api_client, at) == []


def test_a_task_taking_one_submission_already_handed_in_is_refused_when_prepared(
    api_client: TestClient, user_client: UserCreator, db_session
):
    at = _project_of_a_task(
        api_client, user_client, name="只能交一次", resubmittable=False
    )
    handed = api_client.post(
        f"/tasks/{at['task']}/participants/{at['participant']}/submissions",
        json=[{"text": "页面上交的"}, {"text": "说明"}],
        headers=_auth(at["token"]),
    )
    assert handed.status_code == 200, handed.text
    _wake(api_client, db_session, at, at["user"].username, "再帮我交一版")

    refused = _prepare(api_client, at)

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "submissionNotResubmittable"


def test_a_project_not_made_from_a_task_has_nothing_to_hand_in_to(
    api_client: TestClient,
):
    project = post_project(api_client, json={"name": "P"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = [
        m["member_handle"]
        for m in api_client.get(
            f"/topics/{room}/members", headers=session_auth_headers("alice")
        ).json()["data"]["data"]
        if m["agent"]
    ][0]
    at = {"project": project["id"], "room": room, "seat": seat}

    refused = _prepare(api_client, at)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "handInNoTask"
