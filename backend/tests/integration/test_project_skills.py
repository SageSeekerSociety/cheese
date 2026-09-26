"""A way of working a project saved: a person confirms it, every new session in
the project gets it with its files, and what it says is the confirmed version."""

import sys
import uuid

import pytest

from app.core.config import settings
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "user-1"
PERSON = session_auth_headers(OWNER)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))


def _project(client) -> str:
    r = post_project(
        client, json={"name": f"方法-{uuid.uuid4().hex[:6]}", "owner_handle": OWNER}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "周报") -> str:
    r = client.post(
        "/topics", json={"project_id": project_id, "title": title, "created_by": OWNER}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


METHOD = {
    "name": "weekly-report",
    "title": "项目周报",
    "description": "把一周的项目进展整理成一页周报",
    "inputs": "本周的时间范围；要覆盖的房间",
    "steps": "1. 列出本周完成的任务\n2. 数字只写有来源的，每条后面标来源",
    "outputs": "一页 markdown，放在 周报/ 下",
    "files": {"scripts/count.py": "print('count tasks')\n"},
}


def _shipped(project_id: str) -> dict[str, str]:
    """What a brand-new executor in this project is handed."""
    return payload_for(
        uuid.UUID(project_id),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
    )["skills"]


def test_an_ai_draft_ships_nothing_until_a_person_confirms(client):
    project = _project(client)
    room = _room(client, project)
    drafted = client.post(f"/topics/{room}/skills", json=METHOD)
    assert drafted.status_code == 200, drafted.text
    skill = drafted.json()["data"]
    assert skill["state"] == "draft"
    assert "skills/weekly-report/SKILL.md" not in _shipped(project)

    refused = client.post(f"/skills/{skill['id']}/confirm")
    assert refused.status_code == 403, "an AI teammate confirmed its own draft"

    confirmed = client.post(f"/skills/{skill['id']}/confirm", headers=PERSON)
    assert confirmed.status_code == 200, confirmed.text
    shipped = _shipped(project)
    text = shipped["skills/weekly-report/SKILL.md"]
    assert "name: weekly-report" in text
    assert "每条后面标来源" in text
    assert shipped["skills/weekly-report/scripts/count.py"] == "print('count tasks')\n"
    assert "skills/documents/SKILL.md" in shipped, (
        "the platform's own skills went missing"
    )


def test_an_edit_waits_for_confirmation_and_then_the_new_rule_ships(client):
    project = _project(client)
    room = _room(client, project)
    skill = client.post(f"/topics/{room}/skills", json=METHOD, headers=PERSON).json()[
        "data"
    ]
    assert skill["state"] == "active"

    new_steps = "1. 列出本周完成的任务\n2. 只统计已合并的 PR"
    edited = client.patch(f"/skills/{skill['id']}", json={"steps": new_steps})
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["state"] == "draft"
    still = _shipped(project)["skills/weekly-report/SKILL.md"]
    assert "每条后面标来源" in still and "只统计已合并的 PR" not in still

    client.post(f"/skills/{skill['id']}/confirm", headers=PERSON)
    now = _shipped(project)["skills/weekly-report/SKILL.md"]
    assert "只统计已合并的 PR" in now and "每条后面标来源" not in now


def test_an_earlier_version_can_be_read_and_restored(client):
    project = _project(client)
    room = _room(client, project)
    skill = client.post(f"/topics/{room}/skills", json=METHOD, headers=PERSON).json()[
        "data"
    ]
    client.patch(f"/skills/{skill['id']}", json={"outputs": "发到群里"}, headers=PERSON)

    detail = client.get(f"/skills/{skill['id']}", headers=PERSON).json()["data"]
    assert [r["revision"] for r in detail["revisions"]] == [2, 1]
    assert detail["revisions"][1]["content"]["outputs"] == METHOD["outputs"]

    restored = client.post(f"/skills/{skill['id']}/revisions/1/restore", headers=PERSON)
    assert restored.status_code == 200, restored.text
    text = _shipped(project)["skills/weekly-report/SKILL.md"]
    assert METHOD["outputs"] in text and "发到群里" not in text
    detail = client.get(f"/skills/{skill['id']}", headers=PERSON).json()["data"]
    assert [r["revision"] for r in detail["revisions"]] == [3, 2, 1]


def test_a_deleted_skill_leaves_a_machine_that_had_it(client, tmp_path, monkeypatch):
    project = _project(client)
    room = _room(client, project)
    skill = client.post(f"/topics/{room}/skills", json=METHOD, headers=PERSON).json()[
        "data"
    ]
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    machine = tmp_path / "machine"

    def prepare():
        payload = payload_for(
            uuid.UUID(project),
            uuid.uuid4(),
            {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        )
        with bootstrap.prepared(payload, machine) as (home, _c, _s, _e):
            return home / ".claude" / "skills"

    skills = prepare()
    assert (skills / "weekly-report" / "scripts" / "count.py").is_file()

    assert client.delete(f"/skills/{skill['id']}", headers=PERSON).status_code == 200
    skills = prepare()
    assert not (skills / "weekly-report").exists(), "a deleted skill lingered"
    assert (skills / "documents" / "SKILL.md").is_file()


def test_a_skill_is_a_project_thing_and_names_are_checked(client):
    project = _project(client)
    room = _room(client, project)
    other = _project(client)
    client.post(f"/topics/{room}/skills", json=METHOD, headers=PERSON)
    assert "skills/weekly-report/SKILL.md" not in _shipped(other)

    for bad in (
        {**METHOD, "name": "documents"},
        {**METHOD, "name": "Weekly Report"},
        {**METHOD, "name": "ok-name", "files": {"../escape.py": "x"}},
        {**METHOD, "name": "ok-name2", "files": {"logo.png": "x"}},
    ):
        r = client.post(f"/topics/{room}/skills", json=bad, headers=PERSON)
        assert r.status_code in (400, 422), (bad["name"], r.text)

    stranger = session_auth_headers("user-2")
    assert client.get(f"/projects/{project}/skills", headers=stranger).status_code in (
        401,
        403,
    )
