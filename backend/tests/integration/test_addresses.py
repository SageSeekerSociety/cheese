"""Addresses people can say: `/projects/<slug>/tasks/318`.

The rules, as decided before the code was written:

- every project has a slug, unique across projects; a new one gets a random one;
- its managers may rename it; the old name keeps leading to the project, no other
  project may take it, and the project may take it back;
- a slug is lowercase letters, digits and hyphens, 3 to 32 long, and not a word
  kept back for a page;
- tasks, documents of the project's own and channels are each numbered from 1
  within their project, and the numbers of one kind do not move another's;
- a number leads to the thing, and the thing to its number;
- nobody who may not see a project, or a channel only its members see, learns
  from an address that it exists.
"""

from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    session_auth_headers,
)

OWNER = session_auth_headers("owner")
MEMBER = session_auth_headers("member")
OUTSIDER = session_auth_headers("outsider")


def _channel(client, project: str, title: str, **extra) -> dict:
    made = client.post(
        "/topics", json={"project_id": project, "title": title, **extra}, headers=OWNER
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _document(client, project: str, title: str) -> dict:
    made = client.post(
        f"/projects/{project}/documents", json={"title": title}, headers=OWNER
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _resolve(client, ref: str, headers=OWNER):
    return client.get(f"/addresses/projects/{ref}", headers=headers)


def _rename(client, project: str, slug: str, headers=OWNER):
    return client.put(f"/projects/{project}/slug", json={"slug": slug}, headers=headers)


def test_a_new_project_goes_by_a_slug_that_leads_back_to_it(client):
    project = new_project(client)
    slug = project["slug"]
    assert 3 <= len(slug) <= 32
    found = _resolve(client, slug)
    assert found.status_code == 200, found.text
    assert found.json()["data"] == {"id": project["id"], "slug": slug}
    # Its id is an address too.
    assert _resolve(client, project["id"]).json()["data"]["slug"] == slug
    assert new_project(client)["slug"] != slug


def test_a_renamed_project_keeps_its_old_name_and_nobody_else_can_take_it(client):
    project = new_project(client)
    first = project["slug"]
    assert _rename(client, project["id"], "Course-Helper").status_code == 200
    # The old name still finds it, and says what it is called now.
    assert _resolve(client, first).json()["data"] == {
        "id": project["id"],
        "slug": "course-helper",
    }
    other = new_project(client)
    assert _rename(client, other["id"], first).status_code == 409
    assert _rename(client, other["id"], "course-helper").status_code == 409
    # It may go back to the name it had.
    assert _rename(client, project["id"], first).status_code == 200
    assert _resolve(client, "course-helper").json()["data"]["slug"] == first


def test_a_slug_that_breaks_the_rules_is_refused(client):
    project = new_project(client)
    for bad in ["ab", "x" * 33, "-lead", "trail-", "有中文", "a_b", "new"]:
        assert _rename(client, project["id"], bad).status_code == 422, bad
    assert _resolve(client, project["id"]).json()["data"]["slug"] == project["slug"]


def test_only_the_projects_managers_rename_it(client):
    project = new_project(client)
    join_project_team(client, project["id"], "member")
    assert _rename(client, project["id"], "mine", headers=MEMBER).status_code == 403
    assert _rename(client, project["id"], "mine", headers=OUTSIDER).status_code in (
        403,
        404,
    )
    assert _resolve(client, project["id"]).json()["data"]["slug"] == project["slug"]


def test_each_kind_is_numbered_on_its_own_from_one(client):
    project = new_project(client)
    general = _channel(client, project["id"], "前端")
    first = open_task(client, general["id"], owner="owner", start=False)
    doc = _document(client, project["id"], "竞品对比")
    second = open_task(client, general["id"], owner="owner", start=False)
    assert (first["number"], second["number"]) == (1, 2)
    assert doc["number"] == 1
    # The project's own general channel was its first.
    assert general["number"] == 2

    elsewhere = new_project(client)
    other = _channel(client, elsewhere["id"], "x")
    assert open_task(client, other["id"], owner="owner", start=False)["number"] == 1


def test_a_number_leads_to_the_thing_and_the_thing_to_its_number(client):
    project = new_project(client)
    _rename(client, project["id"], "helper")
    room = _channel(client, project["id"], "前端")
    task = open_task(client, room["id"], owner="owner", start=False)
    doc = _document(client, project["id"], "竞品对比")

    found = client.get(
        f"/addresses/projects/helper/tasks/{task['number']}", headers=OWNER
    )
    assert found.status_code == 200, found.text
    assert found.json()["data"]["id"] == task["id"]
    assert found.json()["data"]["room_id"] == room["id"]
    found = client.get(
        f"/addresses/projects/helper/docs/{doc['number']}", headers=OWNER
    ).json()["data"]
    assert found["id"] == doc["id"]
    found = client.get(
        f"/addresses/projects/helper/channels/{room['number']}", headers=OWNER
    ).json()["data"]
    assert found["id"] == room["id"]
    assert (
        client.get("/addresses/projects/helper/tasks/999", headers=OWNER).status_code
        == 404
    )

    back = client.get(f"/addresses/of/tasks/{task['id']}", headers=OWNER).json()
    assert (back["data"]["slug"], back["data"]["number"]) == ("helper", task["number"])
    back = client.get(f"/addresses/of/docs/{doc['id']}", headers=OWNER).json()
    assert (back["data"]["slug"], back["data"]["number"]) == ("helper", doc["number"])


def test_an_address_tells_nothing_to_someone_who_may_not_see_it(client):
    project = new_project(client)
    join_project_team(client, project["id"], "member")
    _rename(client, project["id"], "secret-plans")
    hidden = _channel(client, project["id"], "私密", members_only=True)
    task = open_task(client, hidden["id"], owner="owner", start=False)

    assert _resolve(client, "secret-plans", headers=OUTSIDER).status_code == 404
    assert _resolve(client, "no-such-project").status_code == 404
    for path in [
        f"/addresses/projects/secret-plans/channels/{hidden['number']}",
        f"/addresses/projects/secret-plans/tasks/{task['number']}",
        f"/addresses/of/tasks/{task['id']}",
    ]:
        assert client.get(path, headers=MEMBER).status_code == 404, path
        assert client.get(path, headers=OWNER).status_code == 200, path
