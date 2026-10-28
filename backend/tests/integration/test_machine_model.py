"""A model call a sandbox makes on its platform socket.

The rules, as a person would state them:

- the sandbox holds no token; the call is the session's that leases the
  sandbox now, and goes out on that project's machine key, apart from the
  session's own (`gateway_usage.MachineGatewayKey`);
- who the call is, as the device link owner vouched for it, is never shown to
  the gateway;
- nobody but the device link owner is anyone here.
"""

import pytest

from app.api.routes import llm_proxy
from app.core.config import settings
from tests.integration.test_llm_proxy import _FakeClient
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _pool(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _FakeClient)

    async def fake_key(
        self,
        project_id,
        *,
        dream=False,
        task_id=None,
        credit_owner_handle=None,
        machine_conversation_id=None,
    ):
        kind = "machine" if machine_conversation_id else "virtual"
        return f"sk-{kind}-for-" + str(project_id)[:8]

    monkeypatch.setattr(
        "app.domain.agent.chat.ChatService.project_gateway_key", fake_key
    )


def _ask(client, *, device, secret="owner-secret"):
    return client.post(
        "/machine/llm/v1/messages",
        headers={
            "x-device-connection-secret": secret,
            "x-machine-device": device,
            "x-machine-state": "/old/state",
            "anthropic-version": "2023-06-01",
        },
        content=b'{"model":"m","messages":[]}',
    )


async def test_the_sandboxs_call_goes_out_on_its_projects_machine_key(client):
    room = await _room(client)
    answer = _ask(client, device=room.old_device)
    assert answer.status_code == 200, answer.text
    assert answer.content == b'{"ok":true}'
    sent = _FakeClient.seen
    assert sent["url"] == "http://pool:4000/v1/messages"
    assert sent["headers"]["authorization"] == (
        f"Bearer sk-machine-for-{str(room.project_id)[:8]}"
    )
    assert sent["content"] == b'{"model":"m","messages":[]}'


async def test_the_gateway_never_sees_who_vouched_for_the_call(client):
    room = await _room(client)
    assert _ask(client, device=room.old_device).status_code == 200
    seen = {k.lower() for k in _FakeClient.seen["headers"]}
    assert not seen & {
        "x-device-connection-secret",
        "x-machine-device",
        "x-machine-state",
    }
    assert "owner-secret" not in str(_FakeClient.seen["headers"])


async def test_without_the_owner_or_a_lease_there_is_no_call(client):
    room = await _room(client)
    _FakeClient.seen = {}
    assert _ask(client, device=room.old_device, secret="guessed").status_code == 401
    assert _ask(client, device=room.new_device).status_code == 403
    assert _FakeClient.seen == {}


async def test_a_teammates_sandbox_calls_as_the_seat_it_acts_under(client):
    """A session is stored under its teammate's handle but acts under the seat
    that teammate has on the room's roster, which is what its own credential
    names; a call from its sandbox must pass the same seat check."""
    from sqlalchemy import select

    from app.domain.agent_session.models import AgentSession
    from tests.integration.test_session_agent_names import _teammates_on_bobs_device

    team = await _teammates_on_bobs_device(client)
    async with client.test_factory() as db:
        session = await db.scalar(
            select(AgentSession).where(
                AgentSession.agent_handle == team.handles["opus"]
            )
        )
        session.work_lease = {
            "device_id": team.device_id,
            "state": "/opus/state",
            "status": "ready",
        }
        await db.commit()
    headers = {
        "x-device-connection-secret": "owner-secret",
        "x-machine-device": team.device_id,
        "x-machine-state": "/opus/state",
    }
    who = client.get("/machine/whoami", headers=headers).json()
    assert who["agent"] != team.handles["opus"], who
    answer = client.post(
        "/machine/llm/v1/messages",
        headers={**headers, "anthropic-version": "2023-06-01"},
        content=b'{"model":"m","messages":[]}',
    )
    assert answer.status_code == 200, answer.text
