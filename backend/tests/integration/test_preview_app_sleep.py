"""A previewed app whose machine sleeps: its kept snapshot, waking it, and the steps.

The panel shows the last page that loaded while the app cannot be reached, and a
button that asks the teammate who declared the app to bring it back. The tunnel
here is the real hub with a fake machine on the other end; the agent's turn is
recorded instead of run.
"""

import uuid

import pytest
from sqlalchemy import select

from app.api.deps import get_work_runner
from app.api.preview_runtime import SNAPSHOT_CSP
from app.api.routes import topics_preview_app
from app.core.redis import get_redis_client
from tests.integration.conftest import session_auth_headers
from tests.integration.test_app_preview_proxy import (
    FakeMachine,
    _project_topic,
    _room_agent,
)
from tests.integration.test_app_preview_proxy import preview_config as preview_config

ALICE = session_auth_headers("alice")


class RecordingRunner:
    """Stands in for the work runner: the turn a wake starts, written down."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit(self, chat, topic_id, **kwargs):
        self.submitted.append({"topic_id": topic_id, **kwargs})
        return uuid.uuid4()


@pytest.fixture
def runner(client):
    recording = RecordingRunner()
    client.app.dependency_overrides[get_work_runner] = lambda: recording
    yield recording
    client.app.dependency_overrides.pop(get_work_runner, None)


def _declare(client, topic_id, path="npm run dev") -> str:
    shown = client.post(
        f"/topics/{topic_id}/shown", json={"path": path, "as": "app"}, headers=ALICE
    )
    assert shown.status_code == 200, shown.text
    return client.get(f"/topics/{topic_id}/preview", headers=ALICE).json()["data"][
        "artifact_id"
    ]


def _room(client):
    _, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    return topic_id, _room_agent(client, topic_id)


def test_a_snapshot_is_kept_per_app_and_read_back_by_the_room(client, preview_config):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        artifact = _declare(client, topic_id)
    finally:
        machine.detach()
    url = f"/topics/{topic_id}/preview/snapshot"
    assert client.get(url, headers=ALICE).json()["data"] is None
    page = "<!DOCTYPE html><html><body>第 2 步</body></html>"
    kept = client.put(
        url,
        json={
            "artifact_id": artifact,
            "html": page,
            "href": "https://x/signup",
            "title": "报名",
        },
        headers=ALICE,
    )
    assert kept.status_code == 200, kept.text
    read = client.get(url, headers=ALICE).json()["data"]
    # 平台自己盖上了那条 CSP，而且盖在文档内容之前（CSP 只管它后面的东西）：
    # 写快照的人自己带不带都不算数。
    assert SNAPSHOT_CSP in read["html"]
    assert read["html"].index(SNAPSHOT_CSP) < read["html"].index("第 2 步")
    assert read["href"] == "https://x/signup"
    assert read["title"] == "报名" and read["taken_at"] > 0

    # 同一个应用再声明一次（新的一行 artifact）：快照还在。
    machine = FakeMachine().attach(topic_id, seat)
    try:
        again = _declare(client, topic_id)
        assert again != artifact
        assert client.get(url, headers=ALICE).json()["data"]["html"] == read["html"]
        # 写给旧那一行的快照不算数：预览已经换过了。
        stale = client.put(
            url, json={"artifact_id": artifact, "html": page}, headers=ALICE
        )
        assert stale.status_code == 422, stale.text
        # 换成另一个应用：不继承别人的快照。
        _declare(client, topic_id, path="python -m http.server")
        assert client.get(url, headers=ALICE).json()["data"] is None
    finally:
        machine.detach()


def test_an_oversized_snapshot_or_a_file_preview_is_refused(client, preview_config):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        artifact = _declare(client, topic_id)
    finally:
        machine.detach()
    url = f"/topics/{topic_id}/preview/snapshot"
    big = "x" * (topics_preview_app.SNAPSHOT_MAX_BYTES + 1)
    refused = client.put(
        url, json={"artifact_id": artifact, "html": big}, headers=ALICE
    )
    assert refused.status_code == 422, refused.text
    # 一个外人读不到这间房的快照。
    outsider = client.get(url, headers=session_auth_headers("mallory"))
    assert outsider.status_code in (403, 404), outsider.text


def test_a_snapshot_the_bridge_accepted_is_not_refused_for_the_meta_we_add(
    client, preview_config
):
    """The cap is measured on the html, before the platform stamps its CSP.

    The bridge refuses anything over the same number before it uploads, so an
    html it accepted must be storable. Measuring the sealed bytes instead leaves
    a band of ~140 bytes that the bridge accepts and the platform then refuses —
    and the caller swallows that error, so the room quietly loses the snapshot.
    """
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        artifact = _declare(client, topic_id)
    finally:
        machine.detach()
    url = f"/topics/{topic_id}/preview/snapshot"
    at_cap = "x" * topics_preview_app.SNAPSHOT_MAX_BYTES
    saved = client.put(
        url, json={"artifact_id": artifact, "html": at_cap}, headers=ALICE
    )
    assert saved.status_code == 200, saved.text
    stored = client.get(url, headers=ALICE).json()["data"]
    # Sealed anyway: the stored copy carries the platform's CSP.
    assert stored["html"].startswith('<meta http-equiv="Content-Security-Policy"')
    assert f'content="{SNAPSHOT_CSP}"' in stored["html"]


def test_waking_a_sleeping_app_asks_its_teammate_once(client, preview_config, runner):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id)
        # 应用还答着：不必叫醒谁。
        up = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
        assert up.json()["data"] == {"started": False, "reason": "up"}, up.text
        status = client.get(f"/topics/{topic_id}/preview/wake-status", headers=ALICE)
        assert status.json()["data"]["tunnel_up"] is True
        assert status.json()["data"]["app_up"] is True
    finally:
        machine.detach()
    assert runner.submitted == []

    # 电脑睡了：通道断开。
    status = client.get(f"/topics/{topic_id}/preview/wake-status", headers=ALICE)
    assert status.status_code == 200, status.text
    assert status.json()["data"] == {
        "machine": None,
        "tunnel_up": False,
        "agent_working": False,
        "app_up": False,
    }
    woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
    assert woke.status_code == 200, woke.text
    assert woke.json()["data"]["started"] is True
    [turn] = runner.submitted
    assert turn["author"] == "system"
    assert "npm run dev" in turn["content"] and "cheese serve" in turn["content"]
    assert turn["nudge_event"].key == "previewWakeRequested"
    assert seat in str(turn["addressed"])
    if get_redis_client() is not None:
        # 紧接着再按一次：不再起第二轮。
        twice = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
        assert twice.json()["data"] == {"started": False, "reason": "pending"}
        assert len(runner.submitted) == 1


def test_waking_needs_a_member_who_may_ask_the_agent(client, preview_config, runner):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id)
    finally:
        machine.detach()
    refused = client.post(
        f"/topics/{topic_id}/preview/wake", headers=session_auth_headers("mallory")
    )
    assert refused.status_code in (403, 404), refused.text
    assert runner.submitted == []


def _seed_sandbox(client, topic_id, seat) -> uuid.UUID:
    """A session whose sandbox is on a platform host, as the pool leaves it."""

    async def _seed() -> uuid.UUID:
        from datetime import UTC, datetime, timedelta

        from app.domain.agent_session.services import AgentSessionService
        from app.domain.machine.models import (
            AiStatus,
            CloudHost,
            MachineStatus,
            SandboxHome,
        )

        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic_id, seat, harness="claude-code"
            )
            row.runtime_location = {
                "device_id": "center",
                "resource_id": str(topic_id),
                "channel": "device",
            }
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
                active_at=datetime.now(UTC) - timedelta(minutes=15),
            )
            db.add(home)
            await db.commit()
            return row.id

    return client.portal.call(_seed)


def _freeze(client, session_id) -> str:
    """The session's home frozen, as the sweep freezes it. Returns its resource."""

    async def _do() -> str:
        from datetime import UTC, datetime, timedelta

        from app.domain.machine.models import SandboxHome

        async with client.test_request_factory() as db:
            home = await db.scalar(
                select(SandboxHome).where(SandboxHome.session_id == session_id)
            )
            home.frozen_at = datetime.now(UTC) - timedelta(minutes=5)
            home.active_at = datetime.now(UTC) - timedelta(minutes=15)
            await db.commit()
            return str(home.resource_id)

    return client.portal.call(_do)


def test_waking_an_app_whose_sandbox_still_exists_thaws_it_without_a_model_turn(
    client, preview_config, runner, monkeypatch
):
    """The sandbox is still there, frozen: waking thaws it in place through the
    same path a tool call's wake takes, and the app goes on running. No launch
    command is run and no agent turn is spent."""
    import time

    from app.domain.agent import execution
    from app.domain.machine import lifecycle

    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id, path="npm run dev")
    finally:
        machine.detach()
    session_id = _seed_sandbox(client, topic_id, seat)
    resource = _freeze(client, session_id)
    thawed: list[tuple[str, str]] = []

    async def _fake_thaw(hub, device_id, work_resource):
        thawed.append((device_id, work_resource))

    async def _no_command(*_args, **_kwargs):
        raise AssertionError("waking a live sandbox must not run a launch command")

    monkeypatch.setattr(lifecycle, "thaw_on_host", _fake_thaw)
    monkeypatch.setattr(execution, "call", _no_command)

    woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
    assert woke.status_code == 200, woke.text
    assert woke.json()["data"] == {"started": True}
    deadline = time.monotonic() + 5.0
    while not thawed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert thawed == [("box-1", resource)]
    assert runner.submitted == []


def test_waking_falls_back_to_agent_turn_when_the_sandbox_cannot_come_up(
    client, preview_config, runner, monkeypatch
):
    """If the sandbox cannot be brought up, the user is not stuck: the teammate
    gets a turn to start the app again."""
    import time

    from app.domain.machine import lifecycle

    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id, path="npm run dev")
    finally:
        machine.detach()
    session_id = _seed_sandbox(client, topic_id, seat)
    _freeze(client, session_id)

    async def _failing_thaw(hub, device_id, work_resource):
        raise lifecycle.SandboxHomeError("no such sandbox")

    monkeypatch.setattr(lifecycle, "thaw_on_host", _failing_thaw)

    woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
    assert woke.status_code == 200, woke.text
    assert woke.json()["data"] == {"started": True}

    deadline = time.monotonic() + 5.0
    while not runner.submitted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(runner.submitted) == 1
    assert "npm run dev" in runner.submitted[0]["content"]


def test_browsing_an_app_preview_refreshes_sandbox_idle_clock(client, preview_config):
    """Browsing a previewed app moves its session's `SandboxHome.active_at`
    forward (throttled to `ACTIVE_STEP`) so the idle sweeper does not reclaim
    the sandbox while someone is using the preview."""
    from datetime import UTC, datetime, timedelta

    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine.models import (
        AiStatus,
        CloudHost,
        MachineStatus,
        SandboxHome,
    )
    from app.domain.machine.preview_wake import (
        clear_preview_touch_cache,
        touch_preview_activity,
    )

    topic_id, seat = _room(client)
    clear_preview_touch_cache()
    stale = datetime.now(UTC) - timedelta(minutes=15)

    async def _seed_home() -> tuple[uuid.UUID, uuid.UUID]:
        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic_id, seat, harness="claude-code"
            )
            other_row = await AgentSessionService(db).ensure(
                topic_id, "other-seat", harness="claude-code"
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
            )
            other_home = SandboxHome(
                host_id=host.id,
                topic_id=topic_id,
                room_resource_id=str(topic_id),
                resource_id=str(uuid.uuid4()),
                session_id=other_row.id,
                device_id=None,
                spec="standard",
                active_at=stale,
            )
            db.add_all([home, other_home])
            await db.commit()
            return home.id, other_home.id

    home_id, other_home_id = client.portal.call(_seed_home)

    async def _touch_and_read() -> tuple[datetime, datetime, datetime]:
        async with client.test_request_factory() as db:
            await touch_preview_activity(db, topic_id, seat)
            first = (await db.get(SandboxHome, home_id)).active_at
            other = (await db.get(SandboxHome, other_home_id)).active_at
            # Move it back in DB without clearing the in-memory throttle: a
            # second call within ACTIVE_STEP must skip the DB write.
            home = await db.get(SandboxHome, home_id)
            home.active_at = stale
            await db.commit()
            await touch_preview_activity(db, topic_id, seat)
            second = (await db.get(SandboxHome, home_id)).active_at
            return first, second, other

    first, second, other = client.portal.call(_touch_and_read)
    assert first > stale + timedelta(minutes=10)
    assert second == stale
    assert other == stale


def test_opening_a_frozen_sandboxes_preview_thaws_it(
    client, preview_config, monkeypatch
):
    """Browsing a preview is using the sandbox: one found frozen is thawed in
    the background, so the app somebody opened goes on running."""
    from datetime import UTC, datetime, timedelta

    from app.domain.agent_session.models import THAW_KEY
    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine import lifecycle
    from app.domain.machine.models import (
        AiStatus,
        CloudHost,
        MachineStatus,
        SandboxHome,
    )
    from app.domain.machine.preview_wake import (
        clear_preview_touch_cache,
        touch_preview_activity,
    )

    topic_id, seat = _room(client)
    clear_preview_touch_cache()
    stale = datetime.now(UTC) - timedelta(minutes=15)

    async def _seed() -> tuple[uuid.UUID, str, uuid.UUID]:
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
            return home.id, home.resource_id, row.id

    home_id, resource, session_id = client.portal.call(_seed)
    thawed: list[tuple[str, str]] = []

    async def _fake_thaw(hub, device_id, work_resource):
        thawed.append((device_id, work_resource))

    monkeypatch.setattr(lifecycle, "thaw_on_host", _fake_thaw)

    async def _touch() -> tuple[bool, datetime | None, bool]:
        async with client.test_request_factory() as db:
            tasks = await touch_preview_activity(db, topic_id, seat)
        assert len(tasks) == 1, "a frozen sandbox was not asked to thaw"
        started = await tasks[0]
        async with client.test_request_factory() as db:
            home = await db.get(SandboxHome, home_id)
            row = await AgentSessionService(db).by_id(session_id)
            return (
                started,
                home.frozen_at,
                bool((row.execution_request or {}).get(THAW_KEY)),
            )

    started, frozen_at, still_thawing = client.portal.call(_touch)
    assert started is True
    assert thawed == [("box-1", resource)]
    assert frozen_at is None
    assert still_thawing is False
