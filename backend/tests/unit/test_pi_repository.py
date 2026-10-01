"""What a repository says about itself reaches a pi room as it reaches the other
harness: its AGENTS.md, CLAUDE.md, CLAUDE.local.md and `.claude/rules`, read in
the checkout on the room's machine (`repository.py`, run there as a script)."""

import json
import subprocess
import sys
from pathlib import Path

from app.domain.agent.harness.pi import repository

SCRIPT = Path(repository.__file__)


def said(root: Path, files: dict[str, str]) -> str:
    """What the repository at `root` says, read the way the machine reads it."""
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)
    done = subprocess.run(
        [sys.executable, "-", str(root)],
        input=SCRIPT.read_text(),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)["context"]


def test_a_repository_that_says_nothing_adds_nothing(tmp_path):
    assert said(tmp_path, {"README.md": "# readme\n"}) == ""


def test_one_file_under_two_names_is_read_once(tmp_path):
    # A repository that keeps CLAUDE.md and points AGENTS.md at it should not
    # pay for it twice on every turn.
    body = "# 同一份\n\n只说一次。\n"
    text = said(tmp_path, {"CLAUDE.md": body, "AGENTS.md": body})
    assert text.count("只说一次。") == 1


def test_local_notes_and_rules_are_read_too(tmp_path):
    text = said(
        tmp_path,
        {
            "CLAUDE.md": "# 主约定\n\n主。\n",
            "CLAUDE.local.md": "# 本机补充\n\n本机。\n",
            ".claude/rules/code.md": "# 代码规则\n\n规则。\n",
            "pkg/CLAUDE.md": "# 子目录\n\n子。\n",
        },
    )
    for part in ("主。", "本机。", "规则。", "子。", ".claude/rules/code.md"):
        assert part in text


def test_relative_imports_are_expanded_and_absolute_ones_left(tmp_path):
    text = said(
        tmp_path,
        {
            "CLAUDE.md": "# 入口\n\n@rules/inner.md\n\n另见 @/etc/passwd。\n",
            "rules/inner.md": "内层内容。\n",
        },
    )
    assert "内层内容。" in text
    assert "@/etc/passwd" in text


def test_settings_are_configuration_not_conventions(tmp_path):
    # Executable configuration in a prompt would hand whoever can open a PR the
    # room's hook runner.
    text = said(
        tmp_path,
        {
            "CLAUDE.md": "# 约定\n\n正文。\n",
            ".claude/settings.json": json.dumps(
                {"hooks": {"PostToolUse": [{"command": "rm -rf /"}]}}
            ),
        },
    )
    assert "PostToolUse" not in text
    assert "settings.json" not in text


def test_an_oversized_file_is_cut_and_says_which(tmp_path):
    text = said(tmp_path, {"CLAUDE.md": "# 大文件\n\n" + "字" * (70 * 1024)})
    assert "truncated at 65536 bytes" in text
    assert "CLAUDE.md" in text
