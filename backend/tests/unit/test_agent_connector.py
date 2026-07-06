"""Unit tests for the agent-plane connector seams.

These guard the two invariants the connector exists to enforce (architecture
§1, §5):

* **Actor injection.** ``ActorContext`` is injected by the registry at the
  trust boundary; it is never part of a tool's agent-facing schema and can
  never be supplied as agent JSON.
* **逻辑只读 arbitration.** cheesed is a dumb read-write relay; "read-only" is
  command-emission arbitration owned by the backend ``SessionHub``: agent
  output fans out to every viewer, but only the current *controller*'s input
  reaches the agent (resize geometry excepted). Takeover moves the controller.

Everything is exercised against the in-memory defaults with a fake transport --
no WebSocket, no cheesed process.
"""

import json

import pytest

from app.agent.authorization import PermissiveAuthorizer, ScopeAuthorizer
from app.agent.context import ActorContext
from app.agent.proxy import SessionHub
from app.agent.registry import ToolRegistry, tool
from app.agent.services import InMemoryChatService
from app.agent.sessions import InMemorySessionStore, SessionStatus
from app.agent.tools import register_default_tools
from app.core.errors import PermissionDeniedError

# --- schema projection + actor injection ---------------------------------


def test_actor_param_is_never_in_the_agent_facing_schema() -> None:
    registry = ToolRegistry()
    register_default_tools(registry, InMemoryChatService())
    schema = {t["name"]: t for t in registry.schema()}
    chat = schema["chat"]
    assert "message" in chat["parameters"]["properties"]
    assert "message" in chat["parameters"]["required"]
    # the injected identity is invisible to the agent
    assert "actor" not in chat["parameters"]["properties"]


def test_optional_param_is_not_required() -> None:
    registry = ToolRegistry()

    @tool(name="echo", registry=registry)
    async def echo(actor: ActorContext, text: str, times: int = 1) -> dict[str, object]:
        return {"text": text * times}

    params = registry.schema()[0]["parameters"]
    assert params["required"] == ["text"]  # times has a default -> optional


@pytest.mark.anyio
async def test_invoke_injects_actor_and_rejects_undeclared_args() -> None:
    registry = ToolRegistry()
    chat = InMemoryChatService()
    register_default_tools(registry, chat)
    actor = ActorContext(actor_id="agent-7", session_id="s1")

    result = await registry.invoke("chat", actor, {"message": "hi"})
    assert result["authorId"] == "agent-7"  # actor came from the boundary, not JSON

    # an agent cannot smuggle a different identity through the args
    with pytest.raises(ValueError):
        await registry.invoke("chat", actor, {"message": "hi", "actor": "someone-else"})
    with pytest.raises(ValueError):
        await registry.invoke("chat", actor, {})  # missing required


# --- authorization -------------------------------------------------------


@pytest.mark.anyio
async def test_scope_authorizer_denies_without_scope() -> None:
    authz = ScopeAuthorizer()
    with pytest.raises(PermissionDeniedError):
        await authz.authorize(ActorContext(actor_id="a"), "chat", {})
    # wildcard grants
    await authz.authorize(ActorContext(actor_id="a", scopes=frozenset({"tool:*"})), "chat", {})


@pytest.mark.anyio
async def test_permissive_authorizer_allows() -> None:
    await PermissiveAuthorizer().authorize(ActorContext(actor_id="a"), "chat", {})


# --- 逻辑只读 arbitration (SessionHub) ------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.binaries: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.texts.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binaries.append(data)


async def _session(store: InMemorySessionStore) -> str:
    session = await store.create(ActorContext(actor_id="agent-1", session_token="tok"))
    assert session.status is SessionStatus.ACTIVE
    return session.session_id


@pytest.mark.anyio
async def test_agent_output_fans_out_to_every_viewer() -> None:
    store = InMemorySessionStore()
    hub = SessionHub(store)
    sid = await _session(store)
    agent = FakeTransport()
    v1, v2 = FakeTransport(), FakeTransport()
    await hub.attach_agent(sid, agent)
    await hub.attach_viewer(sid, "v1", v1)
    await hub.attach_viewer(sid, "v2", v2)

    await hub.on_agent_binary(sid, b"1screen-bytes")
    assert v1.binaries == [b"1screen-bytes"]
    assert v2.binaries == [b"1screen-bytes"]


@pytest.mark.anyio
async def test_non_controller_input_is_dropped_but_controller_input_reaches_agent() -> None:
    store = InMemorySessionStore()
    hub = SessionHub(store)
    sid = await _session(store)
    agent = FakeTransport()
    viewer = FakeTransport()
    await hub.attach_agent(sid, agent)
    await hub.attach_viewer(sid, "v1", viewer)

    # default state: no controller -> keystroke dropped
    await hub.on_viewer_binary(sid, "v1", b"1ls\n")
    assert agent.binaries == []

    # takeover -> viewer becomes controller -> keystroke forwarded
    await hub.request_takeover(sid, "v1", True)
    await hub.on_viewer_binary(sid, "v1", b"1ls\n")
    assert agent.binaries == [b"1ls\n"]


@pytest.mark.anyio
async def test_resize_geometry_is_forwarded_from_any_viewer() -> None:
    store = InMemorySessionStore()
    hub = SessionHub(store)
    sid = await _session(store)
    agent = FakeTransport()
    viewer = FakeTransport()
    await hub.attach_agent(sid, agent)
    await hub.attach_viewer(sid, "v1", viewer)

    # webtty opcode '3' = ResizeTerminal: geometry, not state-changing input.
    # forwarded even though this non-controller viewer is "read-only".
    await hub.on_viewer_binary(sid, "v1", b'3{"columns":120,"rows":40}')
    assert agent.binaries == [b'3{"columns":120,"rows":40}']


@pytest.mark.anyio
async def test_takeover_grant_notifies_agent_and_broadcasts_then_release() -> None:
    store = InMemorySessionStore()
    hub = SessionHub(store)
    sid = await _session(store)
    agent = FakeTransport()
    viewer = FakeTransport()
    await hub.attach_agent(sid, agent)
    await hub.attach_viewer(sid, "v1", viewer)

    await hub.request_takeover(sid, "v1", True)
    session = await store.get(sid)
    assert session is not None and session.controller == "v1"
    # agent told to pause its auto-driver
    assert any(json.loads(t) == {"t": "takeover", "on": True} for t in agent.texts)
    # viewer UI learns it is now the controller
    assert any(json.loads(t).get("granted") is True for t in viewer.texts)

    await hub.request_takeover(sid, "v1", False)
    session = await store.get(sid)
    assert session is not None and session.controller is None
    assert any(json.loads(t) == {"t": "takeover", "on": False} for t in agent.texts)


@pytest.mark.anyio
async def test_viewer_disconnect_releases_its_takeover() -> None:
    store = InMemorySessionStore()
    hub = SessionHub(store)
    sid = await _session(store)
    agent = FakeTransport()
    viewer = FakeTransport()
    await hub.attach_agent(sid, agent)
    await hub.attach_viewer(sid, "v1", viewer)
    await hub.request_takeover(sid, "v1", True)

    await hub.detach_viewer(sid, "v1")
    session = await store.get(sid)
    assert session is not None and session.controller is None  # authority not left dangling
