"""CloudProvider readiness and topic-owned endpoint resolution."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.cloud_provider import CloudLease, CloudProvider
from app.domain.agent.hooks_substrate import ScreenSetupError
from app.domain.device.supply import Supply
from app.domain.identity.actor import Actor

pytestmark = pytest.mark.anyio


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None


class _Hub:
    def __init__(self, online: set[str]):
        self.online = online

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online


async def test_running_machine_waits_until_ai_and_connector_are_ready():
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    actor = Actor("owner", 1, False, "token")
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
    provider = CloudProvider(
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
    provider = CloudProvider(
        session_factory=_Session,
        hub=_Hub({"own-cloud"}),
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(return_value=lease),
    )

    with pytest.raises(ScreenSetupError, match="拒绝借用"):
        await provider._resolve_device_agent(project_id, topic_id)


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
    provider = CloudProvider(
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
