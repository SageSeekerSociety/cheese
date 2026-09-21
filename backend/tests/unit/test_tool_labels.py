"""现场那一行用的词，和真正会被调用的那些工具名，钉在一起。

表里的值现在是**词表的键**（`toolLabels.cheeseAcceptRequest`），不再是中文动词本身：
这里因此只问「这个工具名在表里有没有一条」，而「键在词表里有没有词条」由
`frontend/src/lib/toolLabels.spec.ts` 逐条对着两个语言的词表问。两半合起来才是
「现场那一行读得通」；少了后一半，漏一条词条的后果是渲染出内部键名。

The verb table lives in the frontend because translation happens at display
time — a name missing from today's table is then never frozen untranslated in a
row written yesterday. What the frontend cannot do is notice that a name has
appeared: 现场 renders an unmapped tool raw, deliberately, and the only way
anybody finds out is by reading a room and seeing `cheese_accept_request` where
a verb should be.

So the two ends are pinned here, from their real sources rather than from a
second list written down next to the answer:

- every command of the CLI **this repository ships**, read out of its argparse
  tree the same way the pi catalog is built (`cli_worker._tools`);
- every tool the pi extension registers on its own, read out of the extension.

A command added without a verb fails here, at the commit that adds it.
"""

import importlib.util
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path

from app.domain.agent import cli_worker

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "backend/sandbox/cheese"
EXTENSION = ROOT / "backend/app/domain/agent/harness/pi/platform.ts"
LABELS = ROOT / "frontend/src/lib/toolLabels.ts"

#: `  cheese_doc_set: 'toolLabels.cheeseDocSet',` — the table is a plain object
#: literal, and reading it with a regex is what keeps this test from needing a
#: TypeScript toolchain to answer a question about a dictionary.
_ENTRY = re.compile(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*):\s*'([^']+)'", re.MULTILINE)


def labelled() -> dict[str, str]:
    body = LABELS.read_text(encoding="utf-8")
    table = body.split("TOOL_LABELS", 1)[1].split("\n}", 1)[0]
    found = dict(_ENTRY.findall(table))
    assert found, "the verb table could not be read at all"
    return found


def cli_tools() -> list[str]:
    loader = SourceFileLoader("cheese_cli", str(CLI))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return [tool["name"] for tool in cli_worker._tools(module.build_parser())]


def test_every_platform_command_has_a_verb():
    """A room reading 「cheese_accept_request」 is reading the thing it most
    needed a word for: the steps that changed something outside the machine."""
    verbs = labelled()
    missing = [name for name in cli_tools() if name not in verbs]
    assert not missing, f"现场 would render these raw: {missing}"


def test_every_tool_the_extension_adds_has_a_verb():
    """The background tools and the publish alias are the extension's own — they
    are in no catalog, so nothing else would notice them going untranslated."""
    source = EXTENSION.read_text(encoding="utf-8")
    registered = set(re.findall(r'name:\s*"([a-z_]+)"', source))
    alias = re.findall(r'^const PUBLISH = "([a-z_]+)";', source, re.MULTILINE)
    registered |= set(alias)
    assert "bash_start" in registered, "the extension's tool names could not be read"

    verbs = labelled()
    missing = sorted(name for name in registered if name not in verbs)
    assert not missing, f"现场 would render these raw: {missing}"
