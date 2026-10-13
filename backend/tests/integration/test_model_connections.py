"""A person's model connections, managed from any browser (#2991).

Their own Claude Code is logged in on the machines they enrolled; 「模型连接」
lists each login and lets its owner remove it, or point a machine at another
model service, without being at that machine. Only the owner sees or touches
their connections. A removed connection is out of use at once — nothing starts
with it — and a machine that was away when it was removed logs it out as it
next connects. A model service's key goes to the machine and nowhere else.
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.api.routes import connector
from app.domain.agent import initial_admission
from app.domain.agent.harness.claude_code import owner_login
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)


class _Machines:
    """The device hub as these rules see it: which machines are online, and
    what each one answers. A logged-out machine says so when asked."""

    def __init__(self):
        self.online: set[str] = set()
        self.logged_in: dict[str, bool] = {}
        self.refuse_logout = False
        self.exec = AsyncMock(side_effect=self._exec)

    def is_online(self, device):
        return device in self.online

    async def _exec(self, device, argv, **kwargs):
        if argv[:3] == [owner_login.CONNECTOR, "claude", "logout"]:
            if self.refuse_logout:
                return {"exit": 1, "stdout": "", "stderr": "claude: logout: busy"}
            self.logged_in[device] = False
            return {"exit": 0, "stdout": "", "stderr": ""}
        if argv[:3] == [owner_login.CONNECTOR, "claude", "login"]:
            self.logged_in[device] = True
            return {"exit": 0, "stdout": "", "stderr": ""}
        status = {"loggedIn": self.logged_in.get(device, False)}
        if status["loggedIn"]:
            status |= {"authMethod": "claude.ai", "subscriptionType": "max"}
        return {
            "exit": 0,
            "stdout": json.dumps({"installed": True, "status": status}),
        }

    def ran(self, device, argv):
        return any(
            call.args[0] == device and call.args[1][: len(argv)] == argv
            for call in self.exec.await_args_list
        )


def _laptop(client, owner: str) -> str:
    """A machine ``owner`` enrolled, logged in with their Claude subscription."""

    async def enroll():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == owner))
            devices = sql_device_service(db)
            code = await devices.start(f"{owner}-laptop")
            device = await devices.approve(
                code, owner_user_id=user.id, supply=Supply.self_hosted
            )
            db.add(
                DeviceClaudeLoginRow(
                    device_id=device.device_id,
                    installed=True,
                    logged_in=True,
                    auth_method="claude.ai",
                    subscription_type="max",
                    checked_at=datetime.now(UTC),
                )
            )
            await db.commit()
            return device.device_id

    return client.portal.call(enroll)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _connections(client, headers) -> list[dict]:
    response = client.get("/connector/my/model-connections", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["connections"]


def test_only_its_owner_sees_or_changes_a_connection(client, monkeypatch):
    alice, bob = _bearer(seed_user(client, "alice")), _bearer(seed_user(client, "bob"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.online.add(laptop)
    machines.logged_in[laptop] = True
    monkeypatch.setattr(connector, "device_hub", machines)

    assert [c["device_id"] for c in _connections(client, alice)] == [laptop]
    assert _connections(client, bob) == []

    base = f"/connector/my/devices/{laptop}/claude-code"
    assert client.delete(base, headers=bob).status_code == 404
    assert client.post(base, headers=bob).status_code == 404
    service = {"base_url": "https://kimi.test", "token": "sk-bob", "model": "k2"}
    assert (
        client.put(f"{base}/model-service", headers=bob, json=service).status_code
        == 404
    )

    machines.exec.assert_not_awaited()
    (still,) = _connections(client, alice)
    assert still["logged_in"] is True and still["revoking"] is False


def test_a_connection_removed_from_a_browser_logs_out_an_online_machine(
    client, monkeypatch
):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.online.add(laptop)
    machines.logged_in[laptop] = True
    monkeypatch.setattr(connector, "device_hub", machines)

    removed = client.delete(
        f"/connector/my/devices/{laptop}/claude-code", headers=alice
    )

    assert removed.status_code == 200, removed.text
    assert machines.ran(laptop, [owner_login.CONNECTOR, "claude", "logout"])
    assert _connections(client, alice) == []


def test_a_connection_removed_while_its_machine_is_away_is_logged_out_when_it_connects(
    client, monkeypatch
):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.logged_in[laptop] = True
    monkeypatch.setattr(connector, "device_hub", machines)

    removed = client.delete(
        f"/connector/my/devices/{laptop}/claude-code", headers=alice
    )

    assert removed.status_code == 200, removed.text
    (pending,) = _connections(client, alice)
    assert pending["revoking"] is True and pending["logged_in"] is False
    machines.exec.assert_not_awaited()

    machines.online.add(laptop)
    client.portal.call(
        owner_login.refresh_on_connect, client.test_request_factory, machines, laptop
    )

    assert machines.ran(laptop, [owner_login.CONNECTOR, "claude", "logout"])
    assert machines.logged_in[laptop] is False
    assert _connections(client, alice) == []


def test_a_model_services_key_goes_to_the_machine_and_is_not_kept(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.online.add(laptop)
    monkeypatch.setattr(connector, "device_hub", machines)
    key = "sk-only-on-the-laptop-" + uuid.uuid4().hex

    saved = client.put(
        f"/connector/my/devices/{laptop}/claude-code/model-service",
        headers=alice,
        json={"base_url": "https://kimi.test/anthropic", "token": key, "model": "k2"},
    )

    assert saved.status_code == 200, saved.text
    (handed,) = [
        call
        for call in machines.exec.await_args_list
        if call.args[1][:3] == [owner_login.CONNECTOR, "claude", "login"]
    ]
    assert handed.args[0] == laptop
    assert key in json.dumps(handed.kwargs.get("env") or {})
    assert key not in json.dumps(handed.args[1])
    assert key not in saved.text
    assert key not in json.dumps(_connections(client, alice))

    async def stored():
        async with client.test_factory() as db:
            row = await db.get(DeviceClaudeLoginRow, laptop)
            return json.dumps(
                {c.name: str(getattr(row, c.name)) for c in row.__table__.columns}
            )

    assert key not in client.portal.call(stored)


def test_a_model_service_needs_its_machine_online(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    monkeypatch.setattr(connector, "device_hub", machines)

    refused = client.put(
        f"/connector/my/devices/{laptop}/claude-code/model-service",
        headers=alice,
        json={"base_url": "https://kimi.test", "token": "sk-x", "model": "k2"},
    )

    assert refused.status_code == 409
    machines.exec.assert_not_awaited()


# --- a removed connection starts nothing -------------------------------------


def _room(client):
    project = post_project(client, json={"name": "Own"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    message = post_message(client, room["id"], "alice", {"content": "hello"})
    return uuid.UUID(project["id"]), uuid.UUID(room["id"]), uuid.UUID(message["id"])


async def _own_agent(factory, project_id, topic_id):
    async with factory() as db:
        alice = await db.scalar(select(User).where(User.username == "alice"))
        instance = AgentInstance(
            project_id=project_id,
            handle=f"own-{uuid.uuid4().hex[:8]}",
            configuration={},
            display_name="alice的 Claude Code",
        )
        db.add(instance)
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=instance.id, owner_user_id=alice.id, harness="claude-code"
            )
        )
        seat = await AgentInstanceService(db).ensure_identity(instance)
        await TopicMemberService(db).ensure_agent_seat(topic_id, seat)
        await db.commit()
        return instance.id


async def _waits(chat, topic_id, block_id, instance_id) -> bool:
    """Whether a message to the agent waits, rather than starting its turn."""
    async with initial_admission.admitted_initial(
        chat.session_factory,
        chat.live.seat_lock_for,
        chat._turn_seat_handle,
        chat.post_system_event,
        topic_id,
        None,
        user_block_id=block_id,
        recipient_instance_id=instance_id,
    ) as held:
        return held


def test_a_removed_connection_starts_no_session(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    project_id, topic_id, block_id = _room(client)
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.logged_in[laptop] = True
    monkeypatch.setattr(connector, "device_hub", machines)
    monkeypatch.setattr(initial_admission, "device_hub", machines)

    assert (
        client.delete(
            f"/connector/my/devices/{laptop}/claude-code", headers=alice
        ).status_code
        == 200
    )
    # The machine comes back and fails to log out: it still holds the login,
    # and says so when asked.
    machines.online.add(laptop)
    machines.refuse_logout = True
    client.portal.call(
        owner_login.refresh_on_connect, client.test_request_factory, machines, laptop
    )
    assert machines.logged_in[laptop] is True
    # Asking it again from the page changes nothing either.
    recheck = client.post(f"/connector/my/devices/{laptop}/claude-code", headers=alice)
    assert recheck.status_code == 200, recheck.text
    (still,) = _connections(client, alice)
    assert still["revoking"] is True and still["logged_in"] is False

    async def written_back_by_an_older_backend():
        # The device-connection owner outlives an app release: a backend that
        # predates removals stores what the machine said, logged in, and knows
        # nothing of the removal.
        async with client.test_factory() as db:
            row = await db.get(DeviceClaudeLoginRow, laptop)
            row.logged_in = True
            row.checked_at = datetime.now(UTC)
            await db.commit()

    client.portal.call(written_back_by_an_older_backend)

    async def run():
        factory = client.test_request_factory
        instance_id = await _own_agent(factory, project_id, topic_id)
        chat = client.app.dependency_overrides[get_chat_service]()
        assert await _waits(chat, topic_id, block_id, instance_id) is True
        async with factory() as db:
            blocks = await db.scalars(
                select(Block).where(Block.conversation_id == topic_id)
            )
            assert [b for b in blocks if "电脑上线后自动继续" in (b.content or "")]

    client.portal.call(run)
