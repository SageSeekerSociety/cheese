"""DeviceProvider: turn orchestration + hook→AgentEvent translation (injected hub)."""

import asyncio
import uuid

import pytest

from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceProvider
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.service import AgentMessage, AgentResult, AgentSessionInfo


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
        turn_timeout_s=5,
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
        turn_timeout_s=5,
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

    class Repo:
        def __init__(self, session):
            self._provisioned = session

        async def is_provisioned_device(self, device_id):
            return device_id == "microcloud-machine"

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "app.domain.machine.repositories.ProjectMachineRepository", Repo
    )
    provider = DeviceProvider(session_factory=Session)

    assert await provider._is_co_located("microcloud-machine") is False
    assert await provider._is_co_located("the-box-itself") is True


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
