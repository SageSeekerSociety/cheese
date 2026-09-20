"""平台 CLI 在 pi 这边的样子：一份工具目录，和一次把参数还原成 argv 的调用。

pi has no MCP, so a room's platform tools cannot arrive the way they do for the
other harness. What CAN arrive is the same thing the other harness's MCP server
is itself built out of: the CLI's own argparse tree. `cli_worker` already turns
that tree into tool schemas and turns a tool call back into argv, and it is the
CLI that decides what is valid — so this module reads the CLI **installed on
this machine** and asks that one, rather than restating either half.

Two consequences worth stating, because they are the point:

**The catalog is generated where the CLI is.** A backend that shipped a newer
CLI to the machine gets a newer catalog on the next launch without anything
here knowing what changed. A catalog built on the backend would be a claim
about a file the backend cannot see.

**argparse stays the authority.** ``_command`` re-parses the argv it built, so
a missing required field or an out-of-range choice fails here, with argparse's
own message, instead of reaching the CLI as a malformed command line.
"""

import contextlib
import importlib.util
import io
import shutil
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

from app.domain.agent import cli_worker

# The CLI's own name on PATH. The launcher puts it in the platform's directory
# and exports that directory, for every harness — a pi room gets it because it
# is a room, not because anything about pi asked for it.
CLI = "cheese"


def cli_path() -> Path | None:
    found = shutil.which(CLI)
    return Path(found) if found else None


def _parser(source: Path) -> Any:
    # A module name of our own, so the CLI's `if __name__ == "__main__"` does
    # not fire while we are only asking it to describe itself. Not registered
    # in `sys.modules` either: this is a file being read, not an import anyone
    # else should be able to reach.
    # The loader is named rather than inferred: the CLI is installed WITHOUT a
    # `.py` suffix — it is a command — and suffix-based detection answers "not
    # Python" for it.
    name = "cheese_cli_catalog"
    spec = importlib.util.spec_from_file_location(
        name, source, loader=SourceFileLoader(name, str(source))
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{source} cannot be read as Python")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build = getattr(module, "build_parser", None)
    if build is None:
        raise RuntimeError(f"{source} does not publish an argparse parser")
    return build()


def tools(source: Path) -> list[dict]:
    """Every leaf command of the installed CLI, as a tool definition."""
    return cli_worker._tools(_parser(source))


def argv(source: Path, tool: str, arguments: dict) -> list[str]:
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
    parser = _parser(source)
    printed = io.StringIO()
    try:
        with contextlib.redirect_stderr(printed):
            return cli_worker._command(parser, tool, arguments)
    except SystemExit as exit:
        message = printed.getvalue().strip() or f"{tool}: invalid arguments"
        raise ValueError(message) from exit
