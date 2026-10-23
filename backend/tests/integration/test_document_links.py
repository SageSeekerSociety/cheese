"""Links between a project's documents (#3827).

The rules, as a person would state them:

- a document can reference another with ``<#id>``, like a channel or a task:
  whoever reads the reference learns the document's title, and the reference
  is not reported as naming nothing;
- a document lists what references it: the documents whose text does and the
  conversations where a message does, a conversation once with how many of its
  messages do;
- a reference that is removed from the text no longer counts;
- nobody learns of a document they may not read: a reference to it says only
  that it is closed to them, and a document or conversation they may not read
  is not among what references something they can.
"""

from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    post_message,
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


def _document(client, project: str, title: str, content: str = "") -> dict:
    made = client.post(
        f"/projects/{project}/documents",
        json={"title": title, "content": content},
        headers=OWNER,
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _links(client, document: str, headers=OWNER) -> dict:
    found = client.get(f"/documents/{document}/links", headers=headers)
    assert found.status_code == 200, found.text
    return found.json()["data"]


def _refs(client, ids: list[str], headers=OWNER) -> dict:
    found = client.get(
        "/addresses/refs", params={"ids": ",".join(ids)}, headers=headers
    )
    assert found.status_code == 200, found.text
    return found.json()["data"]


def _linking_documents(links: dict) -> list[str]:
    return [r["title"] for r in links["linked_from"] if r["kind"] == "document"]


def test_a_reference_names_the_document_it_points_at(client):
    project = new_project(client)
    dataset = _document(client, project["id"], "数据集说明")

    found = _refs(client, [dataset["id"]])

    assert found[dataset["id"]]["kind"] == "document"
    assert found[dataset["id"]]["title"] == "数据集说明"
    assert found[dataset["id"]]["project_id"] == project["id"]


def test_a_document_referencing_another_is_listed_on_it_and_not_warned(client):
    project = new_project(client)
    dataset = _document(client, project["id"], "数据集说明")
    spec = _document(client, project["id"], "标注规范")

    written = client.put(
        f"/documents/{spec['id']}",
        json={
            "content": f"适用于 <#{dataset['id']}> 里的数据。",
            "expected_version": 0,
        },
        headers=OWNER,
    )

    assert written.status_code == 200, written.text
    assert not [w for w in written.json().get("warnings") or [] if dataset["id"] in w]
    assert _linking_documents(_links(client, dataset["id"])) == ["标注规范"]
    assert [d["title"] for d in _links(client, spec["id"])["links_to"]] == [
        "数据集说明"
    ]


def test_a_document_made_with_a_reference_is_listed_at_once(client):
    project = new_project(client)
    dataset = _document(client, project["id"], "数据集说明")

    _document(client, project["id"], "开题报告", f"数据见 <#{dataset['id']}>。")

    assert _linking_documents(_links(client, dataset["id"])) == ["开题报告"]


def test_a_removed_reference_no_longer_counts(client):
    project = new_project(client)
    dataset = _document(client, project["id"], "数据集说明")
    spec = _document(client, project["id"], "标注规范", f"见 <#{dataset['id']}>。")

    changed = client.put(
        f"/documents/{spec['id']}",
        json={"content": "数据另行说明。", "expected_version": 1},
        headers=OWNER,
    )

    assert changed.status_code == 200, changed.text
    assert _linking_documents(_links(client, dataset["id"])) == []


def test_messages_referencing_a_document_count_once_per_conversation(client):
    project = new_project(client)
    dataset = _document(client, project["id"], "数据集说明")
    channel = _channel(client, project["id"], "标注讨论")
    for text in ("先看 <#{d}>", "第二批按 <#{d}> 清洗完了"):
        post_message(
            client, channel["id"], "owner", {"content": text.format(d=dataset["id"])}
        )

    [talk] = [r for r in _links(client, dataset["id"])["linked_from"]]

    assert talk["kind"] == "conversation"
    assert talk["conversation"]["kind"] == "room"
    assert talk["conversation"]["title"] == "标注讨论"
    assert talk["count"] == 2
    assert "清洗完了" in talk["excerpt"], "the latest message is the one shown"


def test_nobody_learns_of_a_document_they_may_not_read(client):
    project = new_project(client)
    join_project_team(client, project["id"], "member")
    dataset = _document(client, project["id"], "数据集说明")
    private = _channel(client, project["id"], "合作洽谈", members_only=True)
    task = open_task(client, private["id"], "谈合同", owner="owner")
    hidden = document_of(client, task["id"], headers=OWNER)
    written = client.put(
        f"/documents/{hidden}",
        json={"content": f"合同附件依据 <#{dataset['id']}>。", "expected_version": 0},
        headers=OWNER,
    )
    assert written.status_code == 200, written.text
    post_message(
        client, private["id"], "owner", {"content": f"对方要 <#{dataset['id']}>"}
    )

    seen_by_owner = _links(client, dataset["id"])
    seen_by_member = _links(client, dataset["id"], headers=MEMBER)

    assert len(seen_by_owner["linked_from"]) == 2
    assert seen_by_member["linked_from"] == []
    assert _refs(client, [hidden], headers=MEMBER)[hidden] == {
        "kind": "unreadable",
        "title": "",
        "room_id": "",
        "project_id": project["id"],
    }
