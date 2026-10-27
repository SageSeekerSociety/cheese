"""平台工具在 pi 这边的样子：一份工具目录，和一次调用。

A room's platform tools reach pi as extension tools, built out of one file: the
platform's CLI (`sandbox/cheese`, shipped in the runner archive as `cheese.py`)
carries the platform's tool table (`PLATFORM_TOOLS`, run here against the
backend). What must run on the room's machine as a process is the CLI's own
commands, which a pi room runs with its `bash` as the other harnesses do
(`cheese --help`), so the CLI's argparse tree is no tool here.
"""

import argparse
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


@cache
def cli_commands() -> dict[str, str]:
    """What each of the CLI's commands is called when a model remembers it as a
    tool (`cheese_push_fix`), and the command it is (`cheese push-fix`).

    pi rooms once had them as tools; they are commands run in bash now, and a
    session that calls one as a tool is told so (`not_a_tool`)."""
    parser = _module().build_parser()
    commands: dict[str, str] = {}

    def walk(node, words: tuple[str, ...]) -> None:
        subparsers = next(
            (a for a in node._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        if subparsers is None:
            name = "cheese_" + "_".join(w.replace("-", "_") for w in words)
            commands[name] = " ".join(("cheese", *words))
            return
        seen: set[int] = set()
        for word, child in subparsers.choices.items():
            if id(child) not in seen:  # an alias is the same parser again
                seen.add(id(child))
                walk(child, (*words, word))

    walk(parser, ())
    return commands


def not_a_tool(tool: str) -> str:
    """What a session that calls the CLI command ``tool`` as a tool is told."""
    command = cli_commands()[tool]
    return (
        f"{tool} 不是工具：CLI 的命令在 bash 里跑，`{command} …`"
        f"（`{command} --help` 看它的写法，`cheese --help` 列出全部命令）。"
    )


def schemas_of(names: list[str]) -> list[dict]:
    """The table entries ``names`` name, from the room's table or the one for a
    芝士 answering someone (`DELEGATED_TOOLS`), in the order given. An object
    both tables have (`cheese_task`) is the delegated one: these sessions act
    under the question's credential, and `run_platform_tool` runs it so.

    A name may be one verb of an object (`cheese_doc.get`): the session is then
    handed that object with the verbs it was named with and no others.

    Each call handed must say what it does (`ToolTable.effect`), which is what
    the session's harness weighs before running one: a call that does not —
    `platform_request`, which reaches any route — is refused here, so the
    session does not start rather than hold it."""
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
        keys = [
            f"{tool}.{verb}" if verb else tool
            for verb in (only or list(table.actions(tool) or [None]))
        ]
        unsaid = [key for key in keys if table.effect(key) is None]
        if unsaid:
            # i18n-exempt: a session started wrong, for a developer; no screen shows it
            raise ValueError(f"{', '.join(unsaid)} do not say what they do")
        if only is None:
            schemas.append(next(s for s in table.schemas() if s["name"] == tool))
        else:
            schemas.append(table.narrowed(tool, only))
    return schemas


def ask_for_action(tool: str, verbs: list[str]) -> str:
    """What a call to an object the session holds only some verbs of is told
    when it asks for another: the ones it holds."""
    return _module().ask_for_action(tool, verbs)


def is_platform_tool(tool: str) -> bool:
    return tool in _module().PLATFORM_TOOLS


def run_platform_tool(
    tool: str, arguments: dict, host: Any, *, delegated: bool = False
) -> str:
    """One table tool, run here against the backend; ``host`` reaches the
    room's machine for what a tool needs from it. ``delegated``: the call is
    made under a question's credential (`schemas_of`)."""
    return _module().run_platform_tool(tool, arguments, host, delegated=delegated)
