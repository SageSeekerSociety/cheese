"""The room's agent and a person's 芝士 call some objects by the same name
(`cheese_notify`, `cheese_web`, `cheese_memory`, `cheese_library`): which one a
call is follows the credential it is made under, and each session is handed only
the verbs that are its own."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

from app.domain.agent.document import session as document
from app.domain.agent.harness.pi import catalog
from app.domain.agent.personal import session as personal

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
_ROOM = "11111111-1111-4111-8111-111111111111"


def _load():
    loader = SourceFileLoader("cheese_two_seats", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Host:
    def __init__(self, environ=None, answers=None):
        self.environ = {
            "CHEESE_TOPIC": _ROOM,
            "CHEESE_PROJECT": "p1",
            **(environ or {}),
        }
        self.answers = answers or {}
        self.requests: list[dict] = []
        self.doc_versions: dict = {}

    def request(self, plan):
        self.requests.append(plan)
        return {"data": self.answers.get(plan["path"].split("?")[0], {"text": "好"})}


def test_the_room_notifies_the_project_and_a_persons_cheese_notifies_the_person():
    room, person = Host(), Host(environ={"CHEESE_TOPIC": ""})

    cheese.run_platform_tool("cheese_notify", {"title": "看一眼"}, room)
    cheese.run_platform_tool(
        "cheese_notify", {"text": "该交了"}, person, delegated=True
    )

    assert [p["path"] for p in room.requests] == ["/projects/p1/alerts"]
    assert [p["path"] for p in person.requests] == ["/assistant/agent/notify-me"]
    assert person.requests[0]["body"] == {"text": "该交了"}


def test_a_persons_cheese_reads_a_page_only_through_the_gate():
    room = Host(answers={"/fetch": {"ok": True, "text": "正文"}})
    person = Host(
        environ={"CHEESE_TOPIC": ""},
        answers={"/assistant/agent/review": {"allowed": False, "reason": "用户没要求"}},
    )

    cheese.run_platform_tool(
        "cheese_web", {"action": "fetch", "url": "https://example.test"}, room
    )
    said = cheese.run_platform_tool(
        "cheese_web",
        {"action": "fetch", "url": "https://example.test"},
        person,
        delegated=True,
    )

    assert [p["path"] for p in room.requests] == ["/fetch"]
    assert [p["path"] for p in person.requests] == ["/assistant/agent/review"]
    assert person.requests[0]["body"]["kind"] == "fetch"
    assert "没有做" in said


def _verbs(names, tool):
    [row] = [s for s in catalog.schemas_of(list(names)) if s["name"] == tool]
    return sorted(row["inputSchema"]["properties"]["action"]["enum"])


def test_each_session_is_handed_only_its_own_memory_and_library_verbs():
    assert _verbs(personal.TOOLS, "cheese_memory") == ["forget", "read", "save"]
    assert _verbs(personal.TOOLS, "cheese_library") == ["give", "take"]
    assert _verbs(document.TOOLS, "cheese_memory") == ["project"]
    assert _verbs(document.TOOLS, "cheese_library") == ["read"]


def test_the_rooms_library_has_no_verb_of_a_persons():
    try:
        cheese.run_platform_tool(
            "cheese_library", {"action": "give", "path": "a.png"}, Host()
        )
    except cheese.PlatformToolError as refused:
        assert str(refused) == "cheese_library 要 action：list"
    else:
        raise AssertionError("the room's library has no give")
