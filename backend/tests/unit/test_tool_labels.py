"""现场那一行的中文动词，和真正会被调用的那些工具名，钉在一起。

The verb table lives in the frontend because translation happens at display
time — a name missing from today's table is then never frozen untranslated in a
row written yesterday. What the frontend cannot do is notice that a name has
appeared: 现场 renders an unmapped tool raw, deliberately, and the only way
anybody finds out is by reading a room and seeing `cheese_accept_request` where
a verb should be.

So the two ends are pinned here, from their real sources rather than from a
second list written down next to the answer:

- every tool on the platform's session-side table (`PLATFORM_TOOLS`) and each
  verb of its objects;
- every tool the pi extension registers on its own, read out of the extension.

A command added without a verb fails here, at the commit that adds it.
"""

import importlib.util
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path

from app.domain.session_record import tool_keys

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "backend/sandbox/cheese"
EXTENSION = ROOT / "backend/app/domain/agent/harness/pi/platform.ts"
LABELS = ROOT / "frontend/src/lib/toolLabels.ts"

#: `  cheese_doc_set: '更新实况文档',`, or `  'cheese_task.close': '…',` for one
#: of an object's verbs — the tables are plain object literals, and reading them
#: with a regex is what keeps this test from needing a TypeScript toolchain to
#: answer a question about a dictionary.
_ENTRY = re.compile(r"^\s{2}'?([A-Za-z_][A-Za-z0-9_.]*)'?:\s*'([^']+)'", re.MULTILINE)


def _frontend_table(name: str) -> dict[str, str]:
    body = LABELS.read_text(encoding="utf-8")
    table = body.split(f"export const {name}", 1)[1].split("\n}", 1)[0]
    return dict(_ENTRY.findall(table))


def labelled() -> dict[str, str]:
    found = _frontend_table("TOOL_LABELS")
    assert found, "the verb table could not be read at all"
    return found


def cli_tools() -> list[str]:
    loader = SourceFileLoader("cheese_cli", str(CLI))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return [
        *_keys(module.PLATFORM_TOOLS),
        # A person's 芝士 shares its objects' names with the room's (`cheese_task`),
        # and a verb only it has would otherwise go unnoticed.
        *(key for key in _keys(module.DELEGATED_TOOLS) if "." in key),
    ]


def _keys(table) -> list[str]:
    """What 现场 looks a call up by: a tool's name, and each of an object's
    verbs (`cheese_task.close`). An object's own name is what a step reads
    before its `action` is written."""
    return [
        key
        for name in table.names()
        for key in ([name, *(f"{name}.{verb}" for verb in table.actions(name) or ())])
    ]


def test_every_platform_command_has_a_verb():
    """A room reading 「cheese_accept_request」 is reading the thing it most
    needed a word for: the steps that changed something outside the machine."""
    verbs = labelled()
    # An object's name that is also an old tool's (`cheese_task`) reads as
    # the verb it was before it took an action.
    retired = _frontend_table("RETIRED")
    missing = [
        name
        for name in cli_tools()
        if name not in verbs and retired.get(name) not in verbs
    ]
    assert not missing, f"现场 would render these raw: {missing}"


def test_every_tool_the_extension_adds_has_a_verb():
    """The background tools are the extension's own — they are in no catalog, so
    nothing else would notice them going untranslated."""
    source = EXTENSION.read_text(encoding="utf-8")
    registered = set(re.findall(r'name:\s*"([a-z_]+)"', source))
    alias = re.findall(r'^const PUBLISH = "([a-z_]+)";', source, re.MULTILINE)
    registered |= set(alias)
    assert "bash_start" in registered, "the extension's tool names could not be read"

    verbs = labelled()
    missing = sorted(name for name in registered if name not in verbs)
    assert not missing, f"现场 would render these raw: {missing}"


def test_the_frontend_reads_a_retired_name_as_the_backend_does():
    """A record keeps a call under the name it was made by. The backend reads
    it as the action it became (`session_record.tool_keys`) and so does 现场; were
    the two copies to differ, the same old call would read as one thing in the
    record's preview and another in its verb."""
    frontend = _frontend_table("RETIRED")
    assert frontend == tool_keys.RETIRED
