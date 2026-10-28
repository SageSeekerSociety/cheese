"""Personal subscriptions are managed from Models independently of execution devices.

Only the owner manages their personal-team connections. Removing a connection
immediately prevents new sessions from using it; an offline computer logs it out
when it next connects. A status probe never registers an unknown login or assigns
its team.
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service
from app.api.routes.connector import connections
from app.domain.agent import initial_admission
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code import owner_login
from app.domain.agent.owner_provider import OwnerChannel
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.team.services import team_service
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)
from tests.support.devices import enrollment_team


class _Machines:
    """The device hub as these rules see it: which machines are online, and
    what each one answers. A machine holds logins by the name it gives them;
    a logged-out one says so when asked, and one logged out of anything but
    the first is gone from it."""

    def __init__(self):
        self.online: set[str] = set()
        #: machine -> its logins, by local id -> logged in.
        self.logins: dict[str, dict[str, bool]] = {}
        #: machine -> local id -> the Claude account (email, org) logged in.
        self.accounts: dict[str, dict[str, tuple[str, str]]] = {}
        self.refuse_logout = False
        self.exec = AsyncMock(side_effect=self._exec)

    def is_online(self, device):
        return device in self.online

    def all_online_screens(self):
        return []

    def target(self, device):
        return ""

    def holds(self, device, *local_ids: str):
        self.logins.setdefault(device, {}).update(dict.fromkeys(local_ids, True))

    async def _exec(self, device, argv, **kwargs):
        logins = self.logins.setdefault(device, {})
        if argv[:3] == [owner_login.CONNECTOR, "claude", "logout"]:
            if self.refuse_logout:
                return {"exit": 1, "stdout": "", "stderr": "claude: logout: busy"}
            if argv[3] == FIRST_LOGIN:
                logins[FIRST_LOGIN] = False
            else:
                logins.pop(argv[3], None)
            return {"exit": 0, "stdout": "", "stderr": ""}
        if argv[:3] == [owner_login.CONNECTOR, "claude", "login"]:
            logins[argv[argv.index("--login") + 1]] = True
            return {"exit": 0, "stdout": "", "stderr": ""}
        reports = []
        for local_id, logged_in in {FIRST_LOGIN: False, **logins}.items():
            status = {"loggedIn": logged_in}
            if logged_in:
                status |= {"authMethod": "claude.ai", "subscriptionType": "max"}
                email, org = self.accounts.get(device, {}).get(local_id, (None, None))
                status |= {"email": email, "orgName": org}
            reports.append({"id": local_id, "status": status})
        return {
            "exit": 0,
            "stdout": json.dumps({"installed": True, "logins": reports}),
        }

    def ran(self, device, argv):
        return any(
            call.args[0] == device and call.args[1][: len(argv)] == argv
            for call in self.exec.await_args_list
        )


def _laptop(client, owner: str, *more: str) -> str:
    """A machine ``owner`` enrolled, logged in with their Claude subscription
    (its first login), and holding the further logins ``more`` as well."""

    async def enroll():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == owner))
            devices = sql_device_service(db)
            code = await devices.start(f"{owner}-laptop")
            device = await devices.approve(
                code,
                owner_user_id=user.id,
                supply=Supply.self_hosted,
                team_id=await enrollment_team(devices, user.id),
            )
            for local_id in (FIRST_LOGIN, *more):
                db.add(
                    DeviceClaudeLoginRow(
                        team_id=device.team_id,
                        owner_user_id=user.id,
                        allowed_models=["sonnet", "opus", "haiku"],
                        device_id=device.device_id,
                        local_id=local_id,
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


def team_id(client, owner="alice"):
    async def read():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == owner))
            team = await team_service(db).ensure_personal_team(user.id)
            await db.commit()
            return team.id

    return client.portal.call(read)


def _connections(client, headers, owner="alice") -> list[dict]:
    response = client.get(
        f"/connector/teams/{team_id(client, owner)}/model-connections", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["connections"]


def test_only_its_owner_sees_or_changes_a_connection(client, monkeypatch):
    alice, bob = _bearer(seed_user(client, "alice")), _bearer(seed_user(client, "bob"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.online.add(laptop)
    machines.holds(laptop, FIRST_LOGIN)
    monkeypatch.setattr(connections, "device_hub", machines)

    assert [c["device_id"] for c in _connections(client, alice)] == [laptop]
    assert _connections(client, bob, "bob") == []

    login = f"/connector/teams/{team_id(client)}/claude-logins/{laptop}"
    assert client.delete(login, headers=bob).status_code == 403
    assert client.post(f"{login}/check", headers=bob).status_code == 403
    assert client.patch(login, headers=bob, json={"name": "x"}).status_code == 403

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
    machines.holds(laptop, FIRST_LOGIN)
    monkeypatch.setattr(connections, "device_hub", machines)

    removed = client.delete(
        f"/connector/teams/{team_id(client)}/claude-logins/{laptop}", headers=alice
    )

    assert removed.status_code == 200, removed.text
    assert machines.ran(
        laptop, [owner_login.CONNECTOR, "claude", "logout", FIRST_LOGIN]
    )
    assert _connections(client, alice) == []


def test_a_connection_removed_while_its_machine_is_away_is_logged_out_when_it_connects(
    client, monkeypatch
):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.holds(laptop, FIRST_LOGIN)
    monkeypatch.setattr(connections, "device_hub", machines)

    removed = client.delete(
        f"/connector/teams/{team_id(client)}/claude-logins/{laptop}", headers=alice
    )

    assert removed.status_code == 200, removed.text
    (pending,) = _connections(client, alice)
    assert pending["revoking"] is True and pending["logged_in"] is False
    machines.exec.assert_not_awaited()

    machines.online.add(laptop)
    client.portal.call(
        owner_login.refresh_on_connect, client.test_request_factory, machines, laptop
    )

    assert machines.ran(
        laptop, [owner_login.CONNECTOR, "claude", "logout", FIRST_LOGIN]
    )
    assert machines.logins[laptop][FIRST_LOGIN] is False
    assert _connections(client, alice) == []


def _room(client):
    project = post_project(client, json={"name": "Own"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    message = post_message(client, room["id"], "alice", {"content": "hello"})
    return uuid.UUID(project["id"]), uuid.UUID(room["id"]), uuid.UUID(message["id"])


async def _own_agent(factory, project_id, topic_id, login_id):
    """Alice's own agent, added with one of her logins; its id and handle."""
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
                instance_id=instance.id,
                owner_user_id=alice.id,
                harness="claude-code",
                login_id=login_id,
            )
        )
        seat = await AgentInstanceService(db).ensure_identity(instance)
        await TopicMemberService(db).ensure_agent_seat(topic_id, seat)
        await db.commit()
        return instance.id, instance.handle


async def _waits(chat, topic_id, block_id, instance_id) -> bool:
    """Whether a message to the agent waits, rather than starting its turn."""
    async with initial_admission.admitted_initial(
        chat.session_factory,
        chat.live.seat_lock_for,
        chat._turn_seat_handle,
        chat.post_system_event,
        topic_id,
        None,
        draining=chat,
        user_block_id=block_id,
        recipient_instance_id=instance_id,
    ) as held:
        return held


def test_a_removed_connection_starts_no_session(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    project_id, topic_id, block_id = _room(client)
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.holds(laptop, FIRST_LOGIN)
    monkeypatch.setattr(connections, "device_hub", machines)
    monkeypatch.setattr(initial_admission, "device_hub", machines)

    assert (
        client.delete(
            f"/connector/teams/{team_id(client)}/claude-logins/{laptop}", headers=alice
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
    assert machines.logins[laptop][FIRST_LOGIN] is True
    # Asking it again from the page changes nothing either.
    recheck = client.post(
        f"/connector/teams/{team_id(client)}/claude-logins/{laptop}/check",
        headers=alice,
    )
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
        instance_id, handle = await _own_agent(factory, project_id, topic_id, laptop)
        chat = client.app.dependency_overrides[get_chat_service]()
        # Not waiting for a machine: the connection is gone, so the turn is
        # refused with the reason instead of starting there.
        assert await _waits(chat, topic_id, block_id, instance_id) is False
        channel = OwnerChannel(DeviceChannel(hub=machines, session_factory=factory))
        with pytest.raises(ScreenSetupError) as refused:
            await channel.precheck(
                SessionRef(project_id, topic_id, handle, harness="claude-code"),
                needs_place=False,
            )
        assert str(refused.value), "the caller receives a reason for refusing to start"

    client.portal.call(run)


# --- several logins on one machine --------------------------------------------


def _login_id(client, laptop, local_id) -> str:
    async def read():
        async with client.test_factory() as db:
            return await db.scalar(
                select(DeviceClaudeLoginRow.id).where(
                    DeviceClaudeLoginRow.device_id == laptop,
                    DeviceClaudeLoginRow.local_id == local_id,
                )
            )

    return client.portal.call(read)


def test_each_login_on_one_machine_is_a_connection_of_its_own(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice", "B7QX")
    monkeypatch.setattr(connections, "device_hub", _Machines())

    listed = _connections(client, alice)

    assert [c["device_id"] for c in listed] == [laptop, laptop]
    assert len({c["id"] for c in listed}) == 2
    # The login the machine already had keeps the id it was known by.
    assert {c["id"] for c in listed} == {laptop, _login_id(client, laptop, "B7QX")}


def test_removing_one_login_leaves_the_other_on_the_same_machine(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice", "B7QX")
    machines = _Machines()
    machines.online.add(laptop)
    machines.holds(laptop, FIRST_LOGIN, "B7QX")
    monkeypatch.setattr(connections, "device_hub", machines)
    second = _login_id(client, laptop, "B7QX")

    removed = client.delete(
        f"/connector/teams/{team_id(client)}/claude-logins/{second}", headers=alice
    )

    assert removed.status_code == 200, removed.text
    assert machines.ran(laptop, [owner_login.CONNECTOR, "claude", "logout", "B7QX"])
    assert not machines.ran(
        laptop, [owner_login.CONNECTOR, "claude", "logout", FIRST_LOGIN]
    )
    (left,) = _connections(client, alice)
    assert left["id"] == laptop
    assert left["logged_in"] is True and left["revoking"] is False

    # Asked again, the machine still holds the first one only.
    recheck = client.post(
        f"/connector/teams/{team_id(client)}/claude-logins/{laptop}/check",
        headers=alice,
    )
    assert recheck.status_code == 200, recheck.text
    assert recheck.json()["logged_in"] is True
    assert [c["id"] for c in _connections(client, alice)] == [laptop]


def test_a_probe_does_not_assign_a_team_to_an_unregistered_login(client, monkeypatch):
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice")
    machines = _Machines()
    machines.online.add(laptop)
    machines.holds(laptop, FIRST_LOGIN, "C2QR")
    monkeypatch.setattr(connections, "device_hub", machines)

    path = f"/connector/teams/{team_id(client)}/claude-logins/{laptop}/check"
    asked = client.post(path, headers=alice)

    assert asked.status_code == 200, asked.text
    assert [c["local_id"] for c in _connections(client, alice)] == [FIRST_LOGIN]

    # Logged out and removed on the machine itself: it is no connection now.
    del machines.logins[laptop]["C2QR"]
    client.post(path, headers=alice)
    assert [c["local_id"] for c in _connections(client, alice)] == [FIRST_LOGIN]


def test_each_login_names_the_claude_account_it_is(client, monkeypatch):
    """Two logins on one computer are told apart by the Claude account each
    one is, as the computer reports it on every check."""
    alice = _bearer(seed_user(client, "alice"))
    laptop = _laptop(client, "alice", "B7QX")
    machines = _Machines()
    machines.online.add(laptop)
    machines.holds(laptop, FIRST_LOGIN, "B7QX")
    machines.accounts[laptop] = {
        FIRST_LOGIN: ("alice@example.com", "Alice's Organization"),
        "B7QX": ("alice@work.example", "Work Inc"),
    }
    monkeypatch.setattr(connections, "device_hub", machines)

    path = f"/connector/teams/{team_id(client)}/claude-logins/{laptop}/check"
    checked = client.post(path, headers=alice)

    assert checked.status_code == 200, checked.text
    accounts = {
        c["local_id"]: (c["account_email"], c["account_org"])
        for c in _connections(client, alice)
    }
    assert accounts == {
        FIRST_LOGIN: ("alice@example.com", "Alice's Organization"),
        "B7QX": ("alice@work.example", "Work Inc"),
    }

    # Logged in again there with another account: the next check says so.
    machines.accounts[laptop]["B7QX"] = ("bob@work.example", "Work Inc")
    assert client.post(path, headers=alice).status_code == 200
    second = next(c for c in _connections(client, alice) if c["local_id"] == "B7QX")
    assert second["account_email"] == "bob@work.example"
