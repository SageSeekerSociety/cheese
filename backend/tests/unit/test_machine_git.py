"""The checkout's history as a reader asks for it from the machine's executor
(`remote_execution/machine_git.py`).

Rules held here:

* a branch's change against the trunk includes what is not committed yet;
* a read leaves the checkout as it was, the index included, so it cannot get in
  the way of the room's agent working in it;
* nothing the reader sends becomes an option or a command of its own;
* a program the repository configures git to run while it reads is not run.
"""

import subprocess
import time
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import machine_git


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def checkout(tmp_path) -> Path:
    """A repository whose `work` branch left `main`, committed one change and
    holds another not yet committed; `main` moved on meanwhile."""
    root = tmp_path / "room"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "a@example.test")
    _git(root, "config", "user.name", "a")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "plan.md").write_text("第一节\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "start")
    _git(root, "checkout", "-qb", "work")
    (root / "plan.md").write_text("第一节\n第二节已提交\n")
    _git(root, "commit", "-qam", "second")
    _git(root, "checkout", "-q", "main")
    (root / "other.md").write_text("主干后来加的\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "trunk")
    _git(root, "checkout", "-q", "work")
    (root / "plan.md").write_text("第一节\n第二节已提交\n第三节还没提交\n")
    return root


def test_what_a_branch_changed_includes_what_is_not_committed(checkout):
    answer = machine_git.answer(
        {"command": "diff", "base": "main", "since_branched": True}, str(checkout)
    )
    assert "error" not in answer, answer
    assert "+第二节已提交" in answer["output"]
    assert "+第三节还没提交" in answer["output"]
    assert "主干后来加的" not in answer["output"]


def test_a_read_leaves_the_index_as_it_was(checkout):
    # The file looks touched to git (a new mtime, the same bytes): a status
    # that may write refreshes the index for it. A second on, so the entry is
    # not one git already treats as possibly stale (its racy-clean rule).
    notes = checkout / "notes.md"
    notes.write_text("干净的文件\n")
    _git(checkout, "add", "notes.md")
    _git(checkout, "commit", "-qm", "notes")
    index = checkout / ".git" / "index"
    time.sleep(1.1)
    notes.write_text(notes.read_text())
    before = index.read_bytes()
    for command in ("status", "diff", "log"):
        assert "error" not in machine_git.answer({"command": command}, str(checkout))
    assert index.read_bytes() == before
    assert not (checkout / ".git" / "index.lock").exists()


@pytest.mark.parametrize(
    "request_",
    [
        {"command": "log", "revision": "--output=OUT"},
        {"command": "diff", "base": "--output=OUT"},
        {"command": "show", "revision": "HEAD --output=OUT"},
        {"command": "commit"},
        {"command": "blame"},
    ],
)
def test_nothing_sent_becomes_an_option_or_a_command(checkout, request_):
    head = _git(checkout, "rev-parse", "HEAD")
    answer = machine_git.answer(request_, str(checkout))
    assert "error" in answer
    assert not (checkout / "OUT").exists()
    assert _git(checkout, "rev-parse", "HEAD") == head


def test_a_program_the_repository_configures_is_not_run(checkout, tmp_path):
    ran = tmp_path / "ran"
    _git(checkout, "config", "diff.external", f"sh -c 'touch {ran}' --")
    _git(checkout, "config", "diff.md.textconv", f"sh -c 'touch {ran}; cat \"$0\"'")
    (checkout / ".gitattributes").write_text("*.md diff=md\n")
    for request_ in (
        {"command": "diff"},
        {"command": "diff", "base": "main"},
        {"command": "show"},
        {"command": "log", "stat": True},
    ):
        assert "error" not in machine_git.answer(request_, str(checkout))
    assert not ran.exists()
