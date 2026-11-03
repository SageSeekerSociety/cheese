"""A tool for one object: `action` picks the verb, and each verb takes its own
parameters (`_object` in `sandbox/cheese`).

What an agent can observe is the schema it is handed and what a call does: a
call the verb cannot take is refused before anything is sent, with what to
pass instead, and a call it can take runs that verb. A call written down under
a name the table no longer has reads as the action it became
(`session_record.tool_keys`).
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.session_record import tool_keys

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_platform_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Host:
    environ = {"CHEESE_TOPIC": "room-1", "CHEESE_PROJECT": "project-1"}

    def __init__(self):
        self.requests: list[dict] = []
        self.doc_versions: dict = {}

    def request(self, plan):
        self.requests.append(plan)
        return {"data": {}}


def _gadget():
    """An object with two verbs that share a parameter and one that takes
    none."""
    return cheese._object(
        "cheese_gadget",
        "一样东西。",
        {
            "make": cheese._verb(
                "做一个。",
                {
                    "title": cheese._text("标题"),
                    "note": cheese._text("附一句"),
                },
                ["title"],
            ),
            "rename": cheese._verb(
                "改名。",
                {"title": cheese._text("标题")},
                ["title"],
            ),
            "drop": cheese._verb("扔掉。"),
        },
    )


@pytest.fixture
def gadget(monkeypatch):
    """The room's table with the object on it, each verb answering with the
    arguments it was run with."""
    table = cheese.ToolTable((*cheese.PLATFORM_TOOLS.schemas(), _gadget()))
    monkeypatch.setattr(cheese, "PLATFORM_TOOLS", table)
    for verb in ("make", "rename", "drop"):
        monkeypatch.setitem(
            cheese._STEPS,
            f"cheese_gadget.{verb}",
            lambda args, host, verb=verb: f"{verb} {sorted(args.items())}",
        )
    return table


def test_the_schema_is_flat_and_says_which_verb_takes_what(gadget):
    """One object, one entry: `action` first and the only thing every call
    must carry; the verbs' parameters side by side, each saying whose it is."""
    [row] = [t for t in gadget.schemas() if t["name"] == "cheese_gadget"]
    schema = row["inputSchema"]

    assert set(row) == {"name", "description", "inputSchema"}
    assert list(schema["properties"])[0] == "action"
    assert schema["properties"]["action"]["enum"] == ["make", "rename", "drop"]
    assert schema["required"] == ["action"]
    assert not {"oneOf", "anyOf", "allOf"} & set(schema)
    assert schema["properties"]["title"]["description"] == "make、rename（必填）：标题"
    assert schema["properties"]["note"]["description"] == "make：附一句"
    for verb in ("make：做一个。", "rename：改名。", "drop：扔掉。"):
        assert verb in row["description"]


@pytest.mark.parametrize(
    ("args", "said"),
    [
        ({}, "cheese_gadget 要 action：make / rename / drop"),
        ({"action": "polish"}, "cheese_gadget 要 action：make / rename / drop"),
        ({"action": ["make"]}, "cheese_gadget 要 action：make / rename / drop"),
        ({"action": "make"}, "cheese_gadget(action=make) 缺少参数：title"),
        (
            {"action": "rename", "title": "新名字", "note": "顺手"},
            "cheese_gadget(action=rename) 不收 note：note 是 "
            "cheese_gadget(action=make) 的参数。"
            "cheese_gadget(action=rename) 收的是：title。",
        ),
        (
            {"action": "drop", "reason": "坏了"},
            "cheese_gadget(action=drop) 不收 reason：没有哪一件收 reason。"
            "cheese_gadget(action=drop) 收的是：不收参数。",
        ),
    ],
)
def test_a_call_its_verb_cannot_take_is_refused_before_anything_is_sent(
    gadget, args, said
):
    host = Host()

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool("cheese_gadget", args, host)

    assert str(refused.value) == said
    assert host.requests == []


def test_a_call_runs_the_verb_it_names(gadget):
    """An empty value of another verb's parameter is as good as left out: it
    asks for nothing, so it is neither refused nor passed on as a request."""
    host = Host()

    said = cheese.run_platform_tool(
        "cheese_gadget", {"action": "rename", "title": "新名字", "note": ""}, host
    )

    assert said.startswith("rename ")
    assert "'新名字'" in said


@pytest.fixture
def folded(monkeypatch):
    """Two tools that were folded into one object: one renamed, and one whose
    name the object kept for its first verb."""
    monkeypatch.setattr(
        tool_keys,
        "RETIRED",
        {
            "cheese_gadget_rename": "cheese_gadget.rename",
            "cheese_gadget": "cheese_gadget.make",
        },
    )


def test_a_recorded_call_is_read_as_its_action(folded):
    assert tool_keys.tool_key("cheese_gadget", {"action": "drop"}) == (
        "cheese_gadget.drop"
    )
    assert tool_keys.tool_key("cheese_gadget", {"title": "旧的"}) == (
        "cheese_gadget.make"
    )
    assert tool_keys.tool_key("cheese_gadget_rename", {}) == "cheese_gadget.rename"
    assert tool_keys.tool_key("chat_send", {"content": "在"}) == "chat_send"
