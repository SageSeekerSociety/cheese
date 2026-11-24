"""A project keeps commands under a name, for everyone working on it.

The rule: a command fixed under a name is listed for everyone in the project,
fixing another command under the same name replaces it, taking it off removes
it, and someone outside the project neither sees nor fixes one.
"""

from tests.integration.conftest import post_project, session_auth_headers

OWNER = session_auth_headers("user-1")


def _project(client) -> str:
    response = post_project(client, json={"name": "OS lab"}, owner="user-1")
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _listed(client, project: str, headers=OWNER) -> list[tuple[str, str]]:
    response = client.get(f"/projects/{project}/commands", headers=headers)
    assert response.status_code == 200, response.text
    return [(c["name"], c["command"]) for c in response.json()["data"]["items"]]


def _fix(client, project: str, name: str, command: str, headers=OWNER):
    return client.put(
        f"/projects/{project}/commands",
        json={"name": name, "command": command},
        headers=headers,
    )


def test_a_named_command_is_listed_and_a_second_one_under_its_name_replaces_it(client):
    project = _project(client)

    assert _fix(client, project, "评分", "make grade").status_code == 200
    assert _fix(client, project, "构建", "make -j4").status_code == 200
    assert _fix(client, project, "评分", "make grade LAB=fork").status_code == 200

    assert _listed(client, project) == [
        ("构建", "make -j4"),
        ("评分", "make grade LAB=fork"),
    ]


def test_taking_one_off_removes_it(client):
    project = _project(client)
    fixed = _fix(client, project, "评分", "make grade").json()["data"]

    response = client.delete(
        f"/projects/{project}/commands/{fixed['id']}", headers=OWNER
    )

    assert response.status_code == 200, response.text
    assert _listed(client, project) == []


def test_someone_outside_the_project_neither_sees_nor_fixes_one(client):
    project = _project(client)
    _fix(client, project, "评分", "make grade")
    stranger = session_auth_headers("stranger")

    assert client.get(
        f"/projects/{project}/commands", headers=stranger
    ).status_code in (403, 404)
    assert _fix(client, project, "评分", "rm -rf ~", headers=stranger).status_code in (
        403,
        404,
    )
    assert _listed(client, project) == [("评分", "make grade")]
