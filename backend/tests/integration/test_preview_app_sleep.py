"""A previewed app whose machine sleeps: its kept snapshot, waking it, and the steps.

The panel shows the last page that loaded while the app cannot be reached, and a
button that asks the teammate who declared the app to bring it back. The tunnel
here is the real hub with a fake machine on the other end; the agent's turn is
recorded instead of run.
"""

import uuid

import pytest

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


def test_waking_an_app_with_serve_meta_restarts_it_on_machine_without_a_model_turn(
    client, preview_config, runner, monkeypatch
):
    """When `cheese serve` recorded its launch spec (`serve`), waking a sleeping
    app brings the sandbox and the app back up directly on the machine without
    spending an agent turn; if direct restore fails, it falls back to a turn."""
    import time

    from app.domain.agent import execution
    from app.domain.agent.device_hub import device_hub
    from app.domain.agent_session.models import LOST_KEY
    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine import preview_wake

    topic_id, seat = _room(client)
    task_id = str(uuid.uuid4())
    serve_spec = {
        "port": 5173,
        "command": "python3 -m http.server 5173 --bind 127.0.0.1",
        "cwd": f"/home/cheese/.cheese/tasks/{task_id}",
        "home": "/home/cheese",
        "task_id": task_id,
    }
    machine = FakeMachine().attach(topic_id, seat)
    try:
        shown = client.post(
            f"/topics/{topic_id}/shown",
            json={"path": "Vue dev server", "as": "app", "serve": serve_spec},
            headers=ALICE,
        )
        assert shown.status_code == 200, shown.text
        assert shown.json()["data"]["meta"]["serve"] == serve_spec
    finally:
        machine.detach()

    # Seed the seat's session with a destroyed sandbox marker (`LOST_KEY`).
    async def _seed_session() -> uuid.UUID:
        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic_id, seat, harness="claude-code"
            )
            row.runtime_location = {
                "device_id": "center",
                "resource_id": str(topic_id),
                "channel": "device",
            }
            row.work_lease = {
                "kind": "device",
                "device_id": "box-1",
                "status": "ready",
                "state": "/box-1/state",
                "workspace": "/box-1/work",
                "generation": "gen-1",
            }
            row.execution_request = {"generation": "gen-1", LOST_KEY: True}
            await db.commit()
            return row.id

    session_id = client.portal.call(_seed_session)
    invoked: list[dict] = []
    reattached: list[FakeMachine] = []

    async def _fake_ensure(db, **kwargs):
        return {
            "target": {
                "kind": "device",
                "device_id": "box-1",
                "status": "ready",
                "state": "/box-1/state",
                "workspace": "/box-1/work",
            },
            "token": "tok",
        }

    async def _fake_call(target, method, params, **_kwargs):
        invoked.append({"target": target, "method": method, "params": params})
        # Simulate `cheese serve 5173 --restore ...` bringing the tunnel & app up.
        reattached.append(FakeMachine().attach(topic_id, seat))
        return {"value": {"stdout": "", "stderr": ""}}

    monkeypatch.setattr(preview_wake.session_work, "ensure", _fake_ensure)
    monkeypatch.setattr(execution, "call", _fake_call)
    monkeypatch.setattr(device_hub, "is_online", lambda _dev: True)

    try:
        woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
        assert woke.status_code == 200, woke.text
        assert woke.json()["data"] == {"started": True}

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = client.get(
                f"/topics/{topic_id}/preview/wake-status", headers=ALICE
            ).json()["data"]
            if status["app_up"]:
                break
            time.sleep(0.05)
        assert status["app_up"] is True
        assert status["tunnel_up"] is True
        # Zero LLM turns started!
        assert runner.submitted == []
        assert len(invoked) == 1
        assert invoked[0]["method"] == "invoke"
        assert "cheese serve 5173 --restore" in invoked[0]["params"]["args"]["command"]

        # And LOST_KEY was cleared because the task checkout was restored.
        async def _read_lost() -> bool:
            async with client.test_request_factory() as db:
                row = await AgentSessionService(db).by_id(session_id)
                return bool((row.execution_request or {}).get(LOST_KEY))

        assert client.portal.call(_read_lost) is False
    finally:
        for m in reattached:
            m.detach()


def test_waking_falls_back_to_agent_turn_when_direct_restore_fails(
    client, preview_config, runner, monkeypatch
):
    """If the direct machine restore fails (e.g. command exits non-zero), the
    platform falls back to starting an agent turn so the user is not stuck."""
    import time

    from app.domain.agent import execution
    from app.domain.agent_session.services import AgentSessionService
    from app.domain.machine import preview_wake

    topic_id, seat = _room(client)
    serve_spec = {
        "port": 5173,
        "command": "npm run dev",
        "cwd": "/home/cheese/room",
        "home": "/home/cheese",
    }
    machine = FakeMachine().attach(topic_id, seat)
    try:
        shown = client.post(
            f"/topics/{topic_id}/shown",
            json={"path": "npm run dev", "as": "app", "serve": serve_spec},
            headers=ALICE,
        )
        assert shown.status_code == 200, shown.text
    finally:
        machine.detach()

    async def _seed_session():
        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic_id, seat, harness="claude-code"
            )
            row.runtime_location = {
                "device_id": "center",
                "resource_id": str(topic_id),
                "channel": "device",
            }
            await db.commit()

    client.portal.call(_seed_session)

    async def _fake_ensure(db, **_kwargs):
        return {
            "target": {
                "kind": "device",
                "device_id": "box-1",
                "status": "ready",
                "state": "/box-1/state",
                "workspace": "/box-1/work",
            }
        }

    async def _failing_call(target, method, params, **_kwargs):
        return {"value": {"stdout": "Exit code 1\n", "stderr": "port failed"}}

    monkeypatch.setattr(preview_wake.session_work, "ensure", _fake_ensure)
    monkeypatch.setattr(execution, "call", _failing_call)

    woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
    assert woke.status_code == 200, woke.text
    assert woke.json()["data"] == {"started": True}

    deadline = time.monotonic() + 5.0
    while not runner.submitted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(runner.submitted) == 1
    assert "cheese serve" in runner.submitted[0]["content"]


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

    async def _seed_home() -> uuid.UUID:
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
            db.add(home)
            await db.commit()
            return home.id

    home_id = client.portal.call(_seed_home)

    async def _touch_and_read() -> tuple[datetime, datetime]:
        async with client.test_request_factory() as db:
            await touch_preview_activity(db, topic_id, seat)
            first = (await db.get(SandboxHome, home_id)).active_at
            # Move it back in DB without clearing the in-memory throttle: a
            # second call within ACTIVE_STEP must skip the DB write.
            home = await db.get(SandboxHome, home_id)
            home.active_at = stale
            await db.commit()
            await touch_preview_activity(db, topic_id, seat)
            second = (await db.get(SandboxHome, home_id)).active_at
            return first, second

    first, second = client.portal.call(_touch_and_read)
    assert first > stale + timedelta(minutes=10)
    assert second == stale
