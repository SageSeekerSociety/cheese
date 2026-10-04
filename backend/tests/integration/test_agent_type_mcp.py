"""An agent type's MCP servers reach its teammates' sessions, and only theirs.

A type declares them as a Claude Code subagent does (`mcpServers`). An inline
remote server is connected once for the project, in project settings, like a
`.mcp.json` one, and the platform calls it with that connection; an inline
stdio server is handed to the session as a definition its machine runs. A
teammate of another type gets neither. A name the project's `.mcp.json`
already uses stays the project's.
"""

import json
import sys
import uuid
from typing import Any

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness import SessionRef
from app.domain.agent_instance import services as agent_instances
from app.domain.agent_type.library import load_type_library
from app.domain.remote_mcp import service
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration import test_remote_mcp as base
from tests.integration.conftest import session_auth_headers
from tests.integration.test_archive_retires_storage import _seed_device

upstream = base.upstream
_local_upstream = base._local_upstream
_connect, _project, _servers, _topic = (
    base._connect,
    base._project,
    base._servers,
    base._topic,
)

LINT = {"command": sys.executable, "args": ["lint.py"]}


@pytest.fixture
def types(upstream, tmp_path, monkeypatch):
    """A type library with one type that declares servers, and one that does not."""
    (tmp_path / "tracer.md").write_text(
        f"""---
name: tracer
title: Tracer
mcpServers:
  - ticket:
      type: http
      url: {upstream.base}/mcp
  - lint:
      command: {json.dumps(LINT["command"])}
      args: ["lint.py"]
  # The project's `.mcp.json` names a server `search` too; the project's stays.
  - search:
      type: http
      url: https://elsewhere.example.test/mcp
  - tracker
---
You trace tickets.
""",
        encoding="utf-8",
    )
    (tmp_path / "plain.md").write_text(
        "---\nname: plain\n---\nYou help.", encoding="utf-8"
    )
    # A second type that declares the same remote server, defined the same way.
    (tmp_path / "auditor.md").write_text(
        f"""---
name: auditor
title: Auditor
mcpServers:
  - ticket:
      type: http
      url: {upstream.base}/mcp
---
You audit tickets.
""",
        encoding="utf-8",
    )
    library = load_type_library(tmp_path)
    monkeypatch.setattr(agent_instances, "preset_types", lambda: library)
    return library


def _teammate(client, pid: uuid.UUID, handle: str, type_name: str) -> str:
    """A teammate of this type, and the seat handle its sessions act as."""
    made = client.post(
        f"/projects/{pid}/agents",
        json={"handle": handle, "type_name": type_name, "display_name": handle},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text
    assert "mcp_servers" not in made.json()["data"]["configuration"]
    return made.json()["data"]["seat_handle"]


def _call_as(client, tid: str, pid, seat: str, name: str, method: str, params=None):
    response = client.post(
        f"/topics/{tid}/mcp/{name}",
        json={"method": method, "params": params or {}},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(pid), topic_id=tid, agent_handle=seat
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _session_target(client, pid, tid, seat):
    async def compose():
        async with client.test_factory() as session:
            return await service.session_target(session, pid, uuid.UUID(tid), seat)

    return client.portal.call(compose)


def test_a_types_remote_server_uses_the_projects_connection_for_its_teammates(
    client, upstream, types
):
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    tracer = _teammate(client, pid, "tracer-1", "tracer")
    plain = _teammate(client, pid, "plain-1", "plain")

    # Project settings list it beside the `.mcp.json` servers, to connect once.
    listed = _servers(client, pid)
    assert listed["ticket"]["status"] == "disconnected"
    # A name the project already uses is the project's definition.
    assert listed["search"]["host"] != "elsewhere.example.test"
    assert listed["search"]["auth"] == "headers"

    back = _connect(client, pid, name="ticket")
    assert back.status_code == 302, back.text
    assert _servers(client, pid)["ticket"]["authorized_by"] == "alice"

    assert _session_target(client, pid, tid, tracer) == {
        "path": f"/topics/{tid}/mcp",
        "servers": ["ticket"],
    }
    assert _session_target(client, pid, tid, plain) is None

    tools = _call_as(client, tid, pid, tracer, "ticket", "tools/list")
    assert tools["result"]["tools"][0]["name"] == "whoami"
    called = _call_as(
        client,
        tid,
        pid,
        tracer,
        "ticket",
        "tools/call",
        {"name": "whoami", "arguments": {"note": "hi"}},
    )
    assert called["result"]["content"][0]["text"] == "reached with oauth; note=hi"
    # Called with the project's connection, which the session never holds.
    assert upstream.seen_credentials[-1] == f"Bearer {upstream.issued[-1]}"
    assert upstream.issued[-1] not in json.dumps(called)

    reached = len(upstream.tool_calls)
    refused = _call_as(
        client,
        tid,
        pid,
        plain,
        "ticket",
        "tools/call",
        {"name": "whoami", "arguments": {"note": "not mine"}},
    )
    assert "error" in refused and "result" not in refused
    assert len(upstream.tool_calls) == reached, "another type never reaches it"


def test_each_remote_server_says_where_it_comes_from(client, upstream, types):
    """Project settings and the room's read-only list say, for each remote
    server, whether the project's `.mcp.json` declares it or which of its
    teammates' types do."""
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    _teammate(client, pid, "tracer-1", "tracer")
    _teammate(client, pid, "plain-1", "plain")

    listed = _servers(client, pid)
    assert listed["tracker"]["declared_by"] is None
    # A type reusing a name the project declares does not make it the type's.
    assert listed["search"]["declared_by"] is None
    assert listed["ticket"]["declared_by"] == [{"name": "tracer", "title": "Tracer"}]

    # Once a teammate of another type that declares it joins, both are named.
    _teammate(client, pid, "auditor-1", "auditor")
    declared_by = _servers(client, pid)["ticket"]["declared_by"]
    assert sorted(t["name"] for t in declared_by) == ["auditor", "tracer"]

    room = client.get(
        f"/topics/{tid}/mcp/servers", headers=session_auth_headers("alice")
    )
    assert room.status_code == 200, room.text
    by_name = {s["name"]: s for s in room.json()["data"]["servers"]}
    assert by_name["tracker"]["declared_by"] is None
    assert sorted(t["title"] for t in by_name["ticket"]["declared_by"]) == [
        "Auditor",
        "Tracer",
    ]


@pytest.fixture
def room(client, upstream):
    pid = _project(client, upstream)
    tid = uuid.UUID(_topic(client, pid))

    async def seed():
        async with client.test_factory() as db:
            await _seed_device(db, "executor", project_id=pid)
            await db.commit()

    client.portal.call(seed)
    return pid, tid


def _central_target(client, monkeypatch, pid, tid, handle, seat) -> dict:
    """The execution target a session of this teammate, on any harness,
    starts with. A session is keyed by the teammate's handle; its credential
    names the seat it acts as."""
    central: Any = central_sessions.channel(client, monkeypatch)
    ref = SessionRef(pid, tid, handle, harness="claude-code")

    async def open_session():
        await central_sessions.sessions(central).ensure(
            ref, system_prompt="System", acting=seat
        )
        opening = central._ensure_screen.await_args.kwargs
        return json.loads(opening["env"]["CHEESE_EXECUTION_TARGET"])

    return client.portal.call(open_session)


def test_a_types_stdio_server_is_handed_only_to_its_teammates_sessions(
    client, room, types, monkeypatch
):
    pid, tid = room
    tracer = _teammate(client, pid, "tracer-1", "tracer")
    plain = _teammate(client, pid, "plain-1", "plain")

    own = _central_target(client, monkeypatch, pid, tid, "tracer-1", tracer)
    assert own["agent_mcp"] == {"lint": LINT}
    other = _central_target(client, monkeypatch, pid, tid, "plain-1", plain)
    assert "agent_mcp" not in other


def _private_target(client, monkeypatch, pid, tid, handle, seat) -> dict:
    """The execution target a private chat's session of this teammate starts
    with: a turn there rents no place, so it runs in the chat's scratch."""
    central: Any = central_sessions.channel(client, monkeypatch)
    ref = SessionRef(pid, tid, handle, harness="claude-code")

    async def open_session():
        await central_sessions.sessions(central).ensure(
            ref, system_prompt="System", acting=seat, needs_place=False
        )
        opening = central._ensure_screen.await_args.kwargs
        return json.loads(opening["env"]["CHEESE_EXECUTION_TARGET"])

    return client.portal.call(open_session)


def test_a_private_chat_keeps_the_teammates_type_servers(
    client, room, upstream, types, monkeypatch
):
    """The teammate in a private chat is the same teammate: its type's servers
    come with it — the stdio one to run in the chat's scratch, the remote one
    through the project's connection — and another type's teammate gets none."""
    pid, tid = room
    tracer = _teammate(client, pid, "tracer-1", "tracer")
    plain = _teammate(client, pid, "plain-1", "plain")
    assert _connect(client, pid, name="ticket").status_code == 302

    own = _private_target(client, monkeypatch, pid, tid, "tracer-1", tracer)
    assert own["kind"] == "private"
    assert own["agent_mcp"] == {"lint": LINT}
    assert "ticket" in own["remote_mcp"]["servers"]
    assert own["remote_mcp"]["path"] == f"/topics/{tid}/mcp"

    other = _private_target(client, monkeypatch, pid, tid, "plain-1", plain)
    assert other["kind"] == "private"
    assert "agent_mcp" not in other
    assert "ticket" not in (other.get("remote_mcp") or {}).get("servers", [])
