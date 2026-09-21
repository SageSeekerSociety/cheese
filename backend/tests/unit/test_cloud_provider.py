"""CloudChannel readiness and topic-owned endpoint resolution."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.deps import _cloud_lease
from app.domain.agent.cloud_provider import CloudChannel, CloudLease
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.device.supply import Supply, Visibility
from app.domain.identity.actor import Actor
from app.domain.machine.models import AiStatus, MachineStatus

pytestmark = pytest.mark.anyio


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        # No `tasks` row by this id: the place is a room, and its machine is
        # its own.
        return None

    async def commit(self):
        return None


class _Hub:
    def __init__(self, online: set[str]):
        self.online = online

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online


async def test_running_machine_waits_until_ai_and_connector_are_ready():
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    actor = Actor("owner", 1, "token")
    provisioning = CloudLease(
        project_id=project_id,
        device_id="cloud-1",
        machine_ready=True,
        ai_ready=False,
    )
    ready_lease = CloudLease(
        project_id=project_id,
        device_id="cloud-1",
        machine_ready=True,
        ai_ready=True,
    )
    ensure = AsyncMock(side_effect=[provisioning, ready_lease])
    provider = CloudChannel(
        session_factory=_Session,
        hub=_Hub({"cloud-1"}),
        configured=True,
        ensure_topic_cloud=ensure,
        read_topic_cloud=AsyncMock(),
    )

    ready, message = await provider.prepare_topic(
        project_id=project_id, topic_id=topic_id, actor=actor
    )

    assert ready is False
    assert "正在创建" in message
    ensure.assert_awaited_once_with(topic_id, actor)

    ready, _ = await provider.prepare_topic(
        project_id=project_id, topic_id=topic_id, actor=None
    )
    assert ready is True


async def test_cloud_resolution_rejects_another_topics_endpoint(monkeypatch):
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    lease = CloudLease(project_id, "own-cloud", True, True)

    devices = SimpleNamespace(
        get_device=AsyncMock(
            return_value=SimpleNamespace(device_id="own-cloud", supply=Supply.cloud)
        ),
        topic_binding=AsyncMock(
            return_value=SimpleNamespace(device_id="another-topics-cloud")
        ),
    )
    monkeypatch.setattr(
        "app.domain.agent.cloud_provider.sql_device_service", lambda _session: devices
    )
    provider = CloudChannel(
        session_factory=_Session,
        hub=_Hub({"own-cloud"}),
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(return_value=lease),
    )

    with pytest.raises(ScreenSetupError, match="拒绝借用"):
        await provider._resolve_device_agent(project_id, topic_id)


async def test_a_ready_cloud_machine_gets_through_precheck(monkeypatch):
    """机器接上了就要真的走完 precheck——那是每一轮 cloud 对话的第一步。

    `prepare_topic` 在「机器还在创建」就停了，从没走到这里；而这里是
    `CentralChannel` 与 `PiChannel` 每一轮都要调的那一个入口。签名对不上就是每
    一轮都在第一步炸掉，而且因为没有一条用例等到机器 ready，CI 会全绿地放它出去。
    """
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()

    devices = SimpleNamespace(
        get_device=AsyncMock(
            return_value=SimpleNamespace(device_id="own-cloud", supply=Supply.cloud)
        ),
        topic_binding=AsyncMock(return_value=None),
        # 绑定时的可见性档由供给决定，绑定点只是问一句——所以这个替身也要答得出。
        binding_visibility=AsyncMock(return_value=Visibility.host),
        bind_topic_device=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.domain.agent.cloud_provider.sql_device_service", lambda _session: devices
    )
    monkeypatch.setattr(
        "app.domain.agent.cloud_provider.IdentityService",
        lambda _session: SimpleNamespace(
            ensure_room_agent_user=AsyncMock(
                return_value=SimpleNamespace(id=7, username="cheese-room")
            )
        ),
    )
    provider = CloudChannel(
        session_factory=_Session,
        hub=_Hub({"own-cloud"}),
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(
            return_value=CloudLease(project_id, "own-cloud", True, True)
        ),
    )

    resolved = await provider.precheck(
        SessionRef(project_id, topic_id, "ada", "claude-code"), needs_place=True
    )

    assert resolved == ("own-cloud", 7, "cheese-room", True)
    devices.bind_topic_device.assert_awaited_once()


async def test_cloud_resolution_never_accepts_a_hosted_endpoint(monkeypatch):
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()

    devices = SimpleNamespace(
        get_device=AsyncMock(
            return_value=SimpleNamespace(
                device_id="hosted-1", supply=Supply.self_hosted
            )
        )
    )
    monkeypatch.setattr(
        "app.domain.agent.cloud_provider.sql_device_service", lambda _session: devices
    )
    provider = CloudChannel(
        session_factory=_Session,
        hub=_Hub({"hosted-1"}),
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(
            return_value=CloudLease(project_id, "hosted-1", True, True)
        ),
    )

    with pytest.raises(ScreenSetupError, match="有效的云端连接器"):
        await provider._resolve_device_agent(project_id, topic_id)


@pytest.mark.parametrize(
    ("ai_status", "ready"),
    [
        (AiStatus.ready, True),
        # The built-in channel switched off because the machine reaches the
        # model through the gateway: the same settled state the wake-up sweep
        # hands out, so the turn it wakes must agree that the machine is ready.
        (AiStatus.disabled, True),
        (AiStatus.provisioning, False),
        (AiStatus.unknown, False),
    ],
)
def test_a_running_machine_is_ready_once_its_ai_channel_has_settled(ai_status, ready):
    machine = SimpleNamespace(
        project_id=uuid.uuid4(),
        device_id="cloud-1",
        status=MachineStatus.running,
        ai_status=ai_status,
    )
    lease = _cloud_lease(machine)  # type: ignore[arg-type]
    assert lease.machine_ready and lease.error is None
    assert lease.ai_ready is ready
