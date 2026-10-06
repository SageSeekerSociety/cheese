"""What a room sees when pi does a real turn.

The fixture is a recording, not a construction: one GLM-5.2 turn through
``pi --mode rpc`` that wrote a file, read one, edited it and ran a shell
command. Entries invented by hand would agree with whatever the translator
happens to do; these came out of the harness we are translating.
"""

import json
from pathlib import Path

from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentMessage,
    AgentResult,
    AgentStepFailed,
    AgentStepOutput,
    AgentToolUse,
)

RECORDING = json.loads((Path(__file__).parent / "fixtures/pi-entries.json").read_text())

# The Chinese the turn was asked to write verbatim, including the punctuation
# that a transport splitting on anything but LF would cut in half.
VERBATIM = (
    "芝士平台的房间是长期存在的，一件事做完就归档；工作区可以随时丢弃，"
    "真正要活过一轮的东西只有三个出口——git、房间、记忆。"
    "标点也要保留：，。；：「」——（）！？\n"
)


def landed(session_id="session-1"):
    assembler = Assembler(session_id, harness="pi")
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


# ---- 挂了的一步 ----


def _returns():
    return [
        entry
        for entry in RECORDING["entries"]
        if entry.get("message", {}).get("role") == "toolResult"
    ]


def test_every_tool_call_carries_the_id_its_result_names():
    called = {tool.call_id for tool in landed() if isinstance(tool, AgentToolUse)}
    returned = {entry["message"]["toolCallId"] for entry in _returns()}
    assert returned, "the recording has no tool returns to pair with"
    assert returned <= called


def test_a_tool_that_worked_is_not_marked_failed_and_its_output_goes_on_its_step():
    # 录下来这一轮四次调用全成功 —— 没有一步变红，每一截输出都落在它自己那一步上。
    events = landed()
    assert not [e for e in events if isinstance(e, AgentStepFailed)]
    called = {e.call_id for e in events if isinstance(e, AgentToolUse)}
    outputs = [e for e in events if isinstance(e, AgentStepOutput)]
    assert outputs, "the recording's tools printed nothing"
    assert {e.call_id for e in outputs} <= called


def test_a_failed_tool_says_which_step_failed_and_why():
    entry = json.loads(json.dumps(_returns()[0]))
    entry["message"]["isError"] = True
    entry["message"]["content"] = [{"type": "text", "text": "pandoc: not found"}]

    events = Assembler("session-1", harness="pi").accept(entry)

    assert [type(e) for e in events] == [AgentStepFailed, AgentStepOutput]
    assert events[0].call_id == entry["message"]["toolCallId"]
    assert events[0].text == "pandoc: not found"
    assert events[1].text == "pandoc: not found"


def test_a_long_failure_keeps_its_ending():
    entry = json.loads(json.dumps(_returns()[0]))
    entry["message"]["isError"] = True
    entry["message"]["content"] = [
        {"type": "text", "text": "x " * 2000 + "FAILED test_y"}
    ]

    failed = Assembler("session-1", harness="pi").accept(entry)[0]

    assert failed.text.endswith("FAILED test_y")
    assert len(failed.text) == STEP_ERROR_MAX
    # Cut at the front, and saying so: it does not open on half a word.
    assert failed.text.startswith("…")


# ---- pi 1.0 记进日志、而我们不翻的那两条 ----
#
# Both shapes below were read off a real 1.0.0 binary on 2026-10-02, not
# invented: the pin moved 0.85.1 -> 1.0.0 and these two entries appear in its
# log where 0.85.1 wrote neither. What the test holds is that they land
# nothing, so a later translator change that starts reading them is a decision
# rather than an accident.


def test_a_system_message_entry_lands_nothing():
    """1.0 records the opening prompt as a `system` message entry.

    It is a message, so it reaches the role split — and it is neither the
    person's nor the agent's.
    """
    entry = {
        "type": "message",
        "id": "120b2497",
        "parentId": "b2265ecb",
        "timestamp": "2026-10-02T17:17:02.318Z",
        "message": {
            "role": "system",
            "content": "",
            "sections": {
                "preamble": "You are the probe.",
                "cwd": "<cwd>\n/tmp/probe/workspace\n</cwd>",
            },
            "timestamp": 1790961422318,
        },
    }

    assert Assembler("session-1", harness="pi").accept(entry) == []


def test_a_context_edit_entry_lands_nothing():
    """1.0 drops a retried call from the context with a `context_edit` entry.

    ``replacement`` is null: the entry removes the failed call rather than
    rewriting it. Nothing about the turn changes for the room — the retry it
    belongs to is already on the log as the runner's own mark.
    """
    entry = {
        "type": "context_edit",
        "id": "9645501e",
        "parentId": "657b9f63",
        "timestamp": "2026-10-02T17:17:02.388Z",
        "targetId": "657b9f63",
        "replacement": None,
    }

    assert Assembler("session-1", harness="pi").accept(entry) == []
