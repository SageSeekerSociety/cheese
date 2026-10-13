"""Model connections a person's account holds (#2991): an Anthropic API key or
a custom model service, entered on 「设置 → 模型连接」 with no machine.

The platform keeps the key sealed and never shows it again, only its last four
characters; the service has to answer it before it is kept. Only its owner sees,
checks, renames or removes it, and removing it deletes the key at once. In a
project that allows its kind, its owner adds their own Claude Code with it like
any connection. Its session runs on the deployment's session host, not on any
of its owner's machines, and the key goes into that session's process and not
into what its execution environment is handed.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError, mint_session_token
from app.domain.agent.owner_provider import AccountChannel, owner_is_away
from app.domain.agent.place import OWN_MODEL_KEY
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AccountConnection, AgentInstance
from app.domain.agent_session.models import AgentSession
from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

MODELS = "/connector/my/model-connections"
KEY = "sk-ant-api03-" + "x" * 40 + "Q7zK"


class _Service:
    """The model services the backend calls to check a connection: each
    request is recorded, and the answer is ``status`` with ``message``."""

    def __init__(self, status: int = 200, message: str = ""):
        self.status = status
        self.message = message
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(
                self.status, json={"error": {"message": self.message}}
            )
        return httpx.Response(self.status, json={"data": []})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def service(monkeypatch) -> _Service:
    answering = _Service()
    monkeypatch.setattr(account_connections, "_client", answering.client)
    # The test hosts resolve nowhere; the address rule has a test of its own.
    monkeypatch.setattr(account_connections, "refuse_internal_host", lambda *a: None)
    return answering


def _people(client):
    """A project alice manages and bob is a member of, open to members' own
    Claude Code."""
    project = new_project(client, "Keys", owner="alice")
    join_project_team(client, project["id"], "bob")
    r = client.put(
        f"/projects/{project['id']}/own-agents",
        json={"allowed": True},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return project["id"]


def _keep(client, who="bob", **body):
    body = {"kind": "api_key", "key": KEY, **body}
    return client.post(MODELS, json=body, headers=session_auth_headers(who))


def _mine(client, who="bob") -> list[dict]:
    r = client.get(MODELS, headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["account_connections"]


def _stored(client) -> list[str]:
    async def read():
        async with client.test_factory() as db:
            rows = (await db.scalars(select(AccountConnection))).all()
            return [
                json.dumps(
                    {c.name: str(getattr(r, c.name)) for c in r.__table__.columns}
                )
                for r in rows
            ]

    return client.portal.call(read)


def test_a_key_is_kept_sealed_and_only_its_last_four_are_shown(client, service):
    _people(client)

    kept = _keep(client)

    assert kept.status_code == 200, kept.text
    assert KEY not in kept.text
    assert kept.json()["key_last4"] == "Q7zK"
    (listed,) = _mine(client)
    assert listed["kind"] == "api_key" and listed["key_last4"] == "Q7zK"
    assert listed["checked_at"] and listed["check_error"] is None
    assert KEY not in json.dumps(_mine(client))
    (row,) = _stored(client)
    assert KEY not in row, "the key is sealed at rest"
    (asked,) = service.requests
    assert asked.url.host == "api.anthropic.com"
    assert asked.headers["x-api-key"] == KEY


def test_a_connection_its_service_refuses_is_not_kept(client, service):
    _people(client)
    service.status, service.message = 401, f"invalid x-api-key {KEY}"

    refused = _keep(client)

    assert refused.status_code == 422, refused.text
    assert "401" in refused.text
    assert KEY not in refused.text, "a service echoing the key does not show it"
    assert _mine(client) == []
    assert _stored(client) == []


def test_a_model_service_is_checked_with_its_own_model(client, service):
    _people(client)

    kept = _keep(
        client,
        kind="model_service",
        base_url="https://api.moonshot.test/anthropic",
        model="kimi-k2",
    )

    assert kept.status_code == 200, kept.text
    assert kept.json()["service_host"] == "api.moonshot.test"
    assert kept.json()["model"] == "kimi-k2"
    (asked,) = service.requests
    assert str(asked.url) == "https://api.moonshot.test/anthropic/v1/messages"
    assert json.loads(asked.content)["model"] == "kimi-k2"
    assert asked.headers["authorization"] == f"Bearer {KEY}"


def test_a_model_service_with_no_model_is_refused_without_echoing_the_key(
    client, service
):
    _people(client)

    refused = _keep(
        client, kind="model_service", base_url="https://api.moonshot.test/anthropic"
    )

    assert refused.status_code in (400, 422), refused.text
    assert KEY not in refused.text
    assert service.requests == []
    assert _stored(client) == []


def test_a_subscription_is_never_kept_on_an_account(client, service):
    _people(client)

    refused = _keep(client, kind="subscription")

    assert refused.status_code in (400, 422), refused.text
    assert service.requests == []
    assert _stored(client) == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.moonshot.test/anthropic",
        "https://127.0.0.1:8081",
        "https://10.0.0.5",
    ],
)
def test_a_service_on_plain_http_or_the_platforms_own_network_is_refused(
    client, monkeypatch, base_url
):
    _people(client)
    answering = _Service()
    monkeypatch.setattr(account_connections, "_client", answering.client)

    refused = _keep(client, kind="model_service", base_url=base_url, model="k2")

    assert refused.status_code in (400, 422), refused.text
    assert answering.requests == [], "the backend never called it"
    assert _stored(client) == []


def test_only_its_owner_sees_checks_renames_or_removes_it(client, service):
    _people(client)
    connection = _keep(client).json()["id"]
    service.requests.clear()
    alice = session_auth_headers("alice")

    assert _mine(client, "alice") == []
    assert client.post(f"{MODELS}/{connection}/check", headers=alice).status_code == 404
    assert (
        client.patch(
            f"{MODELS}/{connection}", json={"name": "mine"}, headers=alice
        ).status_code
        == 404
    )
    assert client.delete(f"{MODELS}/{connection}", headers=alice).status_code == 404
    assert service.requests == [], "nobody else made the key be used"
    (still,) = _mine(client)
    assert still["name"] is None


def test_a_recheck_records_what_the_service_says_now(client, service):
    _people(client)
    connection = _keep(client).json()["id"]
    service.status, service.message = 403, "key disabled"

    checked = client.post(
        f"{MODELS}/{connection}/check", headers=session_auth_headers("bob")
    )

    assert checked.status_code == 200, checked.text
    assert "403" in checked.json()["check_error"]
    (listed,) = _mine(client)
    assert "key disabled" in listed["check_error"]


# --- in a project -----------------------------------------------------------


def _settings(client, project_id, viewer):
    r = client.get(
        f"/projects/{project_id}/own-agents", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _add(client, project_id, connection, who="bob"):
    return client.post(
        f"/projects/{project_id}/own-agents",
        json={"connection_id": connection},
        headers=session_auth_headers(who),
    )


def test_its_owner_adds_their_claude_code_with_it_and_it_carries_its_name(
    client, service
):
    project_id = _people(client)
    connection = _keep(
        client,
        kind="model_service",
        base_url="https://api.moonshot.test/anthropic",
        model="kimi-k2",
    ).json()["id"]

    (offered,) = _settings(client, project_id, "bob")["my_connections"]
    assert offered["id"] == connection
    assert offered["on_account"] is True and offered["machine_name"] is None
    assert offered["online"] is True and offered["allowed"] is True

    added = _add(client, project_id, connection)

    assert added.status_code == 200, added.text
    (agent,) = added.json()["data"]["agents"]
    assert agent["name"].endswith("的 Claude Code（kimi-k2）")
    assert agent["connection"]["on_account"] is True
    assert agent["machine"] is None

    renamed = client.patch(
        f"{MODELS}/{connection}",
        json={"name": "Kimi"},
        headers=session_auth_headers("bob"),
    )
    assert renamed.status_code == 200, renamed.text
    (agent,) = _settings(client, project_id, "bob")["agents"]
    assert agent["name"].endswith("的 Claude Code（Kimi）")


def test_nobody_else_adds_it(client, service):
    project_id = _people(client)
    connection = _keep(client).json()["id"]

    refused = _add(client, project_id, connection, who="alice")

    assert refused.status_code == 404, refused.text
    assert _settings(client, project_id, "alice")["agents"] == []


def test_a_project_that_refuses_its_kind_does_not_take_it(client, service):
    project_id = _people(client)
    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"kinds": ["subscription", "model_service"]},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    connection = _keep(client).json()["id"]

    (offered,) = _settings(client, project_id, "bob")["my_connections"]
    assert offered["allowed"] is False
    assert _add(client, project_id, connection).status_code == 403


# --- its session ------------------------------------------------------------


class _Hub:
    """The session host is online; nothing else is asked."""

    def __init__(self, host):
        self.host = host

    def is_online(self, device):
        return device == self.host

    def reconnecting(self, device):
        return False


def _session(client, project_id, connection):
    """Bob's own Claude Code on the connection, its conversation in a room."""
    added = _add(client, project_id, connection)
    assert added.status_code == 200, added.text
    room = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    async def handle():
        async with client.test_factory() as db:
            instance = await db.scalar(
                select(AgentInstance).where(
                    AgentInstance.project_id == uuid.UUID(project_id),
                    AgentInstance.handle.like("own-claude-code-%"),
                )
            )
            return instance.handle

    return SessionRef(
        uuid.UUID(project_id),
        uuid.UUID(room["id"]),
        client.portal.call(handle),
        harness="claude-code",
    )


def _prepare(client, session, host):
    channel = AccountChannel(
        DeviceChannel(hub=_Hub(host), session_factory=client.test_request_factory)
    )

    async def prepare():
        precheck = await channel.precheck(session, needs_place=True)
        token = mint_session_token(
            session.project_id, session.topic_id, precheck.agent_handle
        )
        async with channel.prepare_session(
            session=session, token=token, env={}, precheck=precheck
        ) as prepared:
            return prepared

    return client.portal.call(prepare)


def test_its_session_runs_on_the_session_host_with_its_key_and_hands_none_on(
    client, service, monkeypatch
):
    monkeypatch.setattr(settings, "agent_session_device_id", "the-session-host")
    project_id = _people(client)
    connection = _keep(client).json()["id"]
    session = _session(client, project_id, connection)

    prepared = _prepare(client, session, "the-session-host")

    assert prepared.device_id == "the-session-host"
    assert prepared.env[OWN_MODEL_KEY] == KEY, "its process calls with the key"
    assert KEY not in prepared.env["CHEESE_EXECUTION_TARGET"], (
        "what the execution environment is handed holds no key"
    )

    async def recorded():
        async with client.test_factory() as db:
            rows = (await db.scalars(select(AgentSession))).all()
            return json.dumps(
                [[r.work_lease, r.runtime_location, r.execution_request] for r in rows],
                default=str,
            )

    assert KEY not in client.portal.call(recorded)


def test_a_removed_connection_deletes_its_key_and_starts_nothing(
    client, service, monkeypatch
):
    monkeypatch.setattr(settings, "agent_session_device_id", "the-session-host")
    project_id = _people(client)
    connection = _keep(client).json()["id"]
    session = _session(client, project_id, connection)

    removed = client.delete(
        f"{MODELS}/{connection}", headers=session_auth_headers("bob")
    )

    assert removed.status_code == 200, removed.text
    assert _stored(client) == [], "the key is gone"
    assert _mine(client) == []
    (agent,) = _settings(client, project_id, "bob")["agents"]
    assert agent["connection"] is None
    with pytest.raises(ScreenSetupError) as refused:
        _prepare(client, session, "the-session-host")
    assert "模型连接已解除" in str(refused.value), "the room is told why"


def test_a_message_to_it_waits_for_none_of_its_owners_machines(client, service):
    project_id = _people(client)
    connection = _keep(client).json()["id"]
    session = _session(client, project_id, connection)

    async def away():
        async with client.test_factory() as db:
            instance = await db.scalar(
                select(AgentInstance).where(
                    AgentInstance.handle == session.agent_handle
                )
            )
            # None of bob's machines is online, nor has he any.
            return await owner_is_away(db, instance.id, _Hub(None))

    assert client.portal.call(away) is False
