"""平台工具在 pi 这边的样子：一份工具目录，和一次调用。

A room's platform tools reach pi as extension tools, built out of one file: the
platform's CLI (`sandbox/cheese`, shipped in the runner archive as `cheese.py`)
carries the platform's tool table (`PLATFORM_TOOLS`, run here against the
backend). What must run on the room's machine as a process is the CLI's own
commands, which a pi room runs with its `bash` as the other harnesses do
(`cheese --help`), so the CLI's argparse tree is no tool here.
"""

import importlib.resources
import types
from functools import cache
from pathlib import Path
from typing import Any


@cache
def _module() -> Any:
    """The platform file, shipped beside this module in the runner archive
    (``bundle.py``); read from the source tree when running from a checkout."""
    shipped = importlib.resources.files(__package__).joinpath("cheese.py")
    checkout = Path(__file__).resolve().parents[5] / "sandbox" / "cheese"
    source = shipped.read_text() if shipped.is_file() else checkout.read_text()
    # A module name of our own, so the CLI's `if __name__ == "__main__"` does
    # not fire while we are only asking it to describe itself. It names its own
    # file only to fingerprint itself (`--version`), which this never asks.
    module = types.ModuleType("cheese_platform_tools")
    module.__file__ = str(checkout)
    exec(compile(source, "cheese", "exec"), module.__dict__)
    return module


def tools() -> list[dict]:
    """The platform's tool table: the same constant the other harnesses serve
    (结论 63)."""
    return _module().offered_tools()


def schemas_of(names: list[str]) -> list[dict]:
    """The table entries ``names`` name, from the room's table or the one for a
    芝士 answering someone (`DELEGATED_TOOLS`), in the order given. An object
    both tables have (`cheese_task`) is the delegated one: these sessions act
    under the question's credential, and `run_platform_tool` runs it so.

    A name may be one verb of an object (`cheese_doc.get`): the session is then
    handed that object with the verbs it was named with and no others."""
    module = _module()
    tables = (module.DELEGATED_TOOLS, module.PLATFORM_TOOLS)
    verbs: dict[str, list[str] | None] = {}
    for name in names:
        tool, _, verb = name.partition(".")
        named = verbs.get(tool, [])
        if not verb:
            verbs[tool] = None
        elif named is not None:
            verbs[tool] = [*named, verb]
    schemas = []
    for tool, only in verbs.items():
        table = next((t for t in tables if tool in t), None)
        if table is None:
            continue
        if only is None:
            schemas.append(next(s for s in table.schemas() if s["name"] == tool))
        else:
            schemas.append(table.narrowed(tool, only))
    return schemas


def is_platform_tool(tool: str) -> bool:
    return tool in _module().PLATFORM_TOOLS


def run_platform_tool(
    tool: str, arguments: dict, host: Any, *, delegated: bool = False
) -> str:
    """One table tool, run here against the backend; ``host`` reaches the
    room's machine for what a tool needs from it. ``delegated``: the call is
    made under a question's credential (`schemas_of`)."""
    return _module().run_platform_tool(tool, arguments, host, delegated=delegated)
