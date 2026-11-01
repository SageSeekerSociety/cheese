"""The demo scenes show a room the way a room works: the CLI's commands run as
`cheese …` in bash on every harness, so no scene shows one called as a tool
(`cheese_worktree`) — a step no session can take any more."""

import json
from pathlib import Path

from app.domain.agent.harness.pi import catalog

SCENES = Path(__file__).resolve().parents[3] / "frontend/src/views/demo/scenes"


def _walk(node):
    if isinstance(node, dict):
        if node.get("do") == "act":
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _acts():
    for path in sorted(SCENES.glob("*.json")):
        for act in _walk(json.loads(path.read_text(encoding="utf-8"))):
            yield path.name, act


def test_no_scene_calls_a_cli_command_as_a_tool():
    commands = catalog.cli_commands()
    acts = list(_acts())
    assert acts, "the scenes moved: this test reads none"

    called = [(scene, act["tool"]) for scene, act in acts if act["tool"] in commands]
    assert called == []


def test_a_scene_that_runs_one_runs_it_in_bash():
    ran = [
        act["arg"]
        for _, act in _acts()
        if act["tool"] == "Bash" and str(act.get("arg", "")).startswith("cheese ")
    ]
    assert any(arg.startswith("cheese worktree ") for arg in ran)
