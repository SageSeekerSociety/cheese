"""Remote MCP servers added in project settings, beside the repository's.

The rules, stated before they were written:

- a server added in settings is one more server of the project: listed with who
  added it, and reached by the project's sessions like a committed one;
- a header value it sends is a sealed project secret, never read back;
- a name the repository's `.mcp.json` uses cannot be added, and one the
  repository takes later is the repository's: the added one stops counting;
- a new address forgets what was authorized for the old one, so a key is never
  sent to a host it was not given for;
- only someone who may configure the project's AI teammates adds one.
"""

import json

from app.domain.remote_mcp.models import ProjectMcpSecret
from tests.integration.conftest import join_project_team, session_auth_headers
from tests.integration.test_remote_mcp import (
    _commit_servers,
    _project,
    _rows,
    _servers,
    _session_servers,
)
from tests.integration.test_remote_mcp import _local_upstream as _local_upstream
from tests.integration.test_remote_mcp import upstream as upstream
from tests.support import fake_remote_mcp


def _add(client, pid, body: dict, handle: str = "alice"):
    return client.post(
        f"/projects/{pid}/mcp/servers", json=body, headers=session_auth_headers(handle)
    )


def _keyed(upstream, name: str = "docs") -> dict:
    return {
        "name": name,
        "url": f"{upstream.base}/keyed",
        "auth": "headers",
        "header": "X-Api-Key",
        "value": fake_remote_mcp.API_KEY,
    }


def test_a_server_added_in_settings_is_one_more_server_of_the_project(client, upstream):
    pid = _project(client, upstream)

    added = _add(client, pid, _keyed(upstream))

    assert added.status_code == 200, added.text
    row = _servers(client, pid)["docs"]
    assert row["added"]["by"] == "alice"
    assert row["added"]["header"] == "X-Api-Key"
    assert row["status"] == "ready"
    assert fake_remote_mcp.API_KEY not in json.dumps(row)
    assert all(
        fake_remote_mcp.API_KEY not in secret.value
        for secret in _rows(client, ProjectMcpSecret)
    )
    assert "docs" in _session_servers(client, pid)


def test_a_name_the_repository_uses_is_the_repositorys(client, upstream):
    pid = _project(client, upstream)

    taken = _add(client, pid, _keyed(upstream, name="tracker"))
    assert taken.status_code == 422, taken.text

    assert _add(client, pid, _keyed(upstream)).status_code == 200
    _commit_servers(pid, {"docs": {"type": "http", "url": f"{upstream.base}/mcp"}})
    listed = client.get(
        f"/projects/{pid}/mcp/servers", headers=session_auth_headers("alice")
    ).json()["data"]["servers"]
    docs = [
        (r["added"] is not None, r["shadowed"]) for r in listed if r["name"] == "docs"
    ]
    assert sorted(docs) == [(False, False), (True, True)]


def test_a_new_address_forgets_the_key_given_for_the_old_one(client, upstream):
    pid = _project(client, upstream)
    assert _add(client, pid, _keyed(upstream)).status_code == 200

    moved = client.put(
        f"/projects/{pid}/mcp/servers/docs",
        json={
            "name": "docs",
            "url": f"{upstream.base}/elsewhere",
            "auth": "headers",
            "header": "X-Api-Key",
        },
        headers=session_auth_headers("alice"),
    )

    assert moved.status_code == 422, moved.text
    assert _servers(client, pid)["docs"]["status"] == "ready"

    moved = client.put(
        f"/projects/{pid}/mcp/servers/docs",
        json={
            "name": "docs",
            "url": f"{upstream.base}/elsewhere",
            "auth": "oauth",
        },
        headers=session_auth_headers("alice"),
    )
    assert moved.status_code == 200, moved.text
    assert not [s for s in _rows(client, ProjectMcpSecret) if s.project_id == pid]
    assert _servers(client, pid)["docs"]["status"] == "disconnected"


def test_removing_a_server_takes_its_key_with_it(client, upstream):
    pid = _project(client, upstream)
    assert _add(client, pid, _keyed(upstream)).status_code == 200

    removed = client.delete(
        f"/projects/{pid}/mcp/servers/docs", headers=session_auth_headers("alice")
    )

    assert removed.status_code == 200, removed.text
    assert "docs" not in _servers(client, pid)
    assert not [s for s in _rows(client, ProjectMcpSecret) if s.project_id == pid]
    assert "docs" not in _session_servers(client, pid)


def test_only_who_configures_the_ai_teammates_adds_a_server(client, upstream):
    pid = _project(client, upstream)
    join_project_team(client, str(pid), "bob")
    roles = client.get(
        f"/projects/{pid}/roles", headers=session_auth_headers("alice")
    ).json()["data"]["roles"]
    member = next(r for r in roles if r["key"] == "member")
    changed = client.patch(
        f"/projects/{pid}/roles/member",
        json={
            "permissions": [g for g in member["permissions"] if g != "configure_agents"]
        },
        headers=session_auth_headers("alice"),
    )
    assert changed.status_code == 200, changed.text

    refused = _add(client, pid, _keyed(upstream), handle="bob")

    assert refused.status_code == 403, refused.text
    assert "docs" not in _servers(client, pid)
