"""What a room sees when pi does a real turn.

The fixture is a recording, not a construction: one GLM-5.2 turn through
``pi --mode rpc`` that wrote a file, read one, edited it and ran a shell
command. Entries invented by hand would agree with whatever the translator
happens to do; these came out of the harness we are translating.
"""

import json
from pathlib import Path

from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse

RECORDING = json.loads((Path(__file__).parent / "fixtures/pi-entries.json").read_text())

# The Chinese the turn was asked to write verbatim, including the punctuation
# that a transport splitting on anything but LF would cut in half.
VERBATIM = (
    "芝士平台的房间是长期存在的，一件事做完就归档；工作区可以随时丢弃，"
    "真正要活过一轮的东西只有三个出口——git、房间、记忆。"
    "标点也要保留：，。；：「」——（）！？\n"
)


def landed(session_id="session-1"):
    assembler = Assembler(session_id)
    events = []
    for entry in RECORDING["entries"]:
        events.extend(assembler.accept(entry))
    return events


def test_the_room_sees_what_the_agent_said_and_every_tool_it_used():
    events = landed()
    tools = [event for event in events if isinstance(event, AgentToolUse)]
    assert [tool.name for tool in tools] == ["write", "read", "edit", "bash"]
    written = next(tool for tool in tools if tool.name == "write")
    # Chinese survives the whole path: prompt in, tool arguments out.
    assert written.input["content"] == VERBATIM
    assert [type(event) for event in events].count(AgentMessage) >= 3
    assert all(
        isinstance(event, AgentMessage) and event.text
        for event in events
        if isinstance(event, AgentMessage)
    )


def test_thinking_never_reaches_the_timeline():
    thinking = next(
        part
        for entry in RECORDING["entries"]
        if entry["type"] == "message"
        for part in entry["message"].get("content", [])
        if part["type"] == "thinking"
    )
    assert thinking["thinking"], "the recording has no thinking to exclude"
    said = [e.text for e in landed() if isinstance(e, AgentMessage)]
    assert not any(thinking["thinking"] in text for text in said)


def test_one_turn_ends_once_and_bills_every_message_it_spent():
    events = landed()
    results = [event for event in events if isinstance(event, AgentResult)]
    assert len(results) == 1, "a tool call must not end the turn"
    result = results[0]
    assert result.session_id == "session-1"
    assert not result.is_error

    spent = sum(
        (entry["message"].get("usage") or {}).get("cost", {}).get("total", 0.0)
        for entry in RECORDING["entries"]
        if entry["type"] == "message" and entry["message"]["role"] == "assistant"
    )
    assert result.usage is not None
    # Reading usage off the final message alone would bill a fraction of this.
    assert result.usage.cost_usd == spent
    assert result.usage.output_tokens > 0
    assert result.usage.total_tokens > result.usage.output_tokens


def test_landing_the_same_entries_twice_is_harmless():
    once = landed()
    twice = landed()
    identity = [(type(event).__name__, getattr(event, "eid", None)) for event in once]
    assert identity == [
        (type(event).__name__, getattr(event, "eid", None)) for event in twice
    ]
    ids = [event.eid for event in once if getattr(event, "eid", None)]
    assert len(ids) == len(set(ids)), "two events cannot share one id"
