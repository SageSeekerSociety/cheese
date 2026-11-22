"""A channel's main line that sat idle past its model's cache lifetime is
compacted before its next turn, and that turn is told the whole state again.

Idle that long, the next call re-reads the whole context at full price anyway:
compacting then costs only the summary, and every later call reads the summary
instead of the history. What a compacted conversation has lost is the opening
it was told once, so the input after the compaction carries all of it, not
only what changed. Still warm, nothing is compacted.
"""

import time
import uuid

import pytest

from app.api import deps as session_turn_deps
from app.core import model_roles
from app.domain.agent import gateway_catalog
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.gateway import GatewayModel
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

#: A section of the opening that does not change in this project.
_SNAPSHOT = "## 项目成员"


class Screen(StubChannel):
    """Every answer reads a large context; `/compact` is answered as Claude
    Code answers it."""

    def arrive(self, topic_id, message, *, agent=None) -> None:
        content = message["message"]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        if text != "/compact":
            super().arrive(topic_id, message, agent=agent)
            return
        self.last_prompt = text
        self.took(topic_id, text, agent=agent)
        self.record(
            topic_id,
            agent=agent,
            type="system",
            subtype="compact_boundary",
            compact_metadata={"trigger": "manual", "pre_tokens": 150_000},
        )
        self.stops(topic_id, "", agent=agent, user_message_uuid=message["uuid"])

    def says(self, topic_id, text, *, agent=None, **extra) -> None:
        self.record(
            topic_id,
            agent=agent,
            type="assistant",
            parent_tool_use_id=None,
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 150_000, "output_tokens": 50},
            },
            **extra,
        )

    def inputs(self) -> list[str]:
        """Every user message the session was handed, in order."""
        (session,) = self.sessions.values()
        return [
            m["message"]["content"]
            if isinstance(m["message"]["content"], str)
            else m["message"]["content"][0]["text"]
            for m in session.written
            if m.get("type") == "user"
        ]


async def _two_turns(factory, tmp_path, monkeypatch, *, lifetime_s: int):
    """Two turns of a channel's main line on a model whose cache lasts
    ``lifetime_s``, a little over a second apart; what the session was handed."""
    model = model_roles.model("agent_model")
    monkeypatch.setattr(
        gateway_catalog,
        "snapshot",
        lambda: [
            GatewayModel(
                id=model,
                label=model,
                selectable=True,
                priced=True,
                cache_lifetime_s=lifetime_s,
            )
        ],
    )
    screen = Screen()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id

    async def turn(content: str) -> None:
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content=content, summon=True
        ):
            pass
        await settle_turn(svc, topic_id)

    await turn("开始吧")
    time.sleep(1.2)
    await turn(f"接着做 {uuid.uuid4().hex[:6]}")
    return screen.inputs()


async def test_a_cold_main_line_is_compacted_and_told_the_whole_state(
    client, tmp_path, monkeypatch
):
    inputs = client.portal.call(
        lambda: _two_turns(
            client.test_request_factory, tmp_path, monkeypatch, lifetime_s=1
        )
    )

    first, compact, second = inputs
    assert _SNAPSHOT in first
    assert compact == "/compact", inputs
    assert "接着做" in second
    assert _SNAPSHOT in second, "压缩之后整份现状要重说"


async def test_a_main_line_still_warm_is_not_compacted(client, tmp_path, monkeypatch):
    inputs = client.portal.call(
        lambda: _two_turns(
            client.test_request_factory, tmp_path, monkeypatch, lifetime_s=3600
        )
    )

    first, second = inputs
    assert "/compact" not in inputs
    assert _SNAPSHOT in first
    assert _SNAPSHOT not in second, "没压缩，没变的现状不重说"
