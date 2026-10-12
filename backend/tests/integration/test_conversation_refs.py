"""`<#id>` references: what each one names, and who is told when one names nothing.

The rules, as a person would state them:

- a `<#id>` names a channel, a task or a 支线, and whoever reads it learns which
  one, what it is called and which channel it is in;
- an id that names none of them, or a channel only its members see, names
  nothing to anyone outside it, so the page can say so instead of borrowing a
  name;
- whoever writes a document holding a `<#id>` that names nothing in its project
  is told in the answer to that write, and again whenever the document is read
  while it still does;
- an agent's message holding one gets a line beside it saying so.
"""

import uuid

from tests.integration.conftest import (
    in_thread,
    join_project_team,
    new_project,
    open_task,
    room_agent_headers,
    session_auth_headers,
)
from tests.support.living_doc import document_of

OWNER = session_auth_headers("owner")
MEMBER = session_auth_headers("member")


def _channel(client, project: str, title: str, **extra) -> dict:
    made = client.post(
        "/topics", json={"project_id": project, "title": title, **extra}, headers=OWNER
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _refs(client, ids: list[str], headers=OWNER) -> dict:
    found = client.get(
        "/addresses/refs", params={"ids": ",".join(ids)}, headers=headers
    )
    assert found.status_code == 200, found.text
    return found.json()["data"]


def _warnings_naming(answer: dict, ref: str) -> list[str]:
    return [w for w in answer.get("warnings") or [] if ref in w]


def test_a_reference_says_whether_it_names_a_channel_a_task_or_a_thread(client):
    project = new_project(client)
    channel = _channel(client, project["id"], "前端")
    task = open_task(client, channel["id"], "修滚动", owner="owner", start=False)
    thread = in_thread(client, channel["id"], "owner")
    nothing = str(uuid.uuid4())

    found = _refs(client, [channel["id"], task["id"], thread, nothing])

    assert found[channel["id"]] == {
        "kind": "room",
        "title": "前端",
        "room_id": channel["id"],
        "project_id": project["id"],
    }
    assert (found[task["id"]]["kind"], found[task["id"]]["title"]) == ("task", "修滚动")
    assert found[task["id"]]["room_id"] == channel["id"]
    assert found[thread]["kind"] == "thread"
    assert found[thread]["room_id"] == channel["id"]
    # A 支线 is called by the message it hangs under.
    assert "这件事在支线里说" in found[thread]["title"]
    assert nothing not in found


def test_a_private_channel_names_nothing_to_someone_outside_it(client):
    project = new_project(client)
    join_project_team(client, project["id"], "member")
    private = _channel(client, project["id"], "机密", members_only=True)
    open_one = _channel(client, project["id"], "公开")

    assert private["id"] in _refs(client, [private["id"]])
    assert set(_refs(client, [private["id"], open_one["id"]], headers=MEMBER)) == {
        open_one["id"]
    }


def test_a_document_naming_nothing_is_written_and_its_writer_told(client):
    project = new_project(client)
    channel = _channel(client, project["id"], "前端")
    thread = in_thread(client, channel["id"], "owner")
    elsewhere = _channel(client, new_project(client, "Q")["id"], "别的项目")
    task = open_task(client, channel["id"], owner="owner", start=False)["id"]
    doc = f"/documents/{document_of(client, task)}"
    nothing = str(uuid.uuid4())

    written = client.put(
        doc,
        json={
            "content": f"截图在 <#{thread}> 里。旧讨论在 <#{nothing}>，"
            f"另见 <#{elsewhere['id']}>。",
            "expected_version": 0,
        },
        headers=OWNER,
    )

    assert written.status_code == 200, written.text
    answer = written.json()
    assert _warnings_naming(answer, nothing)
    assert _warnings_naming(answer, elsewhere["id"])
    assert not _warnings_naming(answer, thread)
    # Read later, the document still says so.
    assert _warnings_naming(client.get(doc, headers=OWNER).json(), nothing)

    fixed = client.put(
        doc,
        json={"content": f"截图在 <#{thread}> 里。", "expected_version": 1},
        headers=OWNER,
    )

    assert fixed.status_code == 200, fixed.text
    assert not _warnings_naming(fixed.json(), nothing)
    assert not _warnings_naming(client.get(doc, headers=OWNER).json(), nothing)


def test_an_agent_message_naming_nothing_gets_a_line_beside_it(client):
    project = new_project(client)
    room = project["root_topic_id"]
    thread = in_thread(client, room, "owner")
    nothing = str(uuid.uuid4())

    for content in (f"截图在 <#{thread}> 里", f"旧讨论在 <#{nothing}>"):
        sent = client.post(
            f"/topics/{room}/messages",
            json={"request_id": str(uuid.uuid4()), "content": content},
            headers=room_agent_headers(client, room),
        )
        assert sent.status_code == 200, sent.text

    blocks = client.get(f"/topics/{room}/blocks", headers=OWNER).json()["data"]["data"]
    lines = [b["content"] for b in blocks if b["kind"] == "event"]
    assert [line for line in lines if nothing in line]
    assert not [line for line in lines if thread in line]
