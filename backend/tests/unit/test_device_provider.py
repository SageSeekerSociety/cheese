"""DeviceProvider: turn orchestration + hook→AgentEvent translation (injected hub)."""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceProvider
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.service import AgentMessage, AgentResult, AgentSessionInfo
from app.domain.device.repository import Device, TopicDevice
from app.domain.device.supply import Supply, Visibility


@pytest.fixture(autouse=True)
def _no_device_identity(monkeypatch):
    """Default every test to a device that brings no ccproxy identity.

    `_device_ccproxy_upstream` hits the database exactly like `_is_co_located`
    does, and these tests run without one — but unlike `_is_co_located` it is
    called on one specific branch, so leaving it unpatched fails only the six
    subscription-path tests and looks like their bug. Tests about the
    machine-ticket signal override this with a real value."""

    async def none(_self, _device_id):
        return ""

    monkeypatch.setattr(DeviceProvider, "_device_ccproxy_upstream", none)


class FakeHub:
    """Minimal DeviceHub stand-in recording what the provider drives."""

    def __init__(self) -> None:
        self.opened: list[HubScreen] = []
        self.envs: list[dict | None] = []  # env injected into each opened screen
        self.prompts: list[list] = []
        self.reasserted: list[str] = []  # sids re-sent as adopt-creates
        self.execs: list[tuple[list, str | None]] = []  # (argv, stdin)

    def online_device_ids(self) -> list[str]:
        return ["dev1"]

    def is_online(self, device_id: str) -> bool:
        return device_id == "dev1"

    def all_online_screens(self) -> list[HubScreen]:
        return list(self.opened)

    async def open_screen(self, device_id, command, source, **kw) -> HubScreen:
        screen = HubScreen(
            sid="s1",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
            hook_key=kw.get("hook_key", ""),
        )
        self.opened.append(screen)
        self.envs.append(kw.get("env"))
        return screen

    async def reassert_screen(
        self, screen: HubScreen, *, command, cheeselet_source, env=None
    ) -> None:
        screen.command = command
        self.reasserted.append(screen.sid)

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        self.execs.append((argv, stdin))
        return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}

    async def call_screen(self, device_id, sid, name, args) -> str:
        self.prompts.append(args)
        return "call1"

    async def await_call(self, device_id, call_id, timeout=30):
        return {"ok": True}


def _provider(hub: FakeHub, router: HookRouter, agent_id: uuid.UUID) -> DeviceProvider:
    async def resolver(_project_id, _topic_id):
        return ("dev1", agent_id, "agent-x")

    return DeviceProvider(
        hub=hub,  # type: ignore[arg-type]
        device_resolver=resolver,
        router=router,
        public_base="http://test",
        hard_ceiling_s=5,
    )


async def _run(provider: DeviceProvider, **kw) -> list:
    events: list = []

    async def consume():
        async for ev in provider.run_turn(**kw):
            events.append(ev)

    return events, asyncio.create_task(consume())


async def test_turn_streams_hook_events_until_stop():
    hub = FakeHub()
    router = HookRouter()
    agent_id = uuid.uuid4()
    provider = _provider(hub, router, agent_id)
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()

    events, task = await _run(
        provider,
        project_id=project_id,
        topic_id=topic_id,
        prompt="1+1?",
        system_prompt="",
        resume_session_id=None,
    )
    # Let it resolve the device, open the screen, send the prompt, reach the drain.
    await asyncio.sleep(0.05)
    assert hub.prompts == [["1+1?"]]  # prompt delivered via the cheeselet
    assert hub.opened[0].topic_id == topic_id
    assert hub.opened[0].agent_user_id == agent_id

    key = str(topic_id)
    router.push(key, {"hook_event_name": "SessionStart", "session_id": "sid"})
    router.push(key, {"hook_event_name": "MessageDisplay", "delta": "2"})
    router.push(key, {"hook_event_name": "Stop", "last_assistant_message": "2"})
    await asyncio.wait_for(task, timeout=5)

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["AgentSessionInfo", "AgentMessage", "AgentResult"]
    assert isinstance(events[0], AgentSessionInfo)
    assert isinstance(events[1], AgentMessage) and events[1].text == "2"
    assert isinstance(events[2], AgentResult) and events[2].text == "2"


async def test_restart_recovery_uses_durable_topic_pins(monkeypatch):
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, _model, key):
            if key == topic_id:
                return SimpleNamespace(id=topic_id, project_id=project_id)
            return None

    class Service:
        async def list_topic_bindings(self, device_id):
            assert device_id == "dev1"
            return [
                TopicDevice(
                    topic_id=topic_id,
                    device_id=device_id,
                    visibility=Visibility.host,
                )
            ]

    monkeypatch.setattr(
        "app.domain.agent.device_provider.sql_device_service",
        lambda _session: Service(),
    )
    router = HookRouter()
    provider = DeviceProvider(
        hub=FakeHub(),  # type: ignore[arg-type]
        router=router,
        session_factory=Session,  # type: ignore[arg-type]
    )

    recovered = await provider.recover_subscriptions("dev1")

    assert len(recovered) == 1
    assert recovered[0].project_id == project_id
    assert recovered[0].topic_id == topic_id
    assert provider._subscription_devices[topic_id] == "dev1"
    recovered[0].ready.set()
    router.push(str(topic_id), {"hook_event_name": "PostToolUse"})
    await recovered[0].sink.queue.join()

    await provider.drop_device_subscriptions("dev1")
    assert router.push(str(topic_id), {"hook_event_name": "Stop"}) is False


async def test_a_reported_delivery_failure_is_resent_immediately():
    """#445: the cheeselet's give-up (surfaced as a CheeseDeliveryFailed hook)
    must trigger an immediate re-send of the prompt plus a visible message —
    not leave the room waiting out the 300s no-output bound."""
    hub = FakeHub()
    router = HookRouter()
    agent_id = uuid.uuid4()
    provider = _provider(hub, router, agent_id)
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()

    events, task = await _run(
        provider,
        project_id=project_id,
        topic_id=topic_id,
        prompt="1+1?",
        system_prompt="",
        resume_session_id=None,
    )
    await asyncio.sleep(0.05)
    assert hub.prompts == [["1+1?"]]

    key = str(topic_id)
    router.push(
        key, {"hook_event_name": "CheeseDeliveryFailed", "phase": "paste", "ticks": 41}
    )
    await asyncio.sleep(0.05)
    assert hub.prompts == [["1+1?"], ["1+1?"]], "the prompt was not re-sent"

    router.push(key, {"hook_event_name": "Stop", "last_assistant_message": "2"})
    await asyncio.wait_for(task, timeout=5)

    texts = [getattr(e, "text", "") for e in events]
    assert any("重投" in t for t in texts), f"no visible re-send notice: {texts}"
    assert not any(type(e).__name__ == "AgentDeliveryFailure" for e in events), (
        "the internal delivery-failure event leaked to the chat layer"
    )


async def test_second_turn_reasserts_the_screen_instead_of_trusting_the_registry():
    """The hub's registry outlives what the device actually runs (a connector
    restart kills its sessions; a create sent on a dying transport was never
    delivered), and the frozen cli silently drops rpc.calls for unknown sids. So a
    later turn must re-send the screen's adopt-create — idempotent on a live
    session, a respawn for a lost one — rather than prompt a screen that may not
    exist and die in a blank timeout."""
    hub = FakeHub()
    router = HookRouter()
    provider = _provider(hub, router, uuid.uuid4())
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    key = str(topic_id)

    for turn in range(2):
        _events, task = await _run(
            provider,
            project_id=project_id,
            topic_id=topic_id,
            prompt=f"turn {turn}",
            system_prompt="",
            resume_session_id=None,
        )
        await asyncio.sleep(0.05)
        router.push(key, {"hook_event_name": "Stop", "last_assistant_message": "ok"})
        await asyncio.wait_for(task, timeout=5)

    assert len(hub.opened) == 1  # the topic keeps ONE screen …
    assert hub.reasserted == [hub.opened[0].sid]  # … re-asserted on reuse
    assert hub.prompts == [["turn 0"], ["turn 1"]]


class DeadClaudeHub(FakeHub):
    """A device whose `claude` DIED while the connector kept running: the hub's
    registry still holds the screen, but the liveness probe (DEVICE_ALIVE_PROBE
    over `exec`) answers `dead`. Records `close_screen` and hands out distinct
    sids so a reopen is distinguishable from a reassert."""

    def __init__(self) -> None:
        super().__init__()
        self.closed: list[str] = []
        self._sid_seq = 0

    async def open_screen(self, device_id, command, source, **kw) -> HubScreen:
        self._sid_seq += 1
        screen = HubScreen(
            sid=f"s{self._sid_seq}",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
            hook_key=kw.get("hook_key", ""),
        )
        self.opened.append(screen)
        return screen

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        self.execs.append((argv, stdin))
        # Only the liveness probe (no stdin) reports death; the launcher-ship exec
        # (script on stdin) must still succeed or the turn never reaches a screen.
        out = "dead" if stdin is None else ""
        return {"stdout": out, "stderr": "", "exit": 0, "truncated": False}

    async def close_screen(self, device_id, sid) -> bool:
        self.closed.append(sid)
        self.opened = [s for s in self.opened if s.sid != sid]  # hub forgets it
        return True


async def test_a_reused_screen_whose_claude_died_is_reopened_not_reasserted():
    """The registry holding a screen is NOT proof its `claude` still runs: an orphan
    sweep or `tmux kill-server` can end the device session while the connector lives
    on. Reasserting (adopt-create) would only hot-reload the cheeselet into the dead
    pane — the frozen connector re-Spawns solely for a sid it forgot (i.e. after IT
    restarted), so a screen whose process died under a live connector is never
    respawned and the turn dies in the delivery timeout with no model reached. So a
    reused screen is probed first; a `dead` one is CLOSED (which makes the connector
    forget the sid too) and reopened under a fresh sid the connector must Spawn."""
    hub = DeadClaudeHub()
    router = HookRouter()
    provider = _provider(hub, router, uuid.uuid4())
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    key = str(topic_id)

    for turn in range(2):
        _events, task = await _run(
            provider,
            project_id=project_id,
            topic_id=topic_id,
            prompt=f"turn {turn}",
            system_prompt="",
            resume_session_id=None,
        )
        await asyncio.sleep(0.05)
        router.push(key, {"hook_event_name": "Stop", "last_assistant_message": "ok"})
        await asyncio.wait_for(task, timeout=5)

    assert hub.reasserted == []  # NEVER reasserted into the corpse …
    assert hub.closed == ["s1"]  # … the stale screen was dropped …
    assert [s.sid for s in hub.opened] == ["s2"]  # … and a fresh screen Spawned
    assert hub.prompts == [["turn 0"], ["turn 1"]]  # both turns still delivered


async def test_launch_script_ships_as_a_file_never_as_tmux_argv():
    """The frozen cli hands the screen command to `tmux new-session`, whose packed
    command tops out around 16KB — a launcher carrying the assembled system prompt
    blows through that and every spawn dies with `command too long` (observed live:
    a 57KB session.create, tmux exit 1, and a silent 3×60s prompt timeout). So the
    script must travel over the link's `exec` (stdin → a per-topic file) and the
    session command must stay a short runner, no matter how large the prompt is."""
    hub = FakeHub()
    router = HookRouter()
    provider = _provider(hub, router, uuid.uuid4())
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()

    _events, task = await _run(
        provider,
        project_id=project_id,
        topic_id=topic_id,
        prompt="hi",
        system_prompt="x" * 100_000,  # a system prompt far past tmux's limit
        resume_session_id=None,
    )
    await asyncio.sleep(0.05)
    router.push(
        str(topic_id), {"hook_event_name": "Stop", "last_assistant_message": "ok"}
    )
    await asyncio.wait_for(task, timeout=5)

    # The big script went over exec's stdin, into the per-topic launch file …
    (argv, stdin) = next((a, s) for a, s in hub.execs if s and "x" * 1000 in s)
    assert f"$HOME/.cheese/launch/{topic_id}.sh" in argv[-1]
    # … and the command the device passes to tmux stays tiny and points at it.
    command = hub.opened[0].command
    assert sum(len(part) for part in command) < 1024
    assert f"$HOME/.cheese/launch/{topic_id}.sh" in command[-1]


async def test_no_topic_is_a_clean_error():
    hub = FakeHub()
    provider = _provider(hub, HookRouter(), uuid.uuid4())
    events = [
        e
        async for e in provider.run_turn(
            project_id=uuid.uuid4(),
            topic_id=None,
            prompt="hi",
            system_prompt="",
            resume_session_id=None,
        )
    ]
    assert len(events) == 1 and isinstance(events[0], AgentResult)
    assert events[0].is_error


async def test_no_online_device_is_a_clean_error():
    hub = FakeHub()

    async def resolver(_project_id, _topic_id):
        return None  # nothing online / bound

    provider = DeviceProvider(
        hub=hub,  # type: ignore[arg-type]
        device_resolver=resolver,
        router=HookRouter(),
        hard_ceiling_s=5,
    )
    events = [
        e
        async for e in provider.run_turn(
            project_id=uuid.uuid4(),
            topic_id=uuid.uuid4(),
            prompt="hi",
            system_prompt="",
            resume_session_id=None,
        )
    ]
    assert len(events) == 1 and isinstance(events[0], AgentResult)
    assert events[0].is_error


def test_work_dir_colocated_translates_to_host_worktree(monkeypatch, tmp_path):
    """A co-located device (device_shared_workspace_host_root set) gets the topic's
    REAL worktree, its container path translated to the host root the device sees —
    not an empty scratch dir."""
    from app.core.config import settings
    from app.domain.agent import device_provider as dp

    pid = uuid.uuid4()
    tid = uuid.uuid4()
    container_root = tmp_path / "app" / ".workspaces"
    wt = container_root / ".worktrees" / str(pid) / "topic-abc"
    wt.mkdir(parents=True)

    monkeypatch.setattr(settings, "workspace_root", str(container_root))
    monkeypatch.setattr(
        settings, "device_shared_workspace_host_root", "/home/dev/cheese-workspaces"
    )
    monkeypatch.setattr(dp.ws, "topic_worktree", lambda p, t: wt)

    prov = DeviceProvider(hub=FakeHub())
    got = prov._work_dir(pid, tid, co_located=True)
    assert got == f"/home/dev/cheese-workspaces/.worktrees/{pid}/topic-abc"


def test_work_dir_remote_uses_scratch(monkeypatch):
    """With no shared host root (remote device), the work dir stays a per-topic
    scratch the launcher creates — the co-located path is opt-in."""
    from app.core.config import settings

    pid = uuid.uuid4()
    tid = uuid.uuid4()
    monkeypatch.setattr(settings, "device_shared_workspace_host_root", "")
    prov = DeviceProvider(hub=FakeHub())
    assert (
        prov._work_dir(pid, tid, co_located=True) == f"$HOME/.cheese/work/{pid}/{tid}"
    )


@pytest.mark.anyio
async def test_concurrent_topics_never_share_a_device_home():
    """Two topics of one project must get two DIFFERENT device homes. Hook
    events spool under $HOME/.claude and the drainer ships the spool with the
    URL + token in cheese-drain.env, which every screen start overwrites — so
    a project-shared home delivers every concurrent screen's events to
    whichever session started last: its topic swallows all events, the other
    topics' turns show zero output (dev, 2026-08-15)."""
    hub = FakeHub()
    router = HookRouter()
    agent_id = uuid.uuid4()
    provider = _provider(hub, router, agent_id)
    project_id = uuid.uuid4()
    topic_a, topic_b = uuid.uuid4(), uuid.uuid4()

    for topic_id in (topic_a, topic_b):
        _events, task = await _run(
            provider,
            project_id=project_id,
            topic_id=topic_id,
            prompt="hi",
            system_prompt="",
            resume_session_id=None,
        )
        await asyncio.sleep(0.05)
        router.push(
            str(topic_id),
            {"hook_event_name": "Stop", "last_assistant_message": "ok"},
        )
        await asyncio.wait_for(task, timeout=5)

    homes = [env["CHEESE_HOME"] for env in hub.envs]
    assert str(topic_a) in homes[0]
    assert str(topic_b) in homes[1]
    assert homes[0] != homes[1]


@pytest.mark.anyio
async def test_a_provisioned_machine_is_never_treated_as_co_located(monkeypatch):
    """A MicroCloud machine is on its own host, whatever the deployment says.

    Getting this wrong is silent, not loud: the launcher `mkdir -p`s whatever
    path it is handed, so a turn would open in an EMPTY directory instead of the
    topic's worktree — the agent would find no code and no one would see an error.
    """
    monkeypatch.setattr(
        "app.domain.agent.device_provider.settings.device_shared_workspace_host_root",
        "/home/box/cheese-workspaces",
    )

    # Reads `device.supply` (#282 决定 2). This used to fake
    # `ProjectMachineRepository.is_provisioned_device` — 「machine 表里有没有一行
    # 指向这个 device」— which is precisely the reverse lookup #282 replaced.
    def _device(device_id: str, supply: Supply) -> Device:
        return Device(
            device_id=device_id,
            name=device_id,
            token="t",
            owner_user_id=1,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            supply=supply,
        )

    known = {
        "microcloud-machine": _device("microcloud-machine", Supply.cloud),
        "the-box-itself": _device("the-box-itself", Supply.self_hosted),
    }

    class Service:
        def __init__(self, session):
            self._session = session

        async def get_device(self, device_id):
            return known.get(device_id)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    # Goes through `device.wiring` — the sanctioned seam. Reaching into
    # `device.sql_repository` from the agent domain is what
    # tests/unit/test_domain_import_guard.py exists to stop.
    monkeypatch.setattr("app.domain.agent.device_provider.sql_device_service", Service)
    provider = DeviceProvider(session_factory=Session)

    assert await provider._is_co_located("microcloud-machine") is False
    assert await provider._is_co_located("the-box-itself") is True
    # A device nobody has a row for keeps the old reading rather than silently
    # flipping: the deployments that set a shared root are single-box ones.
    assert await provider._is_co_located("never-seen") is True


@pytest.mark.anyio
async def test_co_location_is_off_when_the_deployment_shares_nothing(monkeypatch):
    monkeypatch.setattr(
        "app.domain.agent.device_provider.settings.device_shared_workspace_host_root",
        "",
    )
    provider = DeviceProvider()
    assert await provider._is_co_located("anything") is False


def test_a_remote_device_gets_a_scratch_dir_not_the_boxs_path(monkeypatch):

    monkeypatch.setattr(
        "app.domain.agent.device_provider.settings.device_shared_workspace_host_root",
        "/home/box/cheese-workspaces",
    )
    provider = DeviceProvider()
    project, topic = uuid.uuid4(), uuid.uuid4()

    remote = provider._work_dir(project, topic, co_located=False)
    assert remote.startswith("$HOME/.cheese/work/")
    assert "/home/box/" not in remote


@pytest.mark.anyio
async def test_checkpoint_does_not_snapshot_for_a_remote_machine(monkeypatch):
    """The snapshot decision must follow the DEVICE, not the deployment switch.

    On a box that also hosts local containers the switch is on, so a remote
    machine's turn used to snapshot the backend's untouched worktree — recording
    an empty commit as if it were the agent's work, while the real edits sat on
    the machine. Silent and wrong in the direction that loses work.
    """
    monkeypatch.setattr(
        "app.domain.agent.device_provider.settings.device_shared_workspace_host_root",
        "/home/box/cheese-workspaces",
    )
    snapshots: list[tuple] = []
    monkeypatch.setattr(
        "app.domain.agent.device_provider.ws.snapshot_worktree",
        lambda p, t: snapshots.append((p, t)),
    )
    provider = DeviceProvider(hub=FakeHub())
    project, topic = uuid.uuid4(), uuid.uuid4()

    provider._co_located_at[(project, topic)] = False
    provider.checkpoint(project, topic)
    assert snapshots == []

    provider._co_located_at[(project, topic)] = True
    provider.checkpoint(project, topic)
    assert snapshots == [(project, topic)]


def test_only_a_machine_that_owns_its_tree_gets_the_push_hook():
    """cheese-sync hands work back over git; the local container edits the real
    worktree and must not also try to push it."""
    from app.domain.agent.hooks_substrate import hooks_settings

    def stop_commands(settings_obj) -> list[str]:
        return [
            hook["command"]
            for entry in settings_obj["hooks"]["Stop"]
            for hook in entry["hooks"]
        ]

    assert stop_commands(hooks_settings()) == ["cheese-hook"]
    assert stop_commands(hooks_settings(["cheese-sync"])) == [
        "cheese-hook",
        "cheese-sync",
    ]


@pytest.mark.anyio
async def test_a_remote_machine_is_told_where_to_clone_from(monkeypatch):
    """The launcher's clone/push block is inert without these two env vars, so the
    wiring is the thing that has to be tested — the block itself can be perfect
    and the machine still starts in an empty dir."""
    from app.domain.workspace.service import branch_for_topic

    class RecordingHub(FakeHub):
        def __init__(self) -> None:
            super().__init__()
            self.env: dict = {}

        async def open_screen(self, device_id, command, source, **kw):
            self.env = kw.get("env") or {}
            return await super().open_screen(device_id, command, source, **kw)

    project, topic = uuid.uuid4(), uuid.uuid4()

    async def ensure(co_located: bool) -> dict:
        hub = RecordingHub()
        provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
        monkeypatch.setattr(
            provider, "_is_co_located", lambda _d: _async_value(co_located)
        )
        await provider._ensure_screen(
            device_id="dev1",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=project,
            topic_id=topic,
            token="tok",
            model=None,
            env=None,
        )
        return hub.env

    remote = await ensure(False)
    assert remote["CHEESE_GIT_REMOTE"] == (
        f"http://cheese.test/api/projects/{project}/git"
    )
    assert remote["CHEESE_GIT_BRANCH"] == branch_for_topic(topic)

    # A co-located device edits the real worktree; cloning over it would be wrong.
    assert "CHEESE_GIT_REMOTE" not in await ensure(True)


async def _async_value(value):
    return value


# --- release_topic: freeing a done topic's screen (the leak this fixes) --------


class ReleaseHub(FakeHub):
    """Hub stand-in that records screen teardown for release_topic tests."""

    def __init__(self, screens: dict) -> None:
        super().__init__()
        self._by_topic = screens  # topic_id -> list[HubScreen]
        self.online = {"dev1"}
        self.closed: list[tuple[str, str]] = []  # (device_id, sid)
        self.execs: list[tuple[str, list]] = []  # (device_id, argv)

    def screens_for_topic(self, topic_id) -> list[HubScreen]:
        return list(self._by_topic.get(topic_id, []))

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online

    async def close_screen(self, device_id, sid) -> bool:
        self.closed.append((device_id, sid))
        return True

    async def exec(self, device_id, argv, timeout=30):
        self.execs.append((device_id, argv))
        return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}


def _screen(device_id: str, sid: str, project_id, topic_id) -> HubScreen:
    return HubScreen(
        sid=sid,
        device_id=device_id,
        command=[],
        token=f"tok-{sid}",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=project_id,
        topic_id=topic_id,
    )


@pytest.mark.anyio
async def test_release_topic_closes_the_exact_screen(monkeypatch):
    """Archiving a topic that ran on a device must close THAT topic's screen on the
    device it pinned to — the reverse lookup lands on the right (device_id, sid)."""
    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({tid: [_screen("dev1", "s7", pid, tid)]})
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))

    await provider.release_topic(pid, tid)

    assert hub.closed == [("dev1", "s7")]


@pytest.mark.anyio
async def test_release_topic_removes_only_a_remote_devices_work_dir(monkeypatch):
    """A REMOTE device owns its own clone under a per-topic scratch dir, so releasing
    the topic removes that dir too — and the delete target is that scratch path,
    never anything above it."""
    from app.core.config import settings

    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({tid: [_screen("dev1", "s1", pid, tid)]})
    monkeypatch.setattr(settings, "device_shared_workspace_host_root", "")
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))

    await provider.release_topic(pid, tid)

    assert hub.closed == [("dev1", "s1")]
    assert len(hub.execs) == 1
    device_id, argv = hub.execs[0]
    assert device_id == "dev1"
    assert argv[0] == "sh"
    rm = argv[-1]
    assert "rm -rf" in rm
    assert f"$HOME/.cheese/work/{pid}/{tid}" in rm


@pytest.mark.anyio
async def test_release_topic_never_touches_a_co_located_tree(monkeypatch):
    """A CO-LOCATED device edited the backend's REAL worktree, so releasing the
    topic closes its screen but must NEVER delete a work dir — deleting the shared
    tree would destroy the topic's branch."""
    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({tid: [_screen("dev1", "s1", pid, tid)]})
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(True))

    await provider.release_topic(pid, tid)

    assert hub.closed == [("dev1", "s1")]
    assert hub.execs == []  # the backend's real worktree is never rm'd


@pytest.mark.anyio
async def test_release_topic_is_a_silent_noop_without_a_screen():
    """The machine may be offline (its screen already gone) or the topic may never
    have run on a device — either way release is a successful no-op, never raising
    (a reap loop must not break on one topic)."""
    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({})  # nothing registered for this topic
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]

    await provider.release_topic(pid, tid)

    assert hub.closed == [] and hub.execs == []


@pytest.mark.anyio
async def test_release_topic_forgets_an_offline_screen_without_a_remote_rm(monkeypatch):
    """An offline device is unreachable — its work dir can't be removed now — but its
    screen is still forgotten here, so an archived topic leaves no stale registry
    entry to re-surface if the machine reconnects."""
    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({tid: [_screen("devX", "s1", pid, tid)]})
    hub.online = set()  # device offline
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))

    await provider.release_topic(pid, tid)

    assert hub.closed == [("devX", "s1")]  # forgotten from the registry
    assert hub.execs == []  # unreachable → no rm attempted


@pytest.mark.anyio
async def test_release_topic_swallows_a_teardown_error(monkeypatch):
    """A dropped device channel mid-teardown must not propagate — the idle reaper
    walks many topics and one failure can't be allowed to abort the rest."""
    pid, tid = uuid.uuid4(), uuid.uuid4()

    class Boom(ReleaseHub):
        async def close_screen(self, device_id, sid):
            raise RuntimeError("device channel dropped")

    hub = Boom({tid: [_screen("dev1", "s1", pid, tid)]})
    provider = DeviceProvider(hub=hub)  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))

    await provider.release_topic(pid, tid)  # must not raise


@pytest.mark.anyio
async def test_release_topic_screen_wrapper_drives_the_given_hub(monkeypatch):
    """The module-level wrapper (what the accept path and reaper call) frees the
    topic through DeviceProvider against the hub it is handed."""
    from app.core.config import settings
    from app.domain.agent.device_provider import release_topic_screen

    pid, tid = uuid.uuid4(), uuid.uuid4()
    hub = ReleaseHub({tid: [_screen("dev1", "s1", pid, tid)]})
    monkeypatch.setattr(settings, "device_shared_workspace_host_root", "")  # remote

    await release_topic_screen(pid, tid, hub=hub)  # type: ignore[arg-type]

    assert hub.closed == [("dev1", "s1")]
    assert len(hub.execs) == 1  # remote → its scratch dir removed


def test_a_remote_device_is_warned_about_a_box_local_model_endpoint(
    monkeypatch, caplog
):
    """Routing the box's turns through the local gateway is what makes spend
    visible — but the same URL means nothing on a machine elsewhere, and the
    failure there is a connection error with no hint why."""
    import logging

    from app.domain.agent.device_provider import (
        _warn_if_model_endpoint_is_box_local,
    )

    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"ANTHROPIC_BASE_URL": "http://172.17.0.1:4000"}, "machine-1"
        )
    assert any("only resolves on the backend" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"},
            "machine-1",
        )
    assert not caplog.records, "a reachable endpoint must not be flagged"


@pytest.mark.anyio
async def test_a_machine_never_receives_the_upstream_provider_key(monkeypatch):
    """The credential must stay on the box.

    A machine used to be handed the raw provider key in its environment, in
    plain sight of anyone on that host — it was readable straight out of a tmux
    command line — and its spend landed in the invoice under one
    undifferentiated key, which is why a week of it could not be attributed to
    anything. It now gets the backend's own model route and its scoped token,
    and the backend substitutes the project's virtual key on the way through.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_auth_token", "UPSTREAM-PROVIDER-KEY")
    monkeypatch.setattr(settings, "anthropic_base_url", "https://provider.example")

    class RecordingHub(FakeHub):
        def __init__(self) -> None:
            super().__init__()
            self.env: dict = {}

        async def open_screen(self, device_id, command, source, **kw):
            self.env = kw.get("env") or {}
            return await super().open_screen(device_id, command, source, **kw)

    hub = RecordingHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))

    await provider._ensure_screen(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="scoped-token-for-this-topic",
        model=None,
        env=None,
    )

    blob = repr(hub.env)
    assert "UPSTREAM-PROVIDER-KEY" not in blob, "the provider key reached the machine"
    assert "provider.example" not in blob, "the machine was pointed at the upstream"
    # The route is root-mounted (`/llm`), like /sandbox and /connector: the
    # base already maps 1:1 onto the backend root — see routes/llm_proxy.py.
    assert hub.env["ANTHROPIC_BASE_URL"] == "http://cheese.test/llm"
    assert hub.env["ANTHROPIC_AUTH_TOKEN"] == "scoped-token-for-this-topic"


# --- subscription parity (#325 G2): device turns ride the metering proxy --------
# On a subscription deployment a device screen must get the SAME supply a local
# tmux container gets: fake credential + proxy CA + scoped session token, no
# ANTHROPIC_BASE_URL, no gateway model pin. The regression this guards is dev
# shipping device screens with CLAUDE_MODEL=deepseek-chat — users thought they
# were talking to Claude and were not.


class SubRecordingHub(FakeHub):
    def __init__(self) -> None:
        super().__init__()
        self.env: dict = {}

    async def open_screen(self, device_id, command, source, **kw):
        self.env = kw.get("env") or {}
        return await super().open_screen(device_id, command, source, **kw)


def _subscription_settings(monkeypatch, tmp_path) -> str:
    """Point the backend at a readable proxy CA and switch the subscription on.
    Returns the CA text so tests can assert it reaches the device."""
    from app.core.config import settings

    ca = "-----BEGIN CERTIFICATE-----\nMETERCA\n-----END CERTIFICATE-----\n"
    ca_path = tmp_path / "proxy-ca.pem"
    ca_path.write_text(ca)
    monkeypatch.setattr(settings, "subscription_enabled", True)
    monkeypatch.setattr(settings, "subscription_ca_backend_path", str(ca_path))
    monkeypatch.setattr(settings, "subscription_proxy_host", "172.17.0.1")
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)
    return ca


async def _subscription_screen(
    monkeypatch, co_located: bool, env: dict | None = None
) -> tuple[SubRecordingHub, uuid.UUID, uuid.UUID]:
    hub = SubRecordingHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(co_located))
    project, topic = uuid.uuid4(), uuid.uuid4()
    await provider._ensure_screen(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=project,
        topic_id=topic,
        token="hook-token",
        model=None,
        env=env,
    )
    return hub, project, topic


@pytest.mark.anyio
async def test_subscription_screen_env_has_no_gateway_and_no_real_credential(
    monkeypatch, tmp_path
):
    from app.core.config import settings
    from app.core.sandbox_auth import scoped_token_claims

    monkeypatch.setattr(settings, "anthropic_auth_token", "UPSTREAM-PROVIDER-KEY")
    _subscription_settings(monkeypatch, tmp_path)
    hub, project, topic = await _subscription_screen(monkeypatch, co_located=False)

    env = hub.env
    # No BASE_URL (it flips the CLI into API-key mode), no gateway key, no
    # deepseek/gateway model pin — the exact env dev observed is impossible.
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert not [k for k in env if "MODEL" in k]
    assert "UPSTREAM-PROVIDER-KEY" not in repr(env)
    # The login credential is a scoped cheese token the proxy can verify —
    # never a real subscription credential.
    claims = scoped_token_claims(env["CLAUDE_CODE_OAUTH_TOKEN"])
    assert claims is not None
    assert claims["p"] == str(project) and claims["t"] == str(topic)


@pytest.mark.anyio
async def test_subscription_screen_reaches_the_meter_by_connect_proxy(
    monkeypatch, tmp_path
):
    """A bare device process has no --add-host, so the capture is HTTPS_PROXY at
    the meter's CONNECT listener, with the scoped token as the proxy password —
    and NO_PROXY keeps the platform's own wiring (hooks, git, CLI) out of it."""
    _subscription_settings(monkeypatch, tmp_path)
    hub, _project, _topic = await _subscription_screen(monkeypatch, co_located=False)

    env = hub.env
    token = env["CLAUDE_CODE_OAUTH_TOKEN"]
    assert env["HTTPS_PROXY"] == f"http://cheese:{token}@172.17.0.1:8444"
    for key in ("NO_PROXY", "no_proxy"):
        assert "cheese.test" in env[key]
        assert "localhost" in env[key]


@pytest.mark.anyio
async def test_subscription_ca_travels_in_the_launcher_not_as_a_host_path(
    monkeypatch, tmp_path
):
    """The backend's CA path means nothing on the device. The CA BYTES ride the
    shipped launch script, which writes them under the screen's isolated home
    and exports NODE_EXTRA_CA_CERTS itself."""
    ca = _subscription_settings(monkeypatch, tmp_path)
    hub, _project, _topic = await _subscription_screen(monkeypatch, co_located=False)

    script = next(s for _a, s in hub.execs if s and "CHEESECA" in s)
    assert "METERCA" in script and ca.strip() in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' in script


@pytest.mark.anyio
async def test_subscription_proxy_token_lives_for_the_session_not_one_hour(
    monkeypatch, tmp_path
):
    """The proxy/OAuth token is baked into the bare process's env (HTTPS_PROXY
    CONNECT password + CLAUDE_CODE_OAUTH_TOKEN Bearer), read ONCE at launch and
    never hot-refreshed while the screen is reused across turns. A 1h token
    therefore expires under a still-running agent and the metering proxy 407s
    every later turn. Its exp must span the session, like the CHEESE_TOKEN minted
    beside it — not the per-turn default."""
    import time

    from app.core.sandbox_auth import scoped_token_claims
    from app.domain.agent.hooks_substrate import SESSION_TOKEN_TTL_S

    _subscription_settings(monkeypatch, tmp_path)
    hub, _project, _topic = await _subscription_screen(monkeypatch, co_located=False)

    env = hub.env
    # The Bearer and the CONNECT credential are one and the same token …
    token = env["CLAUDE_CODE_OAUTH_TOKEN"]
    assert f"cheese:{token}@" in env["HTTPS_PROXY"]
    # … and it lives for the whole session, not one hour.
    claims = scoped_token_claims(token)
    assert claims is not None
    remaining = claims["exp"] - int(time.time())
    assert remaining > 7 * 24 * 3600  # rules out the 3600s per-turn default
    assert SESSION_TOKEN_TTL_S - 300 < remaining <= SESSION_TOKEN_TTL_S + 5
    assert hub.env["NODE_EXTRA_CA_CERTS"] == "$HOME/.claude/proxy-ca.pem"


@pytest.mark.anyio
async def test_subscription_env_is_identical_for_co_located_and_remote(
    monkeypatch, tmp_path
):
    """#325 G2: co-located and remote are one path. The machine never holds a
    credential either way, so nothing about the supply may differ — only the
    worktree wiring (clone vs shared tree) does."""
    _subscription_settings(monkeypatch, tmp_path)
    co_hub, _p1, _t1 = await _subscription_screen(monkeypatch, co_located=True)
    re_hub, _p2, _t2 = await _subscription_screen(monkeypatch, co_located=False)

    supply_keys = {
        "ANTHROPIC_AUTH_TOKEN",
        "NODE_EXTRA_CA_CERTS",
        "HTTPS_PROXY",
        "NO_PROXY",
        "no_proxy",
    }
    for key in supply_keys:
        co, remote = co_hub.env[key], re_hub.env[key]
        if key == "HTTPS_PROXY":
            # Same shape; only the embedded per-session token differs.
            co = co.split("@")[-1]
            remote = remote.split("@")[-1]
        assert co == remote, key
    for env in (co_hub.env, re_hub.env):
        assert "ANTHROPIC_BASE_URL" not in env
        assert env["CLAUDE_CODE_OAUTH_TOKEN"]


@pytest.mark.anyio
async def test_subscription_drops_gateway_pins_a_caller_env_carries(
    monkeypatch, tmp_path
):
    """The caller's env is the gateway shape (BASE_URL + model pins). Any of it
    surviving flips the CLI into API-key mode or pins a model the subscription
    does not serve — dropped, not overridden (mirrors the tmux provider)."""
    _subscription_settings(monkeypatch, tmp_path)
    hub, _p, _t = await _subscription_screen(
        monkeypatch,
        co_located=False,
        env={
            "ANTHROPIC_BASE_URL": "http://cheese.test/llm",
            "CLAUDE_MODEL": "deepseek-chat",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-chat",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-chat",
            "SOME_OTHER": "kept",
        },
    )
    env = hub.env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "deepseek" not in repr(env)
    assert env["SOME_OTHER"] == "kept"


@pytest.mark.anyio
async def test_subscription_without_a_readable_ca_fails_loud_not_into_the_gateway(
    monkeypatch, tmp_path
):
    """Falling back to the gateway would silently swap the model — the failure
    #325 G2 exists to kill. A half-configured deployment must say what to fix."""
    from app.core.config import settings
    from app.domain.agent.hooks_substrate import ScreenSetupError

    _subscription_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "subscription_ca_backend_path", "")

    with pytest.raises(ScreenSetupError, match="SUBSCRIPTION_CA_BACKEND_PATH"):
        await _subscription_screen(monkeypatch, co_located=True)


@pytest.mark.anyio
async def test_gateway_route_is_unchanged_when_no_subscription_is_deployed(
    monkeypatch,
):
    """subscription_enabled=False keeps the /llm gateway path byte-for-byte: a
    deployment without the metering proxy must not lose device compute."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "subscription_enabled", False)
    monkeypatch.setattr(settings, "agent_model", "glm-4.7")
    hub = SubRecordingHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
    monkeypatch.setattr(provider, "_is_co_located", lambda _d: _async_value(False))
    await provider._ensure_screen(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="scoped-tok",
        model=None,
        env=None,
    )
    assert hub.env["ANTHROPIC_BASE_URL"] == "http://cheese.test/llm"
    assert hub.env["ANTHROPIC_AUTH_TOKEN"] == "scoped-tok"
    assert hub.env["CLAUDE_MODEL"] == "glm-4.7"
    assert "HTTPS_PROXY" not in hub.env


def test_a_remote_device_is_warned_about_a_box_local_proxy(monkeypatch, caplog):
    """The subscription's analogue of the box-local gateway URL: HTTPS_PROXY at
    the docker bridge names nothing on a machine elsewhere."""
    import logging

    from app.domain.agent.device_provider import _warn_if_model_endpoint_is_box_local

    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"HTTPS_PROXY": "http://cheese:tok@172.17.0.1:8444"}, "machine-1"
        )
    assert any("HTTPS_PROXY" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"HTTPS_PROXY": "http://cheese:tok@proxy.cheese.example:8444"},
            "machine-1",
        )
    assert not caplog.records, "a reachable proxy must not be flagged"


# --- turn 活跃度检测 (the device half): two-layer timeout + liveness probe -------
# The shared two-layer loop (`run_hooks_turn` idle-suspect / hard-ceiling +
# `confirm_alive`) is exercised in test_hooks_substrate.py; these cover what is
# device-SPECIFIC: the two layers are no longer collapsed into one deadline, the
# device's own `_confirm_alive` maps a process-tree probe to a liveness verdict,
# and run_turn actually wires that probe in with the split thresholds.


class ProbingHub(FakeHub):
    """FakeHub that also answers the liveness `exec` with a fixed verdict, so a
    device turn can be driven through the idle-suspect probe path in-process."""

    def __init__(self, verdict: str = "alive") -> None:
        super().__init__()
        self.verdict = verdict
        self.probe_calls = 0
        self.probe_topics: set[str] = set()
        self.probe_devices: set[str] = set()

    async def exec(self, device_id, argv, *, env=None, timeout=30, **kw):
        self.probe_calls += 1
        self.probe_devices.add(device_id)
        if env and "CHEESE_ALIVE_TOPIC" in env:
            self.probe_topics.add(env["CHEESE_ALIVE_TOPIC"])
        return {"exit": 0, "stdout": self.verdict, "stderr": "", "truncated": False}


def test_device_splits_the_two_timeout_layers_instead_of_collapsing_them():
    """The bug: one 900s value fed BOTH layers, so the only thing that ever fired
    was 'kill unconditionally at 900s' — a long-but-silent foreground command (a
    20-minute pytest emits no interim hook) died at minute 15. The layers must now
    be distinct, idle-suspect well below the hard ceiling, and the hard ceiling is
    the value TurnRunner reschedules its outer wall-clock wrap to."""
    prov = DeviceProvider(hub=FakeHub())
    assert prov._idle_suspect_s == 300.0
    assert prov._hard_ceiling_s == 10800.0
    assert prov._idle_suspect_s != prov._hard_ceiling_s
    assert prov.hard_ceiling_s == 10800.0  # what the outer wrap is told


async def test_confirm_alive_maps_the_probe_result_to_a_liveness_verdict():
    """`_confirm_alive` asks the box (over the hub's `exec`) whether a live `claude`
    still carries THIS topic. ONLY an explicit `dead` ends the turn; alive, unknown,
    a non-zero exit, or an exec that raised are all read as alive, so a link hiccup
    never false-kills a turn that is really still working. The probe is per-topic
    and targets the screen's own device — the SAME path for a co-located device and
    a remote one (it never branches on co-location)."""
    topic = uuid.uuid4()
    screen = HubScreen(
        sid="s1",
        device_id="dev-remote",
        command=[],
        token="t",
        agent_user_id=1,
        agent_handle="a",
        topic_id=topic,
    )

    class Hub:
        def __init__(self, result=None, boom=False) -> None:
            self.result = result
            self.boom = boom
            self.calls: list = []

        async def exec(self, device_id, argv, *, env=None, timeout=30, **kw):
            self.calls.append((device_id, env))
            if self.boom:
                raise RuntimeError("link down")
            return self.result

    async def confirm(result=None, boom=False):
        hub = Hub(result=result, boom=boom)
        prov = DeviceProvider(hub=hub)  # type: ignore[arg-type]
        return await prov._confirm_alive(screen), hub

    alive, hub = await confirm({"exit": 0, "stdout": "alive\n"})
    assert alive is True
    assert hub.calls[0][0] == "dev-remote"  # targets the screen's device
    assert hub.calls[0][1] == {"CHEESE_ALIVE_TOPIC": str(topic)}  # per-topic

    assert (await confirm({"exit": 0, "stdout": "dead\n"}))[0] is False
    assert (await confirm({"exit": 0, "stdout": "unknown\n"}))[0] is True
    # A non-zero exit is inconclusive, not death.
    assert (await confirm({"exit": 3, "stdout": "dead\n"}))[0] is True
    # An exec that raised (link hiccup / timeout) is not proof of death.
    assert (await confirm(boom=True))[0] is True


async def test_a_silent_but_alive_turn_survives_idle_suspect_and_ends_on_stop():
    """The core regression: a long foreground command emits only a first and a last
    hook, silent in between. Past idle-suspect the turn is re-probed; while the
    probe says the screen is alive the turn must NOT be killed — it runs to the
    Stop hook and ends normally."""
    hub = ProbingHub(verdict="alive")
    router = HookRouter()
    tid = uuid.uuid4()

    async def resolver(_p, _t):
        return ("dev1", 1, "agent-x")

    provider = DeviceProvider(
        hub=hub,  # type: ignore[arg-type]
        device_resolver=resolver,
        router=router,
        public_base="http://test",
        idle_suspect_s=0.05,
        hard_ceiling_s=5,
    )
    events, task = await _run(
        provider,
        project_id=uuid.uuid4(),
        topic_id=tid,
        prompt="run the tests",
        system_prompt="",
        resume_session_id=None,
    )
    await asyncio.sleep(0.05)  # resolve + open screen + send prompt + reach drain
    key = str(tid)
    # First hook = the prompt receipt / start of a long foreground command; then
    # the hooks go SILENT for the run — the window this fix has to survive.
    router.push(key, {"hook_event_name": "UserPromptSubmit", "prompt": "run the tests"})
    await asyncio.sleep(0.2)  # cross idle-suspect; the alive probe keeps it running
    assert hub.probe_calls >= 1, "idle-suspect must have re-probed liveness"
    assert not any(isinstance(e, AgentResult) and e.is_error for e in events)
    # The command finishes: the reply and the Stop hook arrive, ending the turn.
    router.push(key, {"hook_event_name": "MessageDisplay", "delta": "tests pass"})
    router.push(
        key, {"hook_event_name": "Stop", "last_assistant_message": "tests pass"}
    )
    await asyncio.wait_for(task, timeout=3)

    assert isinstance(events[-1], AgentResult) and not events[-1].is_error
    assert events[-1].text == "tests pass"
    assert hub.probe_topics == {key}  # the probe was keyed on this topic


async def test_a_dead_screen_is_caught_by_the_probe_before_the_hard_ceiling():
    """A genuinely dead screen must be caught by the idle-suspect probe and end the
    turn promptly — not left running until the (many-hours) hard ceiling. The huge
    hard ceiling here would hang the test if idle-suspect + `_confirm_alive` didn't
    fire."""
    hub = ProbingHub(verdict="dead")
    router = HookRouter()
    tid = uuid.uuid4()

    async def resolver(_p, _t):
        return ("dev1", 1, "agent-x")

    provider = DeviceProvider(
        hub=hub,  # type: ignore[arg-type]
        device_resolver=resolver,
        router=router,
        public_base="http://test",
        idle_suspect_s=0.05,
        hard_ceiling_s=100,  # would time the test out if idle-suspect didn't fire
    )
    events, task = await _run(
        provider,
        project_id=uuid.uuid4(),
        topic_id=tid,
        prompt="hi",
        system_prompt="",
        resume_session_id=None,
    )
    await asyncio.sleep(0.05)
    router.push(str(tid), {"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    await asyncio.wait_for(task, timeout=3)  # ends via the probe, NOT the 100s ceiling

    assert isinstance(events[-1], AgentResult) and events[-1].is_error
    assert events[-1].text == provider._timeout_message
    assert hub.probe_calls >= 1


async def test_each_launch_ships_a_fresh_now_based_token_expiry():
    """The device screen env must carry ``CHEESE_TOKEN_EXPIRES`` — the expiry the
    launcher stamps against the inner tmux session it creates, so a later launch
    retires a session whose baked credential has DIED (a bare `claude` reads its
    model credential once and never re-reads it) instead of adopting the corpse.

    The regression: a relaunch used to reuse a cached, already-expired credential
    (the inner session survived, and the freshly minted token never reached the
    running process — the 407 that #385's TTL bump only delayed). So the value
    shipped here must be minted from NOW: strictly in the future and no further
    out than a session, never a reused past expiry."""
    import time

    from app.domain.agent.hooks_substrate import SESSION_TOKEN_TTL_S

    hub = FakeHub()
    router = HookRouter()
    provider = _provider(hub, router, uuid.uuid4())
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    key = str(topic_id)

    before = int(time.time())
    _events, task = await _run(
        provider,
        project_id=project_id,
        topic_id=topic_id,
        prompt="hi",
        system_prompt="",
        resume_session_id=None,
    )
    await asyncio.sleep(0.05)
    router.push(key, {"hook_event_name": "Stop", "last_assistant_message": "ok"})
    await asyncio.wait_for(task, timeout=5)

    assert hub.envs and hub.envs[0] is not None
    raw = hub.envs[0].get("CHEESE_TOKEN_EXPIRES")
    assert raw is not None, "the launch env must carry the credential expiry"
    exp = int(raw)
    # Minted from now — in the future (the reused corpse had exp in the PAST) and
    # within a session's reach, never an unbounded or stale value.
    assert before < exp <= int(time.time()) + SESSION_TOKEN_TTL_S + 5


# --- #388 缺陷二: credential freshness is part of the reuse decision -------------
# `_confirm_alive` is a process-tree probe: it says a `claude` is running, never
# whether the credential that `claude` was LAUNCHED with is still good. A bare
# `claude` reads that credential once and never re-reads it, and a reused screen is
# only reasserted (a cheeselet hot-reload), never relaunched — so a live process on
# a dead credential is 401/407'd every turn while the probe reports it healthy, and
# the screen is reused forever. The backend already stamps the credential's expiry
# (#386's CHEESE_TOKEN_EXPIRES); these pin that it now gates reuse too, so an
# expired-credential screen is RETIRED and reopened rather than adopted.


class ReuseGateHub(FakeHub):
    """Hands out a fresh sid per open and records close_screen, but its liveness
    probe ALWAYS answers `alive` — so the only thing that can retire a reused
    screen here is the credential-freshness gate, never the process probe."""

    def __init__(self) -> None:
        super().__init__()
        self.closed: list[str] = []
        self._sid_seq = 0

    async def open_screen(self, device_id, command, source, **kw) -> HubScreen:
        self._sid_seq += 1
        screen = HubScreen(
            sid=f"s{self._sid_seq}",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
            hook_key=kw.get("hook_key", ""),
        )
        self.opened.append(screen)
        self.envs.append(kw.get("env"))
        return screen

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        # The launcher-ship exec carries the script on stdin; the liveness probe
        # carries none. Only the probe returns a verdict — and here it is always
        # `alive`, isolating the credential gate as the sole retirement cause.
        self.execs.append((argv, stdin))
        out = "" if stdin is not None else "alive"
        return {"stdout": out, "stderr": "", "exit": 0, "truncated": False}

    async def close_screen(self, device_id, sid) -> bool:
        self.closed.append(sid)
        self.opened = [s for s in self.opened if s.sid != sid]
        return True


@pytest.mark.anyio
async def test_a_reused_screen_whose_birth_credential_expired_is_retired_not_adopted(
    monkeypatch,
):
    import time

    from app.core.config import settings

    monkeypatch.setattr(settings, "subscription_enabled", False)
    monkeypatch.setattr(settings, "device_shared_workspace_host_root", "")
    hub = ReuseGateHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
    pid, tid = uuid.uuid4(), uuid.uuid4()

    async def ensure() -> HubScreen:
        return await provider._ensure_screen(
            device_id="dev1",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=pid,
            topic_id=tid,
            token="tok",
            model=None,
            env=None,
        )

    first = await ensure()
    assert first.sid == "s1"
    # The backend stamped a live expiry on the freshly-Spawned screen.
    assert isinstance(first.credential_expires, int)
    assert first.credential_expires > int(time.time())

    # The credential this screen was BORN with has since died.
    first.credential_expires = int(time.time()) - 1
    second = await ensure()

    # It is RETIRED (close_screen makes the connector forget the sid) and reopened
    # under a fresh sid the connector must Spawn with THIS launch's live credential
    # — never reasserted into the corpse (which would only hot-reload the cheeselet).
    assert hub.closed == ["s1"]
    assert hub.reasserted == []
    assert second.sid == "s2"
    assert second.credential_expires > int(time.time())


@pytest.mark.anyio
async def test_a_reused_screen_with_a_live_credential_is_adopted(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "subscription_enabled", False)
    monkeypatch.setattr(settings, "device_shared_workspace_host_root", "")
    hub = ReuseGateHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")
    pid, tid = uuid.uuid4(), uuid.uuid4()

    async def ensure() -> HubScreen:
        return await provider._ensure_screen(
            device_id="dev1",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=pid,
            topic_id=tid,
            token="tok",
            model=None,
            env=None,
        )

    first = await ensure()
    second = await ensure()

    # A still-good credential means normal reuse: the SAME screen, reasserted, never
    # closed — no churn, and an in-flight turn is never interrupted.
    assert second is first
    assert hub.reasserted == ["s1"]
    assert hub.closed == []
    assert [s.sid for s in hub.opened] == ["s1"]


def test_topic_credential_expiry_reads_the_live_screens_stamp():
    """The runtime fuse's lookup (#388 缺陷一): the credential expiry of a topic's
    LIVE device screen, or None when it has none online / unrecorded — so a topic on
    the local tmux/SDK path (no device screen) never perturbs the fuse."""
    from app.domain.agent.device_provider import topic_credential_expiry

    tid = uuid.uuid4()

    class Hub:
        def __init__(self, screens: dict, online: set) -> None:
            self._screens = screens
            self._online = online

        def screens_for_topic(self, topic_id):
            return list(self._screens.get(topic_id, []))

        def is_online(self, device_id):
            return device_id in self._online

    def _screen_with(device_id: str, exp: int | None) -> HubScreen:
        s = HubScreen(
            sid="s",
            device_id=device_id,
            command=[],
            token="t",
            agent_user_id=1,
            agent_handle="a",
            topic_id=tid,
        )
        s.credential_expires = exp
        return s

    # A live screen with a recorded expiry → that value.
    hub = Hub({tid: [_screen_with("dev1", 12345)]}, {"dev1"})
    assert topic_credential_expiry(tid, hub=hub) == 12345  # type: ignore[arg-type]

    # No screen for the topic (local backend, or none open) → None.
    assert topic_credential_expiry(tid, hub=Hub({}, set())) is None  # type: ignore[arg-type]

    # An OFFLINE device's screen doesn't count — it isn't the one running the turn.
    hub_off = Hub({tid: [_screen_with("devX", 999)]}, set())
    assert topic_credential_expiry(tid, hub=hub_off) is None  # type: ignore[arg-type]

    # A screen whose expiry was never recorded is skipped, not read as 0.
    hub_none = Hub({tid: [_screen_with("dev1", None)]}, {"dev1"})
    assert topic_credential_expiry(tid, hub=hub_none) is None  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_a_device_with_its_own_identity_gets_the_machine_ticket_signal(
    monkeypatch, tmp_path
):
    """The dev box's shape: co-located, no tunnel, but it brings a ccproxy
    identity on its device row. The launcher must be told to hand claude the
    DEVICE's ticket (CHEESE_MACHINE_TICKET) — without the signal the reconcile
    injects our scoped token and every turn dies upstream as
    `401 Invalid bearer token` (measured on the box, 2026-08-15)."""
    _subscription_settings(monkeypatch, tmp_path)

    async def own_identity(_self, _device_id):
        return "m161:pw161"

    monkeypatch.setattr(DeviceProvider, "_device_ccproxy_upstream", own_identity)
    hub, _project, _topic = await _subscription_screen(monkeypatch, co_located=True)

    assert hub.env["CHEESE_MACHINE_TICKET"] == "1"
    # The identity itself must NOT travel: the machine authenticates the meter
    # hop with its scoped token, and admission tells the meter the identity.
    assert "m161" not in repr(hub.env)


@pytest.mark.anyio
async def test_a_device_without_identity_keeps_the_swap_path(monkeypatch, tmp_path):
    """No identity, no signal: the reconcile keeps asserting our scoped token,
    which the meter swaps for the platform credential — today's behaviour for
    every laptop-class device."""
    _subscription_settings(monkeypatch, tmp_path)
    hub, _project, _topic = await _subscription_screen(monkeypatch, co_located=True)

    assert "CHEESE_MACHINE_TICKET" not in hub.env


def test_tunnel_port_is_per_topic_and_stable():
    """#425: one fixed helper port made two concurrent topics on one remote
    machine race for the same bind. The port must differ across topics and be
    stable for one topic (claude bakes its HTTPS_PROXY at launch, #385, so a
    reused screen must re-derive the same value)."""
    from app.domain.agent.device_provider import (
        connect_transport,
        tunnel_port_for_topic,
    )

    # FIXED ids, not uuid4(): the port space is 2000 wide (base + sha1 % 2000),
    # so two random topics collide on ~1 run in 2000 and the assertion below has
    # no way to tell that apart from the bug it guards. It flaked exactly that
    # way on CI (both drew 10339). These two are checked to land apart.
    a = uuid.UUID("11111111-1111-4111-8111-111111111111")
    b = uuid.UUID("22222222-2222-4222-8222-222222222222")
    pa, pb = tunnel_port_for_topic(a), tunnel_port_for_topic(b)
    assert pa == tunnel_port_for_topic(a), "not stable for the same topic"
    assert pa != pb, "distinct topics must not share a port"
    url = connect_transport(session_token="t", via_tunnel=True, tunnel_port=pa)
    assert url == f"http://127.0.0.1:{pa}"
