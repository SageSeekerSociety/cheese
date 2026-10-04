"""A way of working a project saved: a person confirms it, every new session in
the project gets it with its files, and what it says is the confirmed version."""

import sys
import uuid

import pytest

from app.core import storage as storage_module
from app.core.config import settings
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "user-1"
PERSON = session_auth_headers(OWNER)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    # The storage backend is a module-level singleton fixed at first use.
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path / "files"))
    monkeypatch.setattr(storage_module, "_storage_backend", None)


def _project(client) -> str:
    r = post_project(client, json={"name": f"方法-{uuid.uuid4().hex[:6]}"}, owner=OWNER)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "周报") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


METHOD = {
    "name": "weekly-report",
    "title": "项目周报",
    "description": "把一周的项目进展整理成一页周报",
    "body": "1. 列出本周完成的任务\n2. 数字只写有来源的，每条后面标来源",
    "files": {"scripts/count.py": "print('count tasks')\n"},
}


def _shipped(project_id: str) -> dict[str, str]:
    """What a brand-new executor in this project is handed."""
    return payload_for(
        uuid.UUID(project_id),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=False,
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

    new_body = "1. 列出本周完成的任务\n2. 只统计已合并的 PR"
    edited = client.patch(f"/skills/{skill['id']}", json={"body": new_body})
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
    client.patch(f"/skills/{skill['id']}", json={"body": "发到群里"}, headers=PERSON)

    detail = client.get(f"/skills/{skill['id']}", headers=PERSON).json()["data"]
    assert [r["revision"] for r in detail["revisions"]] == [2, 1]
    assert detail["revisions"][1]["content"]["body"] == METHOD["body"]

    restored = client.post(f"/skills/{skill['id']}/revisions/1/restore", headers=PERSON)
    assert restored.status_code == 200, restored.text
    text = _shipped(project)["skills/weekly-report/SKILL.md"]
    assert "每条后面标来源" in text and "发到群里" not in text
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
            sandbox=False,
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


# --- What a teammate may propose, and what a person decides ------------------


def _proposal(name: str = "weekly-report", **extra) -> dict:
    return {
        **METHOD,
        "name": name,
        "taught": ["先写变坏的指标", "数字后面标来源"],
        "accepted": "用户说就这样",
        **extra,
    }


def test_a_declined_proposal_is_not_proposed_again_but_a_person_may_write_it(client):
    project = _project(client)
    room = _room(client, project)
    skill = client.post(f"/topics/{room}/skills", json=_proposal()).json()["data"]
    declined = client.post(f"/skills/{skill['id']}/decline", headers=PERSON)
    assert declined.status_code == 200, declined.text

    listed = client.get(f"/projects/{project}/skills", headers=PERSON).json()["data"]
    assert listed["data"] == [], "a declined proposal still shows as a method"
    again = client.post(f"/topics/{room}/skills", json=_proposal())
    assert again.status_code == 422, "the teammate proposed what was declined"

    written = client.post(f"/topics/{room}/skills", json=METHOD, headers=PERSON)
    assert written.status_code == 200, written.text
    assert "skills/weekly-report/SKILL.md" in _shipped(project)


def test_a_teammate_waits_for_one_proposal_before_the_next(client):
    project = _project(client)
    room = _room(client, project)
    assert client.post(f"/topics/{room}/skills", json=_proposal()).status_code == 200
    second = client.post(f"/topics/{room}/skills", json=_proposal("grading"))
    assert second.status_code == 422, "two proposals waited in one room"


def test_at_the_limit_a_teammate_stops_proposing_but_a_person_does_not(client):
    from app.domain.project_skill.service import PROPOSAL_LIMIT

    project = _project(client)
    room = _room(client, project)
    for i in range(PROPOSAL_LIMIT):
        made = client.post(
            f"/topics/{room}/skills", json={**METHOD, "name": f"m-{i}"}, headers=PERSON
        )
        assert made.status_code == 200, made.text

    refused = client.post(f"/topics/{room}/skills", json=_proposal("one-more"))
    assert refused.status_code == 422, "a teammate added past the limit"
    by_person = client.post(
        f"/topics/{room}/skills", json={**METHOD, "name": "one-more"}, headers=PERSON
    )
    assert by_person.status_code == 200, "a person was held to the teammate's limit"


def test_saving_a_proposal_removes_the_team_memories_it_absorbed(client):
    project = _project(client)
    room = _room(client, project)
    rule = "---\nname: weekly-order\ndescription: 周报先写坏消息\ntype: feedback\n---\n"
    rule += "周报先写坏消息"
    kept = "---\nname: style\ndescription: 回答要短\ntype: feedback\n---\n回答要短"
    for path, content in (("weekly-order.md", rule), ("style.md", kept)):
        wrote = client.put(
            "/memory/files",
            json={
                "project_id": project,
                "scope": "team",
                "path": path,
                "content": content,
            },
            headers=PERSON,
        )
        assert wrote.status_code == 200, wrote.text
    index = "- [周报顺序](weekly-order.md) — 周报先写坏消息\n"
    index += "- [风格](style.md) — 回答要短\n"
    client.put(
        "/memory/files",
        json={
            "project_id": project,
            "scope": "team",
            "path": "MEMORY.md",
            "content": index,
        },
        headers=PERSON,
    )

    skill = client.post(
        f"/topics/{room}/skills",
        json=_proposal(absorbs=["team/weekly-order.md"]),
    ).json()["data"]
    team = {
        f["path"]: f["content"]
        for f in client.get(
            f"/memory/files?project_id={project}&scope=team", headers=PERSON
        ).json()["data"]["data"]
    }
    assert "weekly-order.md" in team, "a proposal removed memories before it was saved"
    # The person deciding sees which memory goes by its title, not its file.
    assert skill["proposal"]["absorbs"] == [
        {"path": "team/weekly-order.md", "title": "周报顺序"}
    ]

    client.post(f"/skills/{skill['id']}/confirm", headers=PERSON)
    team = {
        f["path"]: f["content"]
        for f in client.get(
            f"/memory/files?project_id={project}&scope=team", headers=PERSON
        ).json()["data"]["data"]
    }
    assert "weekly-order.md" not in team
    assert "weekly-order.md" not in team["MEMORY.md"]
    assert "style.md" in team and "style.md" in team["MEMORY.md"]


def test_a_proposal_cannot_absorb_someones_private_memory(client):
    project = _project(client)
    room = _room(client, project)
    refused = client.post(
        f"/topics/{room}/skills",
        json=_proposal(absorbs=[f"private/{OWNER}/style.md"]),
    )
    assert refused.status_code == 422, "a project method absorbed a personal memory"
