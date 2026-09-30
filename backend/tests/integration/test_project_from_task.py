"""Creating a 2.0 project from a 赛题.

"在赛题里创建项目" only means something if the project remembers which 赛题 it
came from — otherwise it is indistinguishable from one made anywhere else.
"""

from tests.conftest import seed_claim, seed_task_with_protocol
from tests.integration.conftest import post_project

OWNER = "owner"


def _claimed_task(client) -> int:
    task_id = seed_task_with_protocol(client)
    seed_claim(client, task_id, handle=OWNER)
    return task_id


def _create(client, **body):
    return post_project(
        client, json={"name": "赛题项目", "owner_handle": OWNER, **body}
    )


def test_a_project_created_from_a_task_remembers_it(client):
    task_id = _claimed_task(client)
    r = _create(client, external_task_id=task_id)
    assert r.status_code == 200
    assert r.json()["data"]["external_task_id"] == task_id


def test_a_project_created_from_the_rail_belongs_to_no_task(client):
    r = _create(client)
    assert r.status_code == 200
    assert r.json()["data"]["external_task_id"] is None


def test_a_task_lists_the_projects_made_from_it(client):
    task_id, other = _claimed_task(client), _claimed_task(client)
    made = [
        _create(client, external_task_id=task_id).json()["data"]["id"] for _ in range(2)
    ]
    _create(client, external_task_id=other)

    body = client.get(f"/projects/by-task/{task_id}").json()["data"]
    assert body["total"] == 2
    assert {p["id"] for p in body["data"]} == set(made)
    # A 赛题 nobody has started shows nothing, rather than everything.
    assert client.get("/projects/by-task/12345").json()["data"]["total"] == 0


def test_an_unclaimed_task_cannot_seed_a_project(client):
    """领了这道题才能用它建项目：项目带着题目的访问权和资源包。"""
    task_id = seed_task_with_protocol(client)

    r = _create(client, external_task_id=task_id)
    assert r.status_code == 403
    assert client.get(f"/projects/by-task/{task_id}").json()["data"]["total"] == 0
