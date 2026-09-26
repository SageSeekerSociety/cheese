"""Dump the sandbox `cheese` CLI's commands and platform tools as JSON.

Read straight from backend/sandbox/cheese — the file is both the tool table and
the CLI — so the generated reference cannot describe a command that is not there.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / "backend" / "sandbox" / "cheese"
loader = importlib.machinery.SourceFileLoader("cheese_cli", str(path))
spec = importlib.util.spec_from_loader("cheese_cli", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


def commands(parser, prefix=""):
    out = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help for a in action._choices_actions}
            for name, sub in action.choices.items():
                args = [
                    " ".join(a.option_strings) or a.dest
                    for a in sub._actions
                    if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))
                ]
                out.append({"name": f"{prefix}{name}", "help": helps.get(name) or sub.description or "", "args": args})
                out += commands(sub, f"{prefix}{name} ")
    return out


tools = []
for t in mod.PLATFORM_TOOLS.schemas():
    schema = t.get("inputSchema") or {}
    tools.append({
        "name": t.get("name"),
        "description": t.get("description", ""),
        "params": list((schema.get("properties") or {}).keys()),
        "required": schema.get("required", []),
    })

json.dump({"commands": commands(mod.build_parser()), "tools": tools}, sys.stdout, ensure_ascii=False)
