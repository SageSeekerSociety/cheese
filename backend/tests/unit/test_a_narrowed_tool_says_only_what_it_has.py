"""A session handed only some of an object's verbs is told only about those.

The tool's description is the object's name and one line per verb it was
handed, so a room whose deployment has not turned compacting on reads nothing
about compacting, and a document's 芝士 reads nothing about writing down or
forgetting the person's memory. A call to a verb it was not handed is told the
verbs it has, the way a call naming no verb is, whichever harness it came
through."""

import asyncio
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent.document.session import TOOLS as DOCUMENT_TOOLS
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.pi import catalog
from app.domain.agent.harness.pi import runner as pi_runner
from app.domain.agent.harness.pi.runner import Runner
from app.domain.agent.personal.session import TOOLS as PERSONAL_TOOLS

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
cheese = SourceFileLoader("cheese_cli_narrowed", str(_CHEESE)).load_module()

_ROOM_SESSION = (
    "cheese_session 要 action：status / environment / wait / note / deliver_at"
)


def _described(schemas: list[dict], name: str) -> str:
    return next(s["description"] for s in schemas if s["name"] == name)


def test_a_room_without_the_switch_reads_nothing_about_compacting():
    whole = _described(cheese.PLATFORM_TOOLS.schemas(), "cheese_session")
    offered = _described(cheese.offered_tools({}), "cheese_session")

    assert "压缩" in whole
    assert "压缩" not in offered
    assert "等它就绪" in offered, "the verbs it kept are still described"


def test_a_documents_memory_and_library_say_only_what_it_may_do():
    document = catalog.schemas_of(list(DOCUMENT_TOOLS))
    person = catalog.schemas_of(list(PERSONAL_TOOLS))

    memory = _described(document, "cheese_memory")
    assert "项目共享记忆" in memory
    assert "记下" not in memory and "删掉" not in memory
    assert "删掉" in _described(person, "cheese_memory")
    # The person's own memory is said to be theirs on each verb that touches
    # it, and not on the project's.
    verbs = cheese.DELEGATED_TOOLS.actions("cheese_memory")
    for verb in ("save", "read", "forget"):
        assert "只有用户自己的面板里用" in verbs[verb]["description"], verb
    assert "只有用户自己的面板里用" not in verbs["project"]["description"]
    assert "只有用户自己的面板里用" not in memory

    library = _described(document, "cheese_library")
    assert "交给用户" not in library and "放到你的电脑上" not in library
    assert "读消息里附带的文件" not in _described(person, "cheese_library")


def test_a_verb_the_session_was_not_handed_is_told_the_ones_it_has(
    tmp_path, monkeypatch
):
    ran: list = []
    monkeypatch.setattr(
        pi_runner.catalog,
        "run_platform_tool",
        lambda tool, arguments, host, *, delegated=False: ran.append(tool) or "好",
    )
    session = Runner(tmp_path / "pi")
    session.tools = {"names": list(DOCUMENT_TOOLS)}
    session.platform_token = "token"

    saved = asyncio.run(
        session.run_cli(
            "cheese_memory", {"action": "save", "name": "x", "body": "y"}, None, "c1"
        )
    )
    unnamed = asyncio.run(session.run_cli("cheese_memory", {}, None, "c2"))

    assert saved == {
        "status": 1,
        "stdout": "",
        "stderr": "cheese_memory 要 action：project",
    }
    assert unnamed["stderr"] == "cheese_memory 要 action：project"
    assert ran == []


def test_a_room_without_the_switch_is_told_the_session_verbs_it_has():
    """Claude Code's server (`remote_execution/client.py`) and Codex's
    (`codex/tools.py`) both answer a room's call with this function."""

    class Host:
        environ: dict = {}

    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool("cheese_session", {"action": "compact"}, Host())

    assert str(refused.value).endswith(_ROOM_SESSION)


@pytest.mark.anyio
async def test_codex_tells_a_room_without_the_switch_the_session_verbs_it_has(
    monkeypatch,
):
    monkeypatch.delenv("CHEESE_COMPACT_TOOL", raising=False)
    tools = RemoteTools({})
    monkeypatch.setattr(tools.client, "call", lambda *_, **__: {"tools": []})
    await tools.discover()

    result = await tools(
        "item/tool/call",
        {"tool": "cheese_session", "callId": "c-1", "arguments": {"action": "compact"}},
    )

    assert result["success"] is False
    assert result["contentItems"][0]["text"].endswith(_ROOM_SESSION)
