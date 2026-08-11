"""快照撞上长命令：await 期间不产生误导性提交（2026-08-11 事故的回归测试）。

The incident, verbatim: a verification script deleted the one line it existed to
validate, ran a 35-second negative control, then restored it. The automatic
post-turn snapshot landed 10 seconds in — because `cheese await` is FOR ending
the turn early, so "turn over" and "command still writing" overlap by design —
and for the next two hours the topic branch showed the fix missing. The worktree
healed itself; the commit did not, and the branch is what diff / 验收卡 / a
reviewing human all read.

So these assert on what a downstream reader sees: the CONTENT of the file on the
topic branch, over a real jj worktree and (for the finish line) the real HTTP
report path. Nothing here inspects how the hold is implemented.
"""

import subprocess
import uuid

import pytest

from app.api.deps import get_turn_runner
from app.domain.agent import awaited_tasks
from app.domain.workspace import service as ws
from app.main import app

FIX = "    return normalise(path)  # the fix under test\n"


class FakeRunner:
    """A wake must not start a real turn in here."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit(self, chat_service, topic_id, **kw) -> uuid.UUID:
        self.submitted.append({"topic_id": topic_id, **kw})
        return uuid.uuid4()

    def running_topic_ids(self) -> set[uuid.UUID]:
        return set()


@pytest.fixture
def runner():
    fake = FakeRunner()
    awaited_tasks.reset()
    app.dependency_overrides[get_turn_runner] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_turn_runner, None)
    awaited_tasks.reset()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))


def _on_branch(project: uuid.UUID, topic: uuid.UUID, path: str) -> str:
    """The file as a downstream reader sees it: read off the topic's git branch,
    not the worktree. Empty string when the branch has no such file."""
    repo = ws.ensure_repo(project)
    done = subprocess.run(
        ["git", "show", f"{ws.branch_for_topic(topic)}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return done.stdout if done.returncode == 0 else ""


def _commit_messages(project: uuid.UUID, topic: uuid.UUID) -> list[str]:
    repo = ws.ensure_repo(project)
    done = subprocess.run(
        ["git", "log", "--format=%s", ws.branch_for_topic(topic)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return done.stdout.splitlines()


def _worktree_with_committed_fix(project: uuid.UUID, topic: uuid.UUID):
    """A topic whose finished work (the fix) is already on the branch — the state
    the incident started from."""
    wt = ws._ensure_worktree(project, ws.branch_for_topic(topic))
    (wt / "app.py").write_text("def load(path):\n" + FIX)
    ws.snapshot_worktree(project, topic, "修复 + 测试")
    assert FIX in _on_branch(project, topic, "app.py")
    return wt


def _register(project: uuid.UUID, topic: uuid.UUID, **over):
    body = {
        "project_id": project,
        "topic_id": topic,
        "command": "bash verify_fix.sh",
        "label": "验证修复有效",
        "timeout_s": 600,
        "log_path": "/home/node/.claude/cheese-await/run.log",
    }
    body.update(over)
    return awaited_tasks.register(**body)


def test_a_snapshot_during_a_background_command_leaves_the_branch_alone(
    workspace, runner
):
    """The 13:21:48 commit, refused. The negative control has deleted the fix and
    the turn ends right here — the branch must still show the fix."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    wt = _worktree_with_committed_fix(project, topic)

    _register(project, topic)
    (wt / "app.py").write_text("def load(path):\n")  # 负向对照：删掉修复那一行

    outcome = awaited_tasks.checkpoint_worktree(project, topic)

    assert outcome.startswith("held")
    assert FIX in _on_branch(project, topic, "app.py"), (
        "the torn tree reached the branch — this is the incident, reproduced"
    )


def test_no_background_task_means_the_snapshot_lands_as_before(workspace, runner):
    """The hold is the exception, not the new normal: with nothing in flight the
    turn's edits reach the branch exactly like they always did."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    wt = _worktree_with_committed_fix(project, topic)

    (wt / "app.py").write_text("def load(path):\n" + FIX + "    # and more work\n")
    assert awaited_tasks.checkpoint_worktree(project, topic) == "snapshotted"

    assert "and more work" in _on_branch(project, topic, "app.py")


def test_a_task_past_its_timeout_stops_holding_the_snapshot(workspace, runner):
    """A child that died with its container never reports, so its registration is
    never cleared. That must not freeze the topic's history forever."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    wt = _worktree_with_committed_fix(project, topic)
    task = _register(project, topic, timeout_s=600)
    task.started_at -= 600 + awaited_tasks.SNAPSHOT_HOLD_GRACE_S + 1

    (wt / "app.py").write_text("def load(path):\n" + FIX + "    # later work\n")

    assert awaited_tasks.snapshot_hold(topic) is None
    assert awaited_tasks.checkpoint_worktree(project, topic) == "snapshotted"
    assert "later work" in _on_branch(project, topic, "app.py")


def test_a_snapshot_that_cannot_be_held_says_so_in_its_commit(workspace, runner):
    """采纳前快照 has a human waiting on it, so refusing would wedge the accept.
    It commits — but the commit admits what it might be."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    wt = _worktree_with_committed_fix(project, topic)
    _register(project, topic, label="全量检查")

    (wt / "app.py").write_text("def load(path):\n")
    ws.snapshot_worktree(project, topic, "采纳前快照")

    assert "全量检查" in _commit_messages(project, topic)[0]


def test_the_finished_command_gets_the_final_state_onto_the_branch(
    client, runner, workspace
):
    """The other half: holding is only safe because the report puts the settled
    tree on the branch. Driven over the real HTTP path the detached child uses."""
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    created = client.post("/api/topics", json={"project_id": p["id"], "title": "T"})
    tid = created.json()["data"]["id"]
    project, topic = uuid.UUID(p["id"]), uuid.UUID(tid)
    wt = _worktree_with_committed_fix(project, topic)

    task = client.post(
        f"/api/topics/{tid}/background-task",
        json={
            "command": "bash verify_fix.sh",
            "label": "验证修复有效",
            "timeout_s": 600,
            "log_path": "/home/node/.claude/cheese-await/run.log",
        },
    ).json()["data"]

    (wt / "app.py").write_text("def load(path):\n")  # mid-run: fix deleted
    assert awaited_tasks.checkpoint_worktree(project, topic).startswith("held")

    # …the script restores the fix and writes what the run produced, then reports.
    (wt / "app.py").write_text("def load(path):\n" + FIX)
    (wt / "verify_fix.out").write_text("负向对照红了，正向绿了\n")
    done = client.post(
        f"/api/topics/{tid}/background-task/{task['task_id']}/done",
        json={"exit_code": 0, "tail": "ok", "duration_s": 35.0},
        headers={"X-Cheese-Token": task["wake_token"]},
    )

    assert done.status_code == 200, done.text
    assert FIX in _on_branch(project, topic, "app.py")
    assert _on_branch(project, topic, "verify_fix.out"), (
        "the run's own output never reached the branch"
    )
    assert "验证修复有效" in _commit_messages(project, topic)[0]
