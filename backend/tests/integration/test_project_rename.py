"""Renaming a project, as decided before the code was written:

- whoever may edit the project's settings renames it, and every list of
  projects shows the new name;
- a name is held to what a new project's name is held to: not empty, at most
  200 characters;
- the short name in its addresses stays what it was, so links keep working;
- a member who may not edit the settings cannot rename it.
"""

from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

OWNER = session_auth_headers("owner")
MEMBER = session_auth_headers("member")


def _rename(client, project: str, name: str, headers=OWNER):
    return client.put(f"/projects/{project}/name", json={"name": name}, headers=headers)


def _read(client, project: str) -> dict:
    return client.get(f"/projects/{project}", headers=OWNER).json()["data"]


def test_a_renamed_project_is_listed_by_its_new_name(client):
    project = new_project(client, name="数据结构")["id"]
    renamed = _rename(client, project, "数据结构课程设计")
    assert renamed.status_code == 200, renamed.text
    assert _read(client, project)["name"] == "数据结构课程设计"
    listed = client.get("/projects", headers=OWNER).json()["data"]["data"]
    assert next(p for p in listed if p["id"] == project)["name"] == "数据结构课程设计"


def test_a_rename_leaves_the_short_name_and_its_links_alone(client):
    made = new_project(client, name="Lab 1")
    slug = made["slug"]
    assert _rename(client, made["id"], "Compiler Lab").status_code == 200
    assert _read(client, made["id"])["slug"] == slug
    found = client.get(f"/addresses/projects/{slug}", headers=OWNER)
    assert found.json()["data"] == {"id": made["id"], "slug": slug}


def test_a_name_a_new_project_could_not_have_is_refused(client):
    project = new_project(client, name="P")["id"]
    assert _rename(client, project, "").status_code == 400
    assert _rename(client, project, "x" * 201).status_code == 400
    assert _read(client, project)["name"] == "P"


def test_a_member_who_may_not_edit_settings_cannot_rename_it(client):
    project = new_project(client, name="P")["id"]
    join_project_team(client, project, "member")
    refused = _rename(client, project, "Mine", headers=MEMBER)
    assert refused.status_code == 403, refused.text
    assert _read(client, project)["name"] == "P"
