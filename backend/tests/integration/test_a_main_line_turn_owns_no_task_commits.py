"""A turn in a channel's main line does not report a task's commits as its own.

What 芝士 does in a channel keeps nothing: what changes the project is done in
a task, on the task's branch, by the task's own session. While a main-line turn
runs, a task in the same channel may commit; when the main-line turn ends, the
channel must not say 「这次改了 N 个文件」 about that work, as if this turn made it.
"""

import time
import uuid

from app.domain.repository import forge_files
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)

DIFF = (
    "diff --git a/README.md b/README.md\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@ -1 +1,2 @@\n"
    " # 项目\n"
    "+一行新说明\n"
)


def _until(check, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_a_tasks_commit_during_a_main_line_turn_is_not_the_turns(
    client, stub_hooks, monkeypatch
):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    open_task(client, room, "README 加一行", owner="alice")

    # The task's branch: one commit before, a second once its session delivers.
    commits = ["base"]

    async def history(self):
        return [{"sha": sha} for sha in commits]

    async def commit_diff(self, sha):
        return DIFF

    monkeypatch.setattr(forge_files.ProjectFiles, "history", history)
    monkeypatch.setattr(forge_files.ProjectFiles, "commit_diff", commit_diff)

    started: list[uuid.UUID] = []

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic, agent=agent)
        stub_hooks.acknowledges(topic, prompt, agent=agent)
        started.append(topic)

    stub_hooks.emit_turn = turn
    post_message(
        client, room, "alice", {"content": "@芝士 README 末尾加一行，建个任务做"}
    )
    _until(lambda: started)
    # Meanwhile the task's own session commits on its branch.
    commits.append("from-the-task")
    stub_hooks.stops(started[0], "好了，任务建好了。")
    wait_work_idle()

    lines = client.get(
        f"/topics/{room}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [b for b in lines if (b.get("meta") or {}).get("changeset")] == []
