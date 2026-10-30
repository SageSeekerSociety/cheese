"""Local MCP servers — a command each — added in project settings, beside the
repository's `.mcp.json`.

The rules, stated before they were written:

- a command added in settings is one more local server of the project: listed
  with who added it, its `${VAR}`s filled from project secrets like a
  committed one's, and handed only the secrets its own definition names;
- a name any source of the project already uses cannot be added, and one the
  repository takes later is the repository's: the added one stops counting;
- a command that cannot be parsed, or an environment variable that is not a
  name, is refused;
- on a team's computer its administrators are asked like for any other command,
  and told it was added in settings, and by whom;
- changing or removing it is done in settings, by whoever configures the
  project's AI teammates.
"""

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import join_project_team, session_auth_headers
from tests.integration.test_local_mcp_servers import _computer, _waiting
from tests.integration.test_remote_mcp import (
    _commit_servers,
    _project,
    _servers,
    _topic,
)
from tests.integration.test_remote_mcp import _local_upstream as _local_upstream
from tests.integration.test_remote_mcp import upstream as upstream

PG = {
    "name": "pg",
    "kind": "command",
    "command": "npx -y @modelcontextprotocol/server-postgres ${DATABASE_URL}",
    "env": {"PGSSLMODE": "require", "PGPASSWORD": "${PG_PASSWORD}"},
}


def _add(client, pid, body: dict, handle: str = "alice"):
    return client.post(
        f"/projects/{pid}/mcp/servers", json=body, headers=session_auth_headers(handle)
    )


def _fill(client, pid, name: str, value: str) -> None:
    put = client.put(
        f"/projects/{pid}/mcp/secrets/{name}",
        json={"value": value},
        headers=session_auth_headers("alice"),
    )
    assert put.status_code == 200, put.text


@pytest.fixture
def room(client, upstream):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    return (
        pid,
        tid,
        {
            "Authorization": f"Bearer {mint_scoped_token(project_id=str(pid), topic_id=tid)}"
        },
    )


def _start(client, tid, session, name="pg", device_id=None) -> dict:
    response = client.post(
        f"/topics/{tid}/mcp/{name}/start",
        json={"device_id": device_id},
        headers=session,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_command_added_in_settings_is_one_more_local_server(client, room):
    pid, tid, session = room

    added = _add(client, pid, PG)

    assert added.status_code == 200, added.text
    pg = _servers(client, pid)["pg"]
    assert pg["transport"] == "stdio"
    assert pg["command"] == (
        "npx -y @modelcontextprotocol/server-postgres '${DATABASE_URL}'"
    )
    assert pg["added"]["by"] == "alice"
    assert pg["added"]["env"] == PG["env"]
    assert pg["status"] == "missing_values"
    assert [v["name"] for v in pg["variables"]] == ["DATABASE_URL", "PG_PASSWORD"]

    _fill(client, pid, "DATABASE_URL", "postgres://db")
    _fill(client, pid, "PG_PASSWORD", "hunter2")
    _fill(client, pid, "SEARCH_KEY", "search-secret")

    assert _servers(client, pid)["pg"]["status"] == "ready"
    assert _start(client, tid, session)["values"] == {
        "DATABASE_URL": "postgres://db",
        "PG_PASSWORD": "hunter2",
    }


def test_a_name_any_source_uses_cannot_be_added(client, room):
    pid, _, _ = room

    for taken in ("tracker", "local"):
        refused = _add(client, pid, {**PG, "name": taken})
        assert refused.status_code == 422, refused.text
    assert _add(client, pid, PG).status_code == 200
    remote = _add(client, pid, {"name": "pg", "url": "https://mcp.example.test/mcp"})
    assert remote.status_code == 422, remote.text
    assert _servers(client, pid)["pg"]["transport"] == "stdio"


def test_the_repository_taking_the_name_later_shadows_the_added_command(
    client, room, upstream
):
    pid, tid, session = room
    assert _add(client, pid, PG).status_code == 200
    _fill(client, pid, "PG_PASSWORD", "hunter2")

    _commit_servers(pid, {"pg": {"command": "uvx", "args": ["pg-mcp"]}})

    listed = client.get(
        f"/projects/{pid}/mcp/servers", headers=session_auth_headers("alice")
    ).json()["data"]["servers"]
    pg = sorted(
        (r["added"] is not None, r["shadowed"], r["command"])
        for r in listed
        if r["name"] == "pg"
    )
    assert pg == [
        (False, False, "uvx pg-mcp"),
        (True, True, "npx -y @modelcontextprotocol/server-postgres '${DATABASE_URL}'"),
    ]
    assert _start(client, tid, session)["values"] == {}, (
        "the repository's pg names no secret"
    )


@pytest.mark.parametrize(
    "body",
    [
        {**PG, "command": "   "},
        {**PG, "command": "npx 'unclosed"},
        {**PG, "env": {"1BAD": "x"}},
        {**PG, "env": {"HAS SPACE": "x"}},
    ],
)
def test_a_command_that_cannot_run_as_written_is_refused(client, room, body):
    pid, _, _ = room

    refused = _add(client, pid, body)

    assert refused.status_code == 422, refused.text
    assert "pg" not in _servers(client, pid)


def test_the_team_admin_is_told_the_command_was_added_in_settings(client, room):
    pid, tid, session = room
    assert _add(client, pid, PG).status_code == 200
    device = _computer(client, pid, "dana")

    assert _start(client, tid, session, device_id=device)["allowed"] == []
    reported = client.post(
        f"/topics/{tid}/mcp/pg/run",
        json={
            "state": "awaiting",
            "device_id": device,
            "fingerprint": "a" * 64,
            "command": "npx -y @modelcontextprotocol/server-postgres '${DATABASE_URL}'",
        },
        headers=session,
    )
    assert reported.status_code == 200, reported.text

    assert _waiting(client, "dana") == []
    [asked] = _waiting(client, "alice")
    assert asked["server"] == "pg"
    assert asked["source"] == "settings"
    assert asked["author"]["handle"] == "alice"


def test_changing_and_removing_a_command(client, room):
    pid, tid, session = room
    assert _add(client, pid, PG).status_code == 200
    alice = session_auth_headers("alice")

    changed = client.put(
        f"/projects/{pid}/mcp/servers/pg",
        json={"name": "pg", "kind": "command", "command": "uvx pg-mcp", "env": {}},
        headers=alice,
    )
    assert changed.status_code == 200, changed.text
    pg = _servers(client, pid)["pg"]
    assert (pg["command"], pg["variables"]) == ("uvx pg-mcp", [])

    removed = client.delete(f"/projects/{pid}/mcp/servers/pg", headers=alice)
    assert removed.status_code == 200, removed.text
    assert "pg" not in _servers(client, pid)
    assert _start(client, tid, session)["values"] == {}


def test_only_who_configures_the_ai_teammates_adds_a_command(client, room):
    pid, _, _ = room
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

    refused = _add(client, pid, PG, handle="bob")

    assert refused.status_code == 403, refused.text
    assert "pg" not in _servers(client, pid)
