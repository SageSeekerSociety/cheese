"""A refusal raised in the domain layer reaches the reader in the reader's language.

The Chinese sentence stays in ``error.message``, unchanged, for agents and the
CLI; ``error.i18n`` names the catalog sentence, and an English screen renders it
from the English catalog. One refusal per area, each reached through the route a
person or an agent actually calls — including the routes that catch a domain
error and raise it again, which must keep its key.
"""

import httpx

from app.core.config import settings
from app.core.sentences import render
from app.domain.living_doc import collab
from tests.conftest import seed_user
from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.support.living_doc import document_of


def _doc(client, task) -> str:
    """The task's document, as its routes address it."""
    return f"/documents/{document_of(client, task)}"


def _english(error: dict) -> str | None:
    return render(error["i18n"], "en")


def _room(client, owner: str = "owner") -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner=owner)
    assert project.status_code == 200, project.text
    project_id = project.json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project_id, "title": "话题"})
    assert room.status_code == 200, room.text
    return project_id, room.json()["data"]["id"]


_MEMORY = """---
name: answer-first
description: 回答先给结论
type: feedback
---

有结论就先说结论，理由跟在后面。
"""


def test_a_new_memory_under_a_taken_name_is_refused_in_english(client):
    alice = session_auth_headers("alice")
    project = post_project(client, json={"name": "记忆"}, headers=alice)
    body = {
        "project_id": project.json()["data"]["id"],
        "scope": "team",
        "path": "answer-first.md",
        "content": _MEMORY,
    }
    assert client.put("/memory/files", json=body, headers=alice).status_code == 200

    r = client.put("/memory/files", json=body, headers=alice)

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["message"] == (
        "answer-first.md 的这一版已经改不动了：它已经存在（现在是第 1 版）。"
        "重读一次，把改动并进去，再写。"
    )
    assert error["i18n"]["key"] == "memoryFileVersionConflict"
    assert _english(error) == (
        "This version of answer-first.md can no longer be changed: it already "
        "exists (now at version 1). Read it again, merge your changes in, then write."
    )


def test_cloning_a_room_that_never_ran_is_refused_in_english(client):
    project_id, source = _room(client)
    target = client.post("/topics", json={"project_id": project_id, "title": "目标"})

    r = client.post(
        f"/topics/{target.json()['data']['id']}/clone-from",
        json={"source_topic_id": source},
    )

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["i18n"] == {"key": "topicCloneNeverRan", "params": {}}
    assert _english(error) == (
        "The source channel hasn't run yet (there's no session to clone)"
    )


def test_a_routine_with_an_unknown_frequency_is_refused_in_english(client):
    _, room = _room(client)

    r = client.post(
        f"/topics/{room}/routines",
        json={
            "title": "每年一次",
            "instructions": "整理一年的进展",
            "trigger": "schedule",
            "spec": {"freq": "yearly", "time": "09:00"},
            "timezone": "Asia/Shanghai",
        },
        headers=session_auth_headers("owner"),
    )

    assert r.status_code in (400, 422), r.text
    error = r.json()["error"]
    assert error["i18n"] == {"key": "routineFrequencyInvalid", "params": {}}
    assert _english(error) == (
        "The frequency can only be one of hourly, daily, weekly, monthly"
    )


def test_two_plan_windows_of_one_length_are_refused_in_english(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["plan-admin"])
    admin = {"Authorization": f"Bearer {seed_user(client, 'plan-admin')}"}

    r = client.put(
        "/admin/plans/free",
        json={"windows": [{"hours": 5, "credits": 1}, {"hours": 5, "credits": 2}]},
        headers=admin,
    )

    assert r.status_code == 400, r.text
    error = r.json()["error"]
    assert error["message"] == "同样长度的时间窗口只能有一个"
    assert error["i18n"] == {"key": "planWindowDuplicate", "params": {}}
    assert _english(error) == "There can be only one time window of each length"


def test_a_branch_protection_switch_that_is_not_a_boolean_says_which(client):
    client.headers.update(session_auth_headers("alice"))
    project_id = post_project(client, json={"name": "P"}).json()["data"]["id"]

    r = client.put(f"/projects/{project_id}/branch-protection", json={"strict": "yes"})

    assert r.status_code in (400, 422), r.text
    error = r.json()["error"]
    assert error["message"] == "strict 必须是 true/false"
    assert error["i18n"] == {
        "key": "protectionNotBoolean",
        "params": {"field": "strict"},
    }
    assert _english(error) == "strict must be true/false"


def test_a_document_write_with_the_service_down_says_so_in_english(client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(collab, "transport", httpx.MockTransport(down))
    _, room = _room(client)
    task = open_task(client, room, owner="owner", start=False)["id"]

    r = client.put(
        _doc(client, task),
        json={"content": "写不进去", "expected_version": 0},
        headers=session_auth_headers("owner"),
    )

    assert r.status_code == 503, r.text
    error = r.json()["error"]
    assert error["i18n"] == {"key": "collabUnreachable", "params": {}}
    assert _english(error) == (
        "The document collaboration service can't be reached right now. Try again later"
    )


def test_a_docs_page_name_that_is_not_one_is_refused_in_english(client):
    _, room = _room(client)

    r = client.post(
        "/docs/agent/read",
        json={"topic": room, "page": "../../etc/passwd"},
        headers=session_auth_headers("owner"),
    )

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["i18n"]["key"] == "docsNotAPage"
    assert _english(error) == (
        "Not a docs page: ../../etc/passwd "
        "(write the page name, such as accept or dev/turn)"
    )


def test_an_unknown_room_member_is_refused_in_english(client):
    _, room = _room(client)

    r = client.delete(
        f"/topics/{room}/members/nobody",
        headers=session_auth_headers("owner"),
    )

    assert r.status_code == 404, r.text
    error = r.json()["error"]
    assert error["i18n"] == {"key": "topicMemberNotFound", "params": {}}
    assert _english(error) == "Member not found"
