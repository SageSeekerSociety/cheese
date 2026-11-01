"""A message send, a document save and a profile page each read a row once.

- Sending a message records its result on the idempotency key the send
  claimed. That is one ``UPDATE`` by key; it used to read the row back first
  only to set one column on it.
- Saving a task's document asks which task owns it before the write and what
  it is called after; both answers are the same task row, read once.
- A person's profile page draws their profile and the face they chose; both
  come from one read of the profile row.
"""

import re
import uuid

from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.integration.test_topic_list_batches import _seed_populated_project
from tests.support.counting_session import counting_sql
from tests.support.living_doc import document_of

_KEY_READ = re.compile(r"FROM idempotency_keys WHERE idempotency_keys\.key")
_KEY_WRITE = re.compile(r"^UPDATE idempotency_keys SET result")
_DOC_TASK = re.compile(r"FROM tasks WHERE tasks\.document_id")
_PROFILE = re.compile(r"FROM user_profile ")


def _count(statements: list[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for s in statements if pattern.search(s))


def test_a_sent_message_records_its_result_without_reading_the_key_back(client):
    _, rooms = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    body = {"content": "只记一次", "request_id": str(uuid.uuid4())}

    with counting_sql(client) as statements:
        first = client.post(f"/topics/{rooms[0]}/messages", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert _count(statements, _KEY_READ) == 0, statements
    assert _count(statements, _KEY_WRITE) == 1, statements

    # The replay still answers with the first send's result.
    again = client.post(f"/topics/{rooms[0]}/messages", json=body, headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] == first.json()["data"]["id"]


def test_saving_a_task_document_reads_its_owning_task_once_per_request(client):
    headers = session_auth_headers("owner")
    project = post_project(client, json={"name": "P"}, owner="owner").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "话题"}, headers=headers
    ).json()["data"]["id"]
    task = open_task(client, room, owner="owner")["id"]
    doc = document_of(client, task)
    first = client.put(
        f"/documents/{doc}",
        json={"content": "## 目标\n第一版", "expected_version": 0},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    with counting_sql(client) as statements:
        r = client.put(
            f"/documents/{doc}",
            json={"content": "## 目标\n第二版", "expected_version": 1},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    # One read to reach the document, one in the save the collab service calls
    # back with; the line's title reuses the second.
    assert _count(statements, _DOC_TASK) == 2, statements


def test_a_profile_page_reads_the_profile_and_face_together(client):
    _seed_populated_project(client)
    with counting_sql(client) as statements:
        r = client.get("/users/alice/profile", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    assert _count(statements, _PROFILE) == 1, statements
