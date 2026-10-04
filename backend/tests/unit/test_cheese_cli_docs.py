"""Drift guard: what the platform skill names vs. what actually exists.

芝士 can only act on what it is told exists. When the two disagree the agent
walks into a wall it cannot diagnose — observed 2026-08-10, where SKILL.md
documented a command in mandatory terms while no such subcommand existed yet,
and conversely `gh-token` was implemented but documented nowhere.

The platform tools describe themselves: each carries its own description into
the session, so the skill does not list them. A command-line subcommand has no
such description, so the skill's command table must name exactly the CLI's
subcommands, and every tool the skill or its references mention must exist.
"""

import argparse
import importlib.util
import re
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
_CHEESE = _SANDBOX / "cheese"
_SKILL = _SANDBOX / "skills" / "cheese" / "SKILL.md"
_REFERENCES = _SANDBOX / "skills" / "cheese" / "references"

_TOOL = re.compile(r"`(cheese_[a-z_]+)")
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


def _mentioned_tools() -> set[str]:
    texts = [_SKILL.read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8") for p in sorted(_REFERENCES.glob("*.md"))]
    return {name for text in texts for name in _TOOL.findall(text)}


def _documented_commands() -> set[str]:
    return {m.group(1) for line in _rows() if (m := _COMMAND_ROW.match(line))}


def _implemented_commands() -> set[str]:
    parser = _load_cli().build_parser()
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(groups) == 1, "expected exactly one subcommand group"
    return {name for name in groups[0].choices if not name.startswith("_")}


def test_every_tool_the_skill_mentions_is_served():
    served = set(_load_cli().PLATFORM_TOOLS.names())
    mentioned = _mentioned_tools()
    assert mentioned, "the skill names no platform tool"
    assert mentioned - served == set(), (
        f"the skill names tools nobody serves: {sorted(mentioned - served)}"
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
