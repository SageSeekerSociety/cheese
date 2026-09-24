"""Drift guard: the platform skill's two tables vs. what actually exists.

芝士 can only act on what the injected skill tells it exists. When the two
disagree the agent walks into a wall it cannot diagnose — observed 2026-08-10,
where SKILL.md documented a command in mandatory terms while no such subcommand
existed yet, and conversely `gh-token` was implemented but documented nowhere.

SKILL.md's tools section has two tables, one per way of calling:

- the MCP table writes each tool's signature — `| \\`cheese_note(thread, content)\\`
  | … |` — and must name exactly the session-side table (`PLATFORM_TOOLS`) plus
  the transport's `platform_request`;
- the CLI table writes the command line itself — `| \\`cheese sync [--task …]\\` |`
  — and must name exactly the CLI's subcommands.

A tool or subcommand added without a row (or a row without one) fails here.
"""

import argparse
import importlib.util
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
_CHEESE = _SANDBOX / "cheese"
_SKILL = _SANDBOX / "skills" / "cheese" / "SKILL.md"

#: One row may document a pair of tools (`cheese_lock(…)` / `cheese_unlock(…)`).
_TOOL = re.compile(r"`(cheese_[a-z_]+|chat_send|platform_request)\(")
_COMMAND_ROW = re.compile(r"^\|\s*`cheese ([a-z][a-z-]*)")


def _load_cli():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _rows():
    return [
        line
        for line in _SKILL.read_text(encoding="utf-8").splitlines()
        if line.startswith("|")
    ]


def _documented_tools() -> set[str]:
    names = set()
    for line in _rows():
        first_cell = line.split("|")[1]
        names.update(_TOOL.findall(first_cell))
    return names


def _documented_commands() -> set[str]:
    return {m.group(1) for line in _rows() if (m := _COMMAND_ROW.match(line))}


def _implemented_commands() -> set[str]:
    parser = _load_cli().build_parser()
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(groups) == 1, "expected exactly one subcommand group"
    return {name for name in groups[0].choices if not name.startswith("_")}


def test_the_mcp_table_is_the_session_side_tool_table():
    served = {*_load_cli().PLATFORM_TOOLS.names(), "platform_request"}
    documented = _documented_tools()
    assert documented - served == set(), (
        f"SKILL.md promises tools nobody serves: {sorted(documented - served)}"
    )
    assert served - documented == set(), (
        f"tools SKILL.md never mentions, which 芝士 will never use: "
        f"{sorted(served - documented)}"
    )


def test_the_cli_table_is_the_cli():
    documented, implemented = _documented_commands(), _implemented_commands()
    assert documented - implemented == set(), (
        f"SKILL.md promises commands the CLI does not implement: "
        f"{sorted(documented - implemented)}"
    )
    assert implemented - documented == set(), (
        f"the CLI implements commands SKILL.md never mentions: "
        f"{sorted(implemented - documented)}"
    )


def test_version_flag_reports_a_source_fingerprint():
    """`cheese --version` is how anyone checks, from inside a box, that the CLI
    it runs is the one the backend shipped (see ws.session_dir staging)."""
    cli = _load_cli()
    fingerprint = cli.source_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{12}", fingerprint), fingerprint
