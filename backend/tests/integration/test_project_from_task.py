"""Creating a 2.0 project from a 赛题.

"在赛题里创建项目" only means something if the project remembers which 赛题 it
came from — otherwise it is indistinguishable from one made anywhere else.
"""


def _create(client, **body):
    return client.post("/api/projects", json={"name": "赛题项目", **body})


def test_a_project_created_from_a_task_remembers_it(client):
    r = _create(client, external_task_id=971)
    assert r.status_code == 200
    assert r.json()["data"]["external_task_id"] == 971


def test_a_project_created_from_the_rail_belongs_to_no_task(client):
    r = _create(client)
    assert r.status_code == 200
    assert r.json()["data"]["external_task_id"] is None


def test_a_task_lists_the_projects_made_from_it(client):
    made = [_create(client, external_task_id=42).json()["data"]["id"] for _ in range(2)]
    _create(client, external_task_id=99)

    body = client.get("/api/projects/by-task/42").json()["data"]
    assert body["total"] == 2
    assert {p["id"] for p in body["data"]} == set(made)
    # A 赛题 nobody has started shows nothing, rather than everything.
    assert client.get("/api/projects/by-task/12345").json()["data"]["total"] == 0
