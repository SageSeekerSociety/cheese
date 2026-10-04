"""The real pi channel records per-conversation outcomes at its I/O boundary
(FB-56 legacy③): an alive=true answer is life, an explicit alive=False
whose session_id matches the stored resume token exactly is death, and
everything else — a timeout at the first layer, an offline machine, a
missing or non-boolean alive, a missing or mismatched id — is unknown. The
hub double substitutes I/O only; the pointers are real rows in a real
database."""

from types import SimpleNamespace

import pytest

from app.domain.agent.harness.pi.channel import PiChannel
from app.domain.agent.harness.pi.runtime import PI
from app.domain.agent_session.services import AgentSessionService
from tests.turn_log import a_topic

DEVICE = "dev-outcomes"
CHANNEL = "test-pi-outcomes"


class _Hub:
    """I/O only: which machine is online, and what each state path answers."""

    def __init__(self, answers: dict[str, object]) -> None:
        self._answers = answers

    def is_online(self, machine: str) -> bool:
        return machine != "dev-offline"

    async def call_executor(self, machine, state, method, params, timeout=660):
        answer = self._answers[state]
        if isinstance(answer, BaseException):
            raise answer
        return answer


async def _place(db_factory, topic, agent, state, sid, machine=DEVICE) -> None:
    async with db_factory() as session:
        service = AgentSessionService(session)
        await service.remember(
            topic_id=topic, agent_handle=agent, resume_token=sid, harness=PI
        )
        await service.remember_place(
            topic_id=topic,
            agent_handle=agent,
            harness=PI,
            work_lease=None,
            runtime_location={
                "device_id": machine,
                "resource_id": f"r-{agent}",
                "channel": CHANNEL,
                "runtime": {"harness": PI, "state": state, "agent_handle": agent},
            },
        )
        await session.commit()


def _channel(hub: _Hub, db_factory) -> PiChannel:
    inner = SimpleNamespace(
        name=CHANNEL,
        deferred_work=None,
        builds_model_env=False,
        _session_factory=db_factory,
        _hub=hub,
    )
    # The executor is the launch path; discover never touches it.
    return PiChannel(inner, SimpleNamespace())


@pytest.mark.anyio
async def test_discover_records_each_conversations_own_outcome(db_factory):
    """A 答 alive=true（找到）；B 在第一层超时（unknown，不是死）；C 答
    alive=false 且 session_id 与所存精确一致（terminal）；D 答 alive=false
    但 session_id 对不上（unknown——缺绑定不收口）。"""
    topic = await a_topic(db_factory)
    await _place(db_factory, topic, "ka", "state-a", "sid-a")
    await _place(db_factory, topic, "kb", "state-b", "sid-b")
    await _place(db_factory, topic, "kc", "state-c", "sid-c")
    await _place(db_factory, topic, "kd", "state-d", "sid-d")
    await _place(db_factory, topic, "ke", "state-e", "sid-e")
    hub = _Hub(
        {
            "state-a": {"alive": True, "session_id": "sid-a", "working": False},
            "state-b": TimeoutError("first-layer timeout"),
            "state-c": {"alive": False, "session_id": "sid-c"},
            "state-d": {"alive": False, "session_id": "sid-somebody-else"},
            # hub 直通 RPC result、无字段校验：缺 alive 不是「假」。
            "state-e": {"session_id": "sid-e"},
        }
    )
    channel = _channel(hub, db_factory)

    handles = await channel.discover(None)

    assert [handle.session.agent_handle for handle in handles] == ["ka"]
    assert channel.last_outcomes[(topic, "ka", "sid-a")] == "alive"
    assert channel.last_outcomes[(topic, "kb", "sid-b")] == "unknown", "超时不是终态"
    assert channel.last_outcomes[(topic, "kc", "sid-c")] == "dead", (
        "绑定所存来源的终止回答才收口"
    )
    assert channel.last_outcomes[(topic, "kd", "sid-d")] == "unknown", (
        "session_id 不匹配：缺绑定不收口"
    )
    assert channel.last_outcomes[(topic, "ke", "sid-e")] == "unknown", (
        "缺 alive 字段即使 SID 匹配也不是终态"
    )


@pytest.mark.anyio
async def test_an_offline_machine_and_another_channels_pointer_say_nothing(
    db_factory,
):
    """机器离线 = unknown；别的 channel 的 pointer 不归这里管，连 unknown 都不记。"""
    topic = await a_topic(db_factory)
    await _place(db_factory, topic, "ka", "state-a", "sid-a", machine="dev-offline")
    await _place(db_factory, topic, "kb", "state-b", "sid-b")
    # kb 的 pointer 属于另一个 channel：不在本 channel 的权威范围。
    async with db_factory() as session:
        from sqlalchemy import update

        from app.domain.agent_session.models import AgentSession

        await session.execute(
            update(AgentSession)
            .where(AgentSession.topic_id == topic, AgentSession.agent_handle == "kb")
            .values(
                runtime_location={
                    "device_id": DEVICE,
                    "resource_id": "r-kb",
                    "channel": "somebody-else",
                    "runtime": {
                        "harness": PI,
                        "state": "state-b",
                        "agent_handle": "kb",
                    },
                }
            )
        )
        await session.commit()
    channel = _channel(_Hub({}), db_factory)

    handles = await channel.discover(None)

    assert handles == []
    assert channel.last_outcomes == {(topic, "ka", "sid-a"): "unknown"}
