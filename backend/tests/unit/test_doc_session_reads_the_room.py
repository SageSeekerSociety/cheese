"""A document thread's 芝士 with the room's machine lent to it: the pinned pi,
started by the real launch, reading the room's checkout through the executor.

Rules held here:

* it reads the room's checkout as the room's agent left it;
* it has pi's tools that only read, and none that write a file or run a
  command;
* a file the project's settings deny its agent is denied to it too.
"""

import json
import uuid

import pytest

from app.api import doc_agent
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.pi.document import Launch
from app.domain.agent.harness.pi.handless import Answered
from tests.support.room_machine import room_machine
from tests.unit.test_personal_sessions import _ask, host, platform  # noqa: F401

PROMPT = "你是芝士。"


def _launch(machine: dict) -> Launch:
    project, thread = uuid.uuid4(), uuid.uuid4()
    return Launch(
        project_id=project,
        thread_id=thread,
        system_prompt=PROMPT,
        tools_path="/doc-agent/tools",
        tools=doc_agent.TOOLS,
        token=mint_scoped_token(
            project_id=str(project),
            topic_id=str(uuid.uuid4()),
            agent_handle="cheese",
            resource_id=str(thread),
        ),
        model="fixture-model",
        machine=machine,
    )


@pytest.mark.anyio
async def test_it_reads_the_rooms_checkout_and_cannot_change_it(
    host,  # noqa: F811
    platform,  # noqa: F811
    tmp_path,
):
    _hub, sessions = host
    checkout = tmp_path / "room"
    checkout.mkdir()
    (checkout / "NOTES.md").write_text("改到一半的第三节", encoding="utf-8")
    fake = platform(
        [{"tool": "read", "arguments": {"path": "NOTES.md"}}, {"text": "读到了。"}],
        tools_path="/doc-agent/tools",
    )
    with room_machine(tmp_path, checkout=checkout) as machine:
        events = await _ask(sessions, _launch(machine), "第三节写到哪了？")

    assert events[-1] == Answered("读到了。")
    names = {tool["function"]["name"] for tool in fake.requests[0]["tools"]}
    assert {"read", "ls", "find", "grep"} <= names
    assert not names & {"write", "edit", "bash"}
    assert "改到一半的第三节" in json.dumps(fake.requests[1], ensure_ascii=False)


@pytest.mark.anyio
async def test_a_file_the_project_denies_stays_unread(
    host,  # noqa: F811
    platform,  # noqa: F811
    tmp_path,
):
    _hub, sessions = host
    checkout = tmp_path / "room"
    (checkout / ".claude").mkdir(parents=True)
    (checkout / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Read(./secret.env)"]}})
    )
    (checkout / "secret.env").write_text("TOKEN=hunter2")
    fake = platform(
        [{"tool": "read", "arguments": {"path": "secret.env"}}, {"text": "读不了。"}],
        tools_path="/doc-agent/tools",
    )
    with room_machine(tmp_path, checkout=checkout) as machine:
        await _ask(sessions, _launch(machine), "密钥是多少？")

    assert len(fake.requests) == 2
    assert "hunter2" not in json.dumps(fake.requests[1], ensure_ascii=False)
