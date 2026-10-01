"""A project's remote MCP servers: connected once, from settings, for the project.

The upstream is a real HTTP server on a local port (`tests/support/
fake_remote_mcp.py`) with its own OAuth authorization server, so what these
tests assert is what crossed the wire: which credential reached the MCP
server, what the authorization server was asked, and what came back to a
room's session.
"""

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.remote_mcp import declared
from app.domain.remote_mcp.models import ProjectMcpConnection, ProjectMcpSecret
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.support import fake_remote_mcp, git_store


@pytest.fixture(scope="module")
def upstream():
    fake, server, thread = fake_remote_mcp.serve()
    yield fake
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _local_upstream(monkeypatch):
    # The fake listens on loopback over plain HTTP, which a deployment refuses.
    monkeypatch.setattr(settings, "remote_mcp_allow_private_hosts", True)
    monkeypatch.setattr(settings, "frontend_url", "http://cheese.test")
    declared._cache.clear()


def _project(client, upstream, *, extra: dict | None = None) -> uuid.UUID:
    response = post_project(
        client, json={"name": "P"}, headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    pid = uuid.UUID(response.json()["data"]["id"])
    servers = {
        "tracker": {"type": "http", "url": f"{upstream.base}/mcp"},
        "search": {
            "type": "http",
            "url": f"{upstream.base}/keyed",
            "headers": {"X-Api-Key": "${SEARCH_KEY}"},
        },
        "local": {"command": "python3", "args": ["server.py"]},
        **(extra or {}),
    }
    repo = git_store.ensure_repo(pid)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    git_store.git(repo, "add", ".mcp.json")
    git_store.git(repo, "commit", "-qm", "Declare MCP servers")
    return pid


def _topic(client, pid: uuid.UUID) -> str:
    response = client.post(
        "/topics",
        json={"project_id": str(pid), "title": "做一个东西"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _servers(client, pid, handle="alice") -> dict:
    response = client.get(
        f"/projects/{pid}/mcp/servers", headers=session_auth_headers(handle)
    )
    assert response.status_code == 200, response.text
    return {s["name"]: s for s in response.json()["data"]["servers"]}


def _connect(client, pid, name="tracker", handle="alice") -> httpx.Response:
    """What a member's browser does: start from settings, pass through the
    authorization server, come back to the platform's callback."""
    started = client.post(
        f"/projects/{pid}/mcp/servers/{name}/connect",
        headers=session_auth_headers(handle),
    )
    assert started.status_code == 200, started.text
    authorization = started.json()["data"]["authorization_url"]
    consent = httpx.get(authorization, follow_redirects=False)
    assert consent.status_code == 302, consent.text
    back = urlsplit(consent.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == (
        "http://cheese.test/api/mcp/oauth/callback"
    )
    return client.get("/mcp/oauth/callback?" + back.query, follow_redirects=False)


def _call(client, tid, pid, name, method, params=None) -> dict:
    response = client.post(
        f"/topics/{tid}/mcp/{name}",
        json={"method": method, "params": params or {}},
        headers={
            "X-Cheese-Token": mint_scoped_token(project_id=str(pid), topic_id=tid)
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _rows(client, model):
    async def read():
        async with client.test_factory() as session:
            return list(await session.scalars(select(model)))

    return asyncio.run(read())


def test_a_member_connects_a_server_once_for_the_whole_project(client, upstream):
    pid = _project(client, upstream)
    before = _servers(client, pid)
    assert set(before) == {"tracker", "search"}, "stdio servers are the machine's"
    assert before["tracker"]["status"] == "disconnected"
    assert before["search"]["status"] == "missing_values"

    back = _connect(client, pid)
    assert back.status_code == 302
    landing = urlsplit(back.headers["location"])
    assert landing.path == f"/projects/{pid}/settings"
    assert parse_qs(landing.query)["mcp_result"] == ["connected"]

    # The authorization request followed the spec: PKCE S256 and the resource.
    asked = upstream.authorize_requests[-1]
    assert asked["code_challenge_method"] == "S256"
    assert asked["resource"] == f"{upstream.base}/mcp"
    assert upstream.token_requests[-1]["resource"] == f"{upstream.base}/mcp"

    after = _servers(client, pid)
    assert after["tracker"]["status"] == "connected"
    assert after["tracker"]["authorized_by"] == "alice"
    assert after["tracker"]["authorized_at"]

    # Another member sees the same connection: it is the project's.
    join_project_team(client, str(pid), "bob")
    assert _servers(client, pid, "bob")["tracker"]["authorized_by"] == "alice"

    # Stored sealed: the token the authorization server issued is not in the row.
    issued = upstream.issued[-1]
    (row,) = [r for r in _rows(client, ProjectMcpConnection) if r.project_id == pid]
    assert issued not in row.access_token
    assert "at-" not in row.access_token

    # No settings answer carries it either.
    assert issued not in json.dumps(after)


def test_a_room_calls_the_server_through_the_platform(client, upstream):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    _connect(client, pid)

    tools = _call(client, tid, pid, "tracker", "tools/list")
    assert tools["result"]["tools"][0]["name"] == "whoami"
    called = _call(
        client,
        tid,
        pid,
        "tracker",
        "tools/call",
        {"name": "whoami", "arguments": {"note": "hi"}},
    )
    text = called["result"]["content"][0]["text"]
    assert text == "reached with oauth; note=hi"
    # The upstream saw the project's token; the room's answer does not carry it.
    assert upstream.seen_credentials[-1] == f"Bearer {upstream.issued[-1]}"
    assert upstream.issued[-1] not in json.dumps(called)
    # The room shows who authorized it, to anyone in the room.
    room = client.get(
        f"/topics/{tid}/mcp/servers", headers=session_auth_headers("alice")
    ).json()["data"]["servers"]
    assert {s["name"]: s["authorized_by"] for s in room}["tracker"] == "alice"


def test_a_room_credential_of_another_project_is_refused(client, upstream):
    pid = _project(client, upstream)
    other = _project(client, upstream)
    tid = _topic(client, pid)
    _connect(client, pid)
    response = client.post(
        f"/topics/{tid}/mcp/tracker",
        json={"method": "tools/list"},
        headers={
            "X-Cheese-Token": mint_scoped_token(project_id=str(other), topic_id=tid)
        },
    )
    assert response.status_code == 403


def test_an_expired_token_is_refreshed_and_a_refused_refresh_asks_for_a_reconnect(
    client, upstream
):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    _connect(client, pid)
    first = upstream.issued[-1]

    upstream.expire_access_tokens()
    called = _call(client, tid, pid, "tracker", "tools/list")
    assert called["result"]["tools"]
    refreshed = upstream.token_requests[-1]
    assert refreshed["grant_type"] == "refresh_token"
    assert refreshed["resource"] == f"{upstream.base}/mcp"
    assert upstream.seen_credentials[-1] == f"Bearer {upstream.issued[-1]}"
    assert upstream.issued[-1] != first

    upstream.expire_access_tokens()
    upstream.refuse_refresh = True
    try:
        refused = _call(client, tid, pid, "tracker", "tools/list")
    finally:
        upstream.refuse_refresh = False
    assert "重新连接" in refused["error"]
    assert _servers(client, pid)["tracker"]["status"] == "needs_reconnect"


def test_an_unconnected_server_answers_with_what_to_do(client, upstream):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    seen = len(upstream.seen_credentials)
    answer = _call(client, tid, pid, "tracker", "tools/list")
    assert answer["error"] == "tracker 需要在项目设置里连接"
    assert upstream.seen_credentials[seen:] == [], "nothing reached the upstream"


def test_disconnect_revokes_at_the_authorization_server(client, upstream):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    _connect(client, pid)
    access = upstream.issued[-1]
    join_project_team(client, str(pid), "bob")

    # Any member may disconnect, not only the one who connected.
    response = client.delete(
        f"/projects/{pid}/mcp/servers/tracker/connection",
        headers=session_auth_headers("bob"),
    )
    assert response.status_code == 200, response.text
    assert access in upstream.revoked
    assert _servers(client, pid)["tracker"]["status"] == "disconnected"
    answer = _call(client, tid, pid, "tracker", "tools/list")
    assert answer["error"] == "tracker 需要在项目设置里连接"


def test_a_refused_revocation_is_logged_and_the_server_still_disconnects(
    client, upstream, caplog
):
    pid = _project(client, upstream)
    _connect(client, pid)
    access = upstream.issued[-1]
    upstream.refuse_revocation = True
    try:
        with caplog.at_level(logging.WARNING, logger="app.domain.remote_mcp"):
            response = client.delete(
                f"/projects/{pid}/mcp/servers/tracker/connection",
                headers=session_auth_headers("alice"),
            )
    finally:
        upstream.refuse_revocation = False
    assert response.status_code == 200, response.text
    assert _servers(client, pid)["tracker"]["status"] == "disconnected"
    refused = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert refused, caplog.text
    assert all("server=tracker" in line and "400" in line for line in refused)
    assert access not in caplog.text


def test_a_header_server_takes_its_key_from_a_project_secret(client, upstream):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    variables = _servers(client, pid)["search"]["variables"]
    assert variables == [
        {"name": "SEARCH_KEY", "set": False, "updated_by": None, "updated_at": None}
    ]
    missing = _call(client, tid, pid, "search", "tools/list")
    assert "SEARCH_KEY" in missing["error"]

    saved = client.put(
        f"/projects/{pid}/mcp/secrets/SEARCH_KEY",
        json={"value": fake_remote_mcp.API_KEY},
        headers=session_auth_headers("alice"),
    )
    assert saved.status_code == 200, saved.text
    row = _servers(client, pid)["search"]
    assert row["status"] == "ready"
    assert row["variables"][0]["set"] is True
    assert row["variables"][0]["updated_by"] == "alice"
    assert fake_remote_mcp.API_KEY not in json.dumps(row)
    (secret,) = [s for s in _rows(client, ProjectMcpSecret) if s.project_id == pid]
    assert fake_remote_mcp.API_KEY not in secret.value

    called = _call(
        client,
        tid,
        pid,
        "search",
        "tools/call",
        {"name": "whoami", "arguments": {"note": "k"}},
    )
    assert called["result"]["content"][0]["text"] == "reached with api key; note=k"
    assert upstream.seen_credentials[-1] == f"X-Api-Key {fake_remote_mcp.API_KEY}"


def test_a_legacy_sse_server_is_called_over_its_stream(client, upstream):
    pid = _project(
        client,
        upstream,
        extra={"old": {"type": "sse", "url": f"{upstream.base}/sse"}},
    )
    tid = _topic(client, pid)
    back = _connect(client, pid, "old")
    assert parse_qs(urlsplit(back.headers["location"]).query)["mcp_result"] == [
        "connected"
    ]
    called = _call(
        client, tid, pid, "old", "tools/call", {"name": "whoami", "arguments": {}}
    )
    assert called["result"]["content"][0]["text"].startswith(
        "reached with oauth over sse"
    )


def test_only_members_manage_the_projects_servers(client, upstream):
    pid = _project(client, upstream)
    outsider = session_auth_headers("mallory")
    assert (
        client.get(f"/projects/{pid}/mcp/servers", headers=outsider).status_code == 403
    )
    assert (
        client.post(
            f"/projects/{pid}/mcp/servers/tracker/connect", headers=outsider
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/projects/{pid}/mcp/secrets/SEARCH_KEY",
            json={"value": "x"},
            headers=outsider,
        ).status_code
        == 403
    )


def test_a_state_is_spent_once(client, upstream):
    pid = _project(client, upstream)
    started = client.post(
        f"/projects/{pid}/mcp/servers/tracker/connect",
        headers=session_auth_headers("alice"),
    ).json()["data"]["authorization_url"]
    consent = httpx.get(started, follow_redirects=False)
    query = urlsplit(consent.headers["location"]).query
    first = client.get("/mcp/oauth/callback?" + query, follow_redirects=False)
    assert "mcp_result" in first.headers["location"]
    replay = client.get("/mcp/oauth/callback?" + query, follow_redirects=False)
    assert "mcp_error" in replay.headers["location"]


# --- reading `.mcp.json` --------------------------------------------------------


def _session_servers(client, pid) -> set[str]:
    """Every server a session in the project is told about, usable or not."""
    from app.domain.remote_mcp import service

    async def ask():
        async with client.test_request_factory() as session:
            found = await service.session_servers(session, pid, None)
            return {*found.usable, *found.unusable}

    return client.portal.call(ask)


def _commit_servers(pid, servers: dict) -> None:
    repo = git_store.ensure_repo(pid)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    git_store.git(repo, "commit", "-qam", "Change MCP servers")


def _forge_answering(monkeypatch, answer):
    """The forge, with `answer` standing in for its reply about `.mcp.json`."""
    from app.domain.project import forge

    real = forge.repository_data

    async def repository_data(project_id, session, path="", **kwargs):
        if path.startswith("/contents/"):
            return await answer(lambda: real(project_id, session, path, **kwargs))
        return await real(project_id, session, path, **kwargs)

    monkeypatch.setattr(forge, "repository_data", repository_data)


def test_a_session_does_not_wait_on_the_forge_for_a_list_it_has_read(
    client, upstream, monkeypatch
):
    pid = _project(client, upstream)
    assert _session_servers(client, pid) == {"tracker", "search"}
    # Every earlier read is out of date from here on.
    monkeypatch.setattr(declared, "_FRESH_S", 0)
    _commit_servers(pid, {"wiki": {"type": "http", "url": f"{upstream.base}/mcp"}})

    async def slow(read):
        await asyncio.sleep(3)
        return await read()

    _forge_answering(monkeypatch, slow)

    started = time.monotonic()
    assert _session_servers(client, pid) == {"tracker", "search"}
    assert time.monotonic() - started < 1.5

    deadline = time.monotonic() + 10
    while _session_servers(client, pid) != {"wiki"}:
        assert time.monotonic() < deadline, "the committed change never arrived"
        time.sleep(0.2)


def test_a_session_keeps_its_servers_while_the_forge_is_down(
    client, upstream, monkeypatch
):
    pid = _project(client, upstream)
    assert _session_servers(client, pid) == {"tracker", "search"}
    monkeypatch.setattr(declared, "_FRESH_S", 0)

    async def down(_read):
        raise httpx.ConnectError("forge unreachable")

    _forge_answering(monkeypatch, down)

    for _ in range(3):
        assert _session_servers(client, pid) == {"tracker", "search"}
        time.sleep(0.2)


def test_the_settings_page_shows_a_committed_change_at_once(client, upstream):
    pid = _project(client, upstream)
    assert set(_servers(client, pid)) == {"tracker", "search"}
    _commit_servers(pid, {"wiki": {"type": "http", "url": f"{upstream.base}/mcp"}})

    assert set(_servers(client, pid)) == {"wiki"}


@pytest.mark.parametrize(
    ("committed", "problem"),
    [(None, "missing"), ("directory", "missing"), ("{not json", "invalid")],
)
def test_what_the_default_branch_holds_decides_the_answer(
    client, upstream, committed, problem
):
    response = post_project(
        client, json={"name": "P"}, headers=session_auth_headers("alice")
    )
    pid = uuid.UUID(response.json()["data"]["id"])
    repo = git_store.ensure_repo(pid)
    if committed == "directory":
        (repo / ".mcp.json").mkdir()
        (repo / ".mcp.json" / "servers.json").write_text("{}")
    elif committed is not None:
        (repo / ".mcp.json").write_text(committed)
    if committed is not None:
        git_store.git(repo, "add", ".mcp.json")
        git_store.git(repo, "commit", "-qm", "Add .mcp.json")

    found = client.get(
        f"/projects/{pid}/mcp/servers", headers=session_auth_headers("alice")
    ).json()["data"]
    assert found == {"servers": [], "problem": problem}
