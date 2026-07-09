"""DeviceProvider: turn orchestration + hook→AgentEvent translation (injected hub)."""

import asyncio
import uuid

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
    async def resolver(_project_id):
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

    async def resolver(_project_id):
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
