"""平台工具在 pi 这边的样子：一份工具目录，和一次调用。

pi has no MCP client, so a room's platform tools cannot arrive the way they do
for the other harnesses. What CAN arrive is the file they are built out of: the
platform's CLI (`sandbox/cheese`, shipped in the runner archive as `cheese.py`)
carries both the platform's tool table (`PLATFORM_TOOLS`, run here against the
backend) and the CLI's argparse tree for what must run on the room's machine as
a process (`cli_worker` turns that tree into tool schemas and a call back into
argv). This module reads that file and asks it, rather than restating either
half.

**argparse stays the authority.** ``argv`` re-parses the argv it built, so a
missing required field or an out-of-range choice fails here, with argparse's own
message, instead of reaching the CLI as a malformed command line.
"""

import contextlib
import importlib.resources
import io
import types
from functools import cache
from pathlib import Path
from typing import Any

from app.domain.agent import cli_worker

#: The CLI's own name on the machine's PATH: the executor puts the platform's
#: directory first on every command's PATH.
CLI = "cheese"


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
    exec(compile(source, "cheese", "exec"), module.__dict__)  # noqa: S102
    return module


def tools() -> list[dict]:
    """The platform's tool table, then every leaf command of the CLI.

    The table is the same constant the other harnesses serve (结论 63); the
    leaves are what is left for the machine to run as a process."""
    return [
        *_module().PLATFORM_TOOLS.schemas(),
        *cli_worker._tools(_module().build_parser()),
    ]


def schemas_of(names: list[str]) -> list[dict]:
    """The table entries ``names`` name, from the room's table or the one for a
    芝士 answering someone (`DELEGATED_TOOLS`), in the order given."""
    module = _module()
    known = {
        tool["name"]: tool
        for tool in (
            *module.PLATFORM_TOOLS.schemas(),
            *module.DELEGATED_TOOLS.schemas(),
        )
    }
    return [known[name] for name in names if name in known]


def is_platform_tool(tool: str) -> bool:
    return tool in _module().PLATFORM_TOOLS


def run_platform_tool(tool: str, arguments: dict, host: Any) -> str:
    """One table tool, run here against the backend; ``host`` reaches the
    room's machine for what a tool needs from it."""
    return _module().run_platform_tool(tool, arguments, host)


def argv(tool: str, arguments: dict) -> list[str]:
    """The argv `tool` means, validated by the parser that will run it.

    A call the parser rejects comes back as a ``ValueError`` carrying what
    argparse would have printed. That conversion is the whole of this function:
    argparse answers a bad command line by writing to stderr and calling
    ``sys.exit``, and ``SystemExit`` is not an ``Exception`` — left alone it
    walks out through the socket handler that was meant to report it and takes
    the runner's event loop with it. A model that guessed a field wrong would
    end the room's session.

    Nothing is awaited inside the redirect, so the swapped stream cannot be
    observed by another turn.
    """
    parser = _module().build_parser()
    printed = io.StringIO()
    try:
        with contextlib.redirect_stderr(printed):
            return cli_worker._command(parser, tool, arguments)
    except SystemExit as exit:
        message = printed.getvalue().strip() or f"{tool}: invalid arguments"
        raise ValueError(message) from exit
