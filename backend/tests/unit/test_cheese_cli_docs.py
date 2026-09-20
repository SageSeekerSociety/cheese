"""Drift guard: the cheese CLI's manual vs. the CLI itself.

芝士 can only act on what the injected skill tells it exists. When the two
disagree the agent walks into a wall it cannot diagnose — observed 2026-08-10,
where SKILL.md documented a command in mandatory terms while no such subcommand
existed yet, and conversely `gh-token` was implemented but documented nowhere.

So the command table and the argparse surface are pinned to each other, the same
way test_hooks_substrate pins the committed hook script to its source constant.
Adding a subcommand without documenting it (or the reverse) fails here.
"""

import argparse
import importlib.util
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
_CHEESE = _SANDBOX / "cheese"
_SKILL = _SANDBOX / "skills" / "cheese" / "SKILL.md"

# A tool-table row: `| \`cheese_<name>(...)\` | 说明 |`, where the tool name is
# the subcommand with `-` folded to `_` (`cheese_doc_set` documents `doc`). Two
# tools stand in for subcommands under their own names.
_ROW = re.compile(r"^\|\s*`(cheese_[a-z_]+|chat_send|platform_request)\(")
_TOOL_TO_COMMAND = {"chat_send": "chat", "platform_request": "api"}


def _load_cli():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _implemented() -> set[str]:
    """Public subcommands. Names starting with `_` are internal plumbing the
    agent never types and are deliberately undocumented."""
    parser = _load_cli().build_parser()
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(groups) == 1, "expected exactly one subcommand group"
    return {name for name in groups[0].choices if not name.startswith("_")}


def _command_for(tool: str) -> str:
    """`cheese_doc_set` → `doc`: the first subcommand word, `_` written as `-`
    where the CLI spells it that way (`accept_request` → `accept-request`)."""
    rest = tool.removeprefix("cheese_")
    for name in sorted(_implemented(), key=len, reverse=True):
        if rest == name.replace("-", "_") or rest.startswith(
            name.replace("-", "_") + "_"
        ):
            return name
    return rest


def _documented() -> set[str]:
    names: set[str] = set()
    for line in _SKILL.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        tool = m.group(1)
        if tool in _TOOL_TO_COMMAND:
            names.add(_TOOL_TO_COMMAND[tool])
            continue
        names.add(_command_for(tool))
    return names


def test_every_documented_command_is_implemented():
    missing = _documented() - _implemented()
    assert not missing, (
        f"SKILL.md promises commands the CLI does not implement: {sorted(missing)}. "
        "芝士 reads that table as fact — implement them or drop the rows."
    )


def test_every_implemented_command_is_documented():
    undocumented = _implemented() - _documented()
    assert not undocumented, (
        f"the CLI implements commands SKILL.md never mentions: {sorted(undocumented)}. "
        "An undocumented command is one 芝士 will never use."
    )


def test_version_flag_reports_a_source_fingerprint():
    """`cheese --version` is how anyone checks, from inside a box, that the CLI
    it runs is the one the backend shipped (see ws.session_dir staging)."""
    cli = _load_cli()
    fingerprint = cli.source_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{12}", fingerprint), fingerprint
