"""A machine's push must land on an open topic.

`cheese-sync` cannot fail loudly — a Stop hook that errors takes the turn down —
so anything that rejects its push looks exactly like success, which is the
original bug: the agent works and the branch never moves. This pins the end state
(the machine's file is in the branch) rather than any single mechanism, so it
still catches it if the platform's own checkout changes shape underneath.
"""

import subprocess
import uuid

from app.api.routes.git_http import _configure_for_push
from app.domain.workspace import service as ws
from tests.machine_work import machine_commits


def test_a_machine_can_push_to_a_topic_that_has_a_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_tree(topic)
    ws._ensure_worktree(project, topic)  # the topic is open on the platform
    _configure_for_push(repo)

    machine_commits(
        project,
        topic,
        {"from_machine.txt": "what the agent wrote on its own machine\n"},
    )

    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "from_machine.txt" in listed
