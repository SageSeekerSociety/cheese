"""Each route declared 芝士-only refuses everyone but 芝士 of its own room or project.

The routes are read off the app (`write_access.write_routes`), not listed here:
a route declared 芝士-only tomorrow is checked by the same cases without anyone
remembering to add it. Which routes those are is pinned separately, in
tests/unit/test_write_routes_declare_their_callers.py.
"""

import re

from app.api.write_access import write_routes
from app.core.sandbox_auth import mint_scoped_token
from app.main import app
from tests.integration.conftest import post_project, session_auth_headers

_NO_CHEESE = {"X-Cheese-Token": ""}


def _project(client, name: str) -> str:
    r = post_project(client, json={"name": name}, owner="user-1")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seat(client, topic_id: str) -> str:
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    return next(m["member_handle"] for m in rows if m["agent"])


def _cheese_only():
    routes = [
        r
        for r in write_routes(app)
        if r.declared and r.declared[0].startswith("cheese_only")
    ]
    assert routes
    return routes


def _url(path: str, *, topic: str, project: str) -> str:
    url = path.replace("{topic_id}", topic).replace("{project_id}", project)
    assert not re.search(r"\{[^}]+\}", url), f"no sample for a parameter of {path}"
    return url


def test_cheese_only_routes_refuse_people_and_other_rooms(client):
    project = _project(client, "这个项目")
    room = _room(client, project, "这个房间")
    other_project = _project(client, "别的项目")
    other_room = _room(client, other_project, "别的房间")
    seat = _seat(client, room)
    next_door = _room(client, project, "隔壁房间")
    beside = mint_scoped_token(
        project_id=project, topic_id=next_door, agent_handle=_seat(client, next_door)
    )
    elsewhere = mint_scoped_token(
        project_id=other_project,
        topic_id=other_room,
        agent_handle=_seat(client, other_room),
    )
    own = mint_scoped_token(project_id=project, topic_id=room, agent_handle=seat)

    for route in _cheese_only():
        url = _url(route.path, topic=room, project=project)
        refused = {
            "nobody": _NO_CHEESE,
            "the project owner's own session": {
                **_NO_CHEESE,
                **session_auth_headers("user-1"),
            },
            "芝士 of another project's room": {"X-Cheese-Token": elsewhere},
        }
        if route.declared[0] == "cheese_only_in_room":
            refused["芝士 of another room in this project"] = {"X-Cheese-Token": beside}
        for who, headers in refused.items():
            r = client.request(route.method, url, json={}, headers=headers)
            assert r.status_code == 401, (route.path, who, r.text)
            assert r.json()["message"] == "invalid sandbox token"
        r = client.request(route.method, url, json={}, headers={"X-Cheese-Token": own})
        assert r.status_code != 401, (route.path, "芝士 of this room", r.text)
