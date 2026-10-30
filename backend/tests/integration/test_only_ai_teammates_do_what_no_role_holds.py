"""What only an AI teammate does in a conversation is a permission no person holds.

The ``cheese`` CLI leaves a note for its own session in another thread, mints
the conversation's webhook credential, and puts the machine's status into 现场.
Each is a permission of ``project_permissions.AGENT_ONLY``: the room's own AI
teammate holds it, and nobody's role does — not the owner's, and not a role the
owner has granted everything the role table offers.

The routes are read off the app (`write_access.write_routes`), not listed here:
a route that needs such a permission tomorrow is checked by the same cases.
Which routes those are is pinned in
tests/unit/test_write_routes_declare_their_callers.py.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.api.write_access import write_routes
from app.core.sandbox_auth import mint_scoped_token
from app.domain.project_permissions import AGENT_ONLY
from app.main import app
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_project_reads_need_membership import off_the_street
from tests.integration.test_who_may_act_on_a_project import Project, seat_everyone

_AGENT_ONLY_NAMES = {p.value for p in AGENT_ONLY}


def _agents_only():
    declared = {f"can {p}" for p in AGENT_ONLY}
    routes = [r for r in write_routes(app) if r.declared and r.declared[0] in declared]
    assert routes
    return routes


def _url(path: str, *, topic: str) -> str:
    url = path.replace("{topic_id}", topic)
    assert not re.search(r"\{[^}]+\}", url), f"no sample for a parameter of {path}"
    return url


def _room(client: TestClient, project_id: str, title: str, by: str = "alice") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _agent_of(client: TestClient, project_id: str, room: str) -> dict[str, str]:
    token = mint_scoped_token(
        project_id=project_id, topic_id=room, agent_handle=room_agent_seat(client, room)
    )
    return {"Authorization": f"Bearer {token}"}


def _grant_admins_everything(p: Project) -> None:
    """The owner gives the admin role every permission the role table lists."""
    listed = p.owner_get(f"{p.base}/roles")["roles"]
    everything = sorted({name for r in listed for name in r["permissions"]})
    granted = p.as_owner(
        "PATCH", f"{p.base}/roles/admin", json={"permissions": everything}
    )
    assert granted.status_code == 200, granted.text


def test_no_role_holds_or_is_granted_what_only_ai_teammates_do(client):
    p = seat_everyone(client)

    for name in sorted(_AGENT_ONLY_NAMES):
        refused = p.as_owner(
            "PATCH", f"{p.base}/roles/member", json={"permissions": [name]}
        )
        assert refused.status_code == 422, (name, refused.text)

    _grant_admins_everything(p)
    roles = p.owner_get(f"{p.base}/roles")["roles"]
    assert all(not _AGENT_ONLY_NAMES & set(r["permissions"]) for r in roles), roles
    for seat in ("owner", "admin"):
        mine = client.get(p.base, headers=p.headers[seat]).json()["data"]
        assert mine["my_permissions"], seat
        assert not _AGENT_ONLY_NAMES & set(mine["my_permissions"]), seat


def test_people_are_refused_and_the_room_s_ai_teammate_is_let_through(client):
    p = seat_everyone(client)
    _grant_admins_everything(p)
    room = p.root
    beside = _agent_of(client, p.id, _room(client, p.id, "隔壁频道"))
    other = post_project(client, json={"name": "别的项目"}, owner="bob")
    assert other.status_code == 200, other.text
    other_id = other.json()["data"]["id"]
    elsewhere = _agent_of(client, other_id, _room(client, other_id, "别处", by="bob"))

    for route in _agents_only():
        url = _url(route.path, topic=room)
        refused = {
            "the owner, who holds every permission a person can": (
                p.headers["owner"],
                403,
            ),
            "an admin whose role was granted everything": (p.headers["admin"], 403),
            "a member": (p.headers["member"], 403),
            "the AI teammate of another channel in this project": (beside, 403),
            "the AI teammate of another project's channel": (elsewhere, 403),
        }
        for who, (headers, status) in refused.items():
            r = client.request(route.method, url, json={}, headers=headers)
            assert r.status_code == status, (route.path, who, r.text)
        with off_the_street(client):
            r = client.request(route.method, url, json={})
            assert r.status_code == 401, (route.path, "nobody signed in", r.text)

        own = client.request(
            route.method, url, json={}, headers=room_agent_headers(client, room)
        )
        assert own.status_code not in (401, 403, 404), (route.path, own.text)
        # The development override the test client carries opens them too.
        dev = client.request(route.method, url, json={})
        assert dev.status_code not in (401, 403, 404), (route.path, dev.text)
