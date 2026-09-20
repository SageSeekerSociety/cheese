"""The Python half of the harness contract.

``backend/tests/fixtures/harness-contract/`` holds one scenario per file, and
``backend/tests/extension/harness-contract.test.ts`` reads the same directory
over in Node. The backend and the extension pi loads never import each other —
one is Python on the platform, the other TypeScript on a session machine — so a
set of files both are held to is the only thing that can keep them saying the
same thing. Neither side can be made green by editing the other, which is the
whole point.

A scenario is one of two things, and the shape check below refuses anything
else:

- a ``drive`` scenario names one of the six verbs and plays steps against
  ``ContractHarness``, so 「reading never consumes」 and 「interrupt is weaker
  than close」 stop being adjectives in a docstring;
- a ``harnesses`` scenario gives each harness's OWN records for the same
  moment and one list of platform events they must all come out as. Every
  harness this deployment runs needs a cell, and a cell that cannot be filled
  names a difference code from the closed list in ``vocabulary.json`` — a hole
  named rather than a hole hidden.

The records are fed through the real stream translators — ``MessageAssembler``
for Claude Code, the two ``Assembler``s for pi and Codex — never through a
stand-in, and never through a lower entry point than the one the platform
itself calls. A fixture checked against a double would agree with the double
and say nothing about what reaches a room; one checked against a fallback
branch would stay green while the path production takes broke.
"""

import dataclasses
import typing
import uuid

import pytest

from app.domain.agent import service
from app.domain.agent.capability import (
    BUILT_IN_DIFFERENCES,
    EVENT_DIFFERENCES,
    Difference,
)
from app.domain.agent.harness import HARNESSES, AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.claude_code.hook_events import MessageAssembler
from app.domain.agent.harness.codex.events import Assembler as CodexAssembler
from app.domain.agent.harness.pi.events import Assembler as PiAssembler
from tests.support.contract_harness import ContractHarness, fixtures, vocabulary

VOCABULARY = vocabulary()
FIXTURES = fixtures()
IDS = [f["name"].removesuffix(".json") for f in FIXTURES]

#: The session every scenario is played in. One id, so a fixture never has to
#: carry one.
SESSION = SessionRef(
    project_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    topic_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
)
OPENING = Opening(system_prompt="CONTRACT")

#: What the room calls each event. The word in a fixture, the class in the code.
KIND_OF = {name: entry["class"] for name, entry in VOCABULARY["events"].items()}


def _axis(axis: str) -> set[str]:
    """夹具里归在这条轴上的码。两个 reader 认的都是这一份。"""
    return {
        code for code, word in VOCABULARY["differences"].items() if word["axis"] == axis
    }


SCENARIO_KEYS = {
    "name",
    "verb",
    "why",
    "readers",
    "drive",
    "harnesses",
    "events",
    "extension",
    "xfail",
}
EXTENSION_KEYS = {"why", "tool", "status", "is_error", "resets_silence"}
READERS = {"python", "extension"}


def _drive_scenarios() -> list[dict]:
    return [f for f in FIXTURES if "drive" in f]


def _record_scenarios() -> list[dict]:
    return [f for f in FIXTURES if "harnesses" in f]


def _cells() -> list[tuple[dict, str]]:
    return [
        (scenario, name)
        for scenario in _record_scenarios()
        for name, cell in scenario["harnesses"].items()
        if "records" in cell
    ]


def _kind(event: object) -> str:
    for word, class_name in KIND_OF.items():
        if type(event).__name__ == class_name:
            return word
    raise AssertionError(f"{type(event).__name__} is in no word list")


def _translate(harness: str, records: list[dict]) -> list:
    """The records of one harness, through that harness's own real translator."""
    if harness == "claude-code":
        # ``MessageAssembler.translate``, not ``translate_hook``: the platform
        # reads this harness's records as a STREAM (hooks_substrate.py's
        # backlog pass feeds every record through one assembler), and that is
        # where a streamed message is reassembled from its flushes and a Stop
        # drains what is still buffered. ``translate_hook`` alone has a
        # MessageDisplay branch that says in its own comment it is the
        # fallback for payloads without the flush fields — checking the
        # contract against it would leave 「一条消息是一个协议边界」 free to
        # break with every fixture still green.
        assembler = MessageAssembler()
        return [event for r in records for event in assembler.translate(r)]
    if harness == "pi":
        assembler = PiAssembler("session-1")
        return [event for r in records for event in assembler.accept(r)]
    if harness == "codex":
        assembler = CodexAssembler()
        return [event for r in records for event in assembler.accept(r)]
    raise AssertionError(f"no translator wired for {harness}")


def _assert_events(produced: list, expected: list[dict]) -> None:
    assert [_kind(event) for event in produced] == [e["kind"] for e in expected]
    for event, wanted in zip(produced, expected, strict=True):
        for field, value in wanted.items():
            if field == "kind":
                continue
            assert getattr(event, field) == value, f"{wanted['kind']}.{field}"


# --- is the word list the room's actual vocabulary --------------------------


def test_the_word_list_is_every_event_the_room_has() -> None:
    """A kind the platform can produce and the fixtures cannot name is a kind
    no harness is ever asked about."""
    classes = {cls.__name__ for cls in typing.get_args(service.AgentEvent)}
    assert set(KIND_OF.values()) == classes


@pytest.mark.parametrize("word", sorted(VOCABULARY["events"]))
def test_a_word_lists_the_fields_that_event_cannot_do_without(word: str) -> None:
    """「字段齐」: the required list is the fields the dataclass has no default
    for, so renaming one or giving it a default turns this red rather than
    quietly loosening what a harness must supply."""
    cls = getattr(service, KIND_OF[word])
    required = {
        f.name
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert set(VOCABULARY["events"][word]["required"]) == required


def test_the_difference_codes_are_the_platforms_one_closed_list() -> None:
    """差异码是全平台一份，不是这个目录一份，而且轴也记在这一份里。

    这里的散文归这个文件（TypeScript 那侧只读得到 JSON），码本身归
    ``capability.Difference``（功能矩阵要拿它填格子）。两边分头长的那一天，一张
    矩阵会开始用另一张矩阵不认识的码——所以两份的成员必须一模一样。

    轴同理，而且更要紧：轴只写在 Python 里的时候，TypeScript 那个 reader 认全部
    八条，于是一个场景填一条「平台关不掉」的码，在 extension 那边绿、在这边红，
    一份夹具两个 reader 读出两份规矩。轴记进夹具，两侧才都读得到它。
    """
    assert set(VOCABULARY["differences"]) == {code.value for code in Difference}
    for code, word in VOCABULARY["differences"].items():
        assert word["why"].strip(), code
        assert word["axis"] in ("event", "built-in"), code
    assert _axis("event") == {code.value for code in EVENT_DIFFERENCES}
    assert _axis("built-in") == {code.value for code in BUILT_IN_DIFFERENCES}


def test_the_six_verbs_are_all_there_and_a_runtime_can_keep_them() -> None:
    """Six, not the five the docstring lists — ``deliver`` is the sixth, and its
    own docstring says it and ``send`` will be one call some day. Until they
    are, a harness has to answer both."""
    assert len(VOCABULARY["verbs"]) == 6
    for verb in VOCABULARY["verbs"]:
        assert hasattr(AgentRuntime, verb), verb
    # A runtime that declares no capability at all is still a runtime: that is
    # what makes the matrix's difference codes possible rather than a fiction.
    assert isinstance(ContractHarness(), AgentRuntime)


# --- is the set well formed -------------------------------------------------


@pytest.mark.parametrize("scenario", FIXTURES, ids=IDS)
def test_a_scenario_is_written_in_the_word_list(scenario: dict) -> None:
    assert set(scenario) <= SCENARIO_KEYS, set(scenario) - SCENARIO_KEYS
    assert scenario["why"].strip()
    assert set(scenario["readers"]) <= READERS and scenario["readers"]
    if "xfail" in scenario:
        # 名洞的那条：动词还不存在，所以既没有步骤可以驱动，也没有哪个骨架要回答
        # 它。它唯一的读者是下面那条守卫，动词一落地就红。
        assert "drive" not in scenario and "harnesses" not in scenario, scenario["name"]
    else:
        assert ("drive" in scenario) != ("harnesses" in scenario), scenario["name"]
    if "drive" in scenario:
        assert "events" not in scenario
    elif "harnesses" in scenario:
        for name, cell in scenario["harnesses"].items():
            assert set(cell) in ({"records"}, {"difference"}), name
            if "difference" in cell:
                # 事件那条轴上的五条，不是整份词汇表：另外三条说的是「平台关不掉
                # 骨架自带的某样东西」，填进一个场景里就是一句跨轴的胡话。轴读的
                # 是夹具，跟 TypeScript 那侧同一个来源。
                assert cell["difference"] in _axis("event"), name
        for event in scenario["events"]:
            word = VOCABULARY["events"][event["kind"]]
            assert set(word["required"]) <= set(event), event
    assert ("extension" in scenario) == ("extension" in scenario["readers"])
    if extension := scenario.get("extension"):
        assert set(extension) == EXTENSION_KEYS


def test_every_verb_has_a_scenario() -> None:
    """六个动词各一条. A verb with no scenario is a promise nothing checks."""
    covered = {f["verb"] for f in FIXTURES if "xfail" not in f}
    assert set(VOCABULARY["verbs"]) <= covered


def test_every_word_has_a_scenario() -> None:
    """A word in the list that no scenario uses is a cell of the matrix nobody
    has ever filled in."""
    used = {e["kind"] for f in _record_scenarios() for e in f["events"]}
    assert set(VOCABULARY["events"]) == used


@pytest.mark.parametrize(
    "scenario", _record_scenarios(), ids=[f["name"] for f in _record_scenarios()]
)
def test_every_harness_this_deployment_runs_has_a_cell(scenario: dict) -> None:
    """The matrix has no blanks: a harness added to ``HARNESSES`` has to answer
    every scenario, with its records or with a difference code."""
    assert set(scenario["harnesses"]) == set(HARNESSES)


# --- the six verbs, played --------------------------------------------------


@pytest.mark.parametrize(
    "scenario", _drive_scenarios(), ids=[f["name"] for f in _drive_scenarios()]
)
async def test_a_scenario_drives_the_six_verbs(scenario: dict) -> None:
    runtime = ContractHarness()
    await _play(runtime, scenario["drive"])


async def _play(runtime, steps: list[dict]) -> None:
    previous: object = None
    snapshot: list = []
    for index, step in enumerate(steps):
        where = f"step {index} ({step['call']})"
        call = step["call"]
        if call == "ensure":
            before = runtime.holds(SESSION.topic_id)
            held = await runtime.ensure(SESSION, OPENING)
            assert (not before) == step["opened"], where
            if before:
                # 「在不在；不在就起」: the one that was already there, not a
                # second conversation started underneath the caller.
                assert held is previous, where
            previous = held
        elif call == "send":
            answer = await runtime.send(
                SESSION,
                step["message"],
                OPENING,
                work_id=uuid.uuid4(),
                on_mark=lambda _: None,
            )
            # An ack, not an answer: a runtime handing back what the agent said
            # would make whoever holds the return value the owner of the turn.
            assert answer is step["returns"], where
        elif call == "deliver":
            landed = await runtime.deliver(SESSION.topic_id, step["text"])
            assert landed is step["returns"], where
        elif call == "interrupt":
            assert await runtime.interrupt(SESSION) is step["returns"], where
        elif call == "close":
            await runtime.close(SESSION)
        elif call == "holds":
            assert runtime.holds(SESSION.topic_id) is step["returns"], where
        elif call == "backlog.unread":
            snapshot = list(runtime.backlog(SESSION).unread())
            assert len(snapshot) == step["returns"], where
        elif call == "backlog.landed":
            runtime.backlog(SESSION).landed(through=snapshot[step["through"]].key)
        else:
            raise AssertionError(f"{where}: no such call in this contract")


# --- one moment, every harness's own words ----------------------------------


@pytest.mark.parametrize(
    ("scenario", "harness"),
    _cells(),
    ids=[f"{s['name'].removesuffix('.json')}-{h}" for s, h in _cells()],
)
def test_a_harness_says_the_same_thing_in_its_own_records(
    scenario: dict, harness: str
) -> None:
    produced = _translate(harness, scenario["harnesses"][harness]["records"])
    _assert_events(produced, scenario["events"])


def test_a_tool_that_failed_is_not_a_tool_that_returned() -> None:
    """#1096, stated over the set rather than inside one harness: whatever a
    harness calls them, the two must not come out as the same events. Collapse
    the two in either language and this is what says so."""
    failed = next(f for f in FIXTURES if f["name"] == "a-tool-that-failed.json")
    returned = next(f for f in FIXTURES if f["name"] == "a-tool-that-returned.json")
    assert failed["events"] != returned["events"]
    for harness in set(failed["harnesses"]) & set(returned["harnesses"]):
        cells = (failed["harnesses"][harness], returned["harnesses"][harness])
        if any("difference" in cell for cell in cells):
            continue
        assert _translate(harness, cells[0]["records"]) != _translate(
            harness, cells[1]["records"]
        ), harness
    assert failed["extension"]["is_error"] is not returned["extension"]["is_error"]


# --- the half that is not here yet ------------------------------------------


def test_recovery_still_reads_the_machine_and_not_the_platforms_copy() -> None:
    """结论 29 says recovery comes from the platform's copy. ``recover`` finds
    sessions that outlived this process on the MACHINE; there is no verb for
    rebuilding one from what the platform holds. When P18 adds it, this turns
    red, and the scenario naming the hole goes with it."""
    scenario = next(f for f in FIXTURES if "xfail" in f)
    assert scenario["xfail"].strip()
    assert not hasattr(AgentRuntime, scenario["verb"])
