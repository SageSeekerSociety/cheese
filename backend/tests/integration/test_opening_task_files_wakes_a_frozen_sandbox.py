"""A person opening or saving a task's files wakes its frozen sandbox.

A frozen sandbox's executor is stopped with everything else in it, so a file
request sent to it waits out its deadline. Reading or saving a task's files is
using the sandbox, as opening its preview is: the frozen one is thawed first,
and its idle clock starts again, so it is not frozen again under the reader.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import lifecycle
from app.domain.machine.models import AiStatus, CloudHost, MachineStatus, SandboxHome
from app.domain.machine.preview_wake import wake_for_files
from tests.integration.test_app_preview_proxy import _project_topic, _room_agent
from tests.integration.test_app_preview_proxy import preview_config as preview_config


def test_a_frozen_sandbox_is_thawed_before_its_files_are_read(
    client, preview_config, monkeypatch
):
    _, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    seat = _room_agent(client, topic_id)
    stale = datetime.now(UTC) - timedelta(minutes=15)

    async def _seed() -> tuple[uuid.UUID, str]:
        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic_id, seat, harness="claude-code"
            )
            host = CloudHost(
                customer_id=1,
                account_id=1,
                offering_id=1,
                hostname=f"host-{uuid.uuid4().hex[:8]}",
                login_user="cheese",
                cores=2,
                memory_mb=4096,
                disk_gb=20,
                device_id="box-1",
                status=MachineStatus.running,
                ai_mode="none",
                ai_status=AiStatus.disabled,
                bootstrap_key="k",
            )
            db.add(host)
            await db.flush()
            home = SandboxHome(
                host_id=host.id,
                topic_id=topic_id,
                room_resource_id=str(topic_id),
                resource_id=str(uuid.uuid4()),
                session_id=row.id,
                device_id=None,
                spec="standard",
                active_at=stale,
                frozen_at=stale,
            )
            db.add(home)
            await db.commit()
            return home.id, home.resource_id

    home_id, resource = client.portal.call(_seed)
    thawed: list[tuple[str, str]] = []

    async def _thaw_on_host(hub, device_id, work_resource):
        thawed.append((device_id, work_resource))

    monkeypatch.setattr(lifecycle, "thaw_on_host", _thaw_on_host)

    async def _open():
        async with client.test_request_factory() as db:
            bind = db.bind
        await wake_for_files(bind, topic_id)
        async with client.test_request_factory() as db:
            home = await db.get(SandboxHome, home_id)
            return home.frozen_at, home.active_at

    frozen_at, active_at = client.portal.call(_open)
    assert thawed == [("box-1", resource)]
    assert frozen_at is None
    assert active_at > stale


def test_a_sandbox_that_is_not_frozen_is_left_as_it_is(
    client, preview_config, monkeypatch
):
    _, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    thawed: list[str] = []

    async def _thaw_on_host(hub, device_id, work_resource):
        thawed.append(device_id)

    monkeypatch.setattr(lifecycle, "thaw_on_host", _thaw_on_host)

    async def _open():
        async with client.test_request_factory() as db:
            bind = db.bind
        await wake_for_files(bind, topic_id)

    client.portal.call(_open)
    assert thawed == []
