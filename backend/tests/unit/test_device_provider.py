"""DeviceProvider: turn orchestration + hook→AgentEvent translation (injected hub)."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceProvider
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.service import AgentMessage, AgentResult, AgentSessionInfo
from app.domain.device.repository import Device
from app.domain.device.supply import Supply


class FakeHub:
    """Minimal DeviceHub stand-in recording what the provider drives."""

    def __init__(self) -> None:
        self.opened: list[HubScreen] = []
        self.prompts: list[list] = []

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
        return screen

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
