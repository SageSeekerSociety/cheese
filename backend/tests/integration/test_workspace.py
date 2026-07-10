"""Project workspace — jj-backed per-topic workspaces + git merge/diff (Phase 4).

Files are authored by the sandbox's native tools (Bash/Write/Edit) inside the
topic's jj workspace; the platform snapshots them with ``snapshot_worktree``.
These tests simulate that by writing into the workspace dir then snapshotting.
"""

import uuid

import pytest

from app.core.errors import ValidationError
from app.domain.workspace import service as ws


def _mkproject(client) -> uuid.UUID:
    resp = client.post("/api/projects", json={"name": "P"}).json()
    return uuid.UUID(resp["data"]["id"])


def _native_edit(
    pid: uuid.UUID, topic_id: uuid.UUID, path: str, content: str
) -> None:
    """Simulate a sandbox turn: native tools write a file into the topic's jj
    workspace, then the platform snapshots it (as converse does after a turn)."""
    wt = ws.topic_worktree(pid, topic_id)
    target = wt / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ws.snapshot_worktree(pid, topic_id)


def test_native_edit_versioned_and_browsable(client):
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "src/app.py", "print('hi')\n")

    # Listed + readable from the topic's own workspace.
    files = ws.list_files(pid, topic_id=tid)
    assert any(f["path"] == "src/app.py" for f in files)
    assert "print('hi')" in ws.read_file(pid, "src/app.py", topic_id=tid)
    # The snapshot is on the topic branch vs the base.
    assert "print('hi')" in ws.topic_diff(pid, tid)


def test_topic_branch_isolated_then_merged(client):
    # spec §6.3: a topic's writes live on its own branch; 采纳 = merge to base.
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "feat.txt", "branch work\n")

    # Not on the base until merged.
    assert "feat.txt" not in {f["path"] for f in ws.list_files(pid)}
    assert "branch work" in ws.topic_diff(pid, tid)

    assert ws.merge_topic(pid, tid)["merged"] is True
    # The base branch now contains the file (browsable via the API).
    files = client.get(f"/api/projects/{pid}/files").json()["data"]["data"]
    assert any(f["path"] == "feat.txt" for f in files)
    log = client.get(f"/api/projects/{pid}/git/log").json()["data"]["data"]
    assert len(log) >= 1


def test_parallel_topics_isolated(client):
    # Two topics edit in their own workspaces without overwriting each other.
    pid = _mkproject(client)
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    _native_edit(pid, t1, "a.txt", "from t1\n")
    _native_edit(pid, t2, "b.txt", "from t2\n")

    n1 = {f["path"] for f in ws.list_files(pid, topic_id=t1)}
    n2 = {f["path"] for f in ws.list_files(pid, topic_id=t2)}
    assert "a.txt" in n1 and "b.txt" not in n1
    assert "b.txt" in n2 and "a.txt" not in n2


def test_read_missing_file_raises(client):
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "rec/cf.py", "def recall_at_10():\n    return 0.18\n")
    assert "recall_at_10" in ws.read_file(pid, "rec/cf.py", topic_id=tid)
    with pytest.raises(ValidationError):
        ws.read_file(pid, "rec/cf.py")  # base tree → not found (isolation)


def test_exec_in_sandbox_runs_code_and_blocks_network(client):
    if not ws.sandbox_available():
        pytest.skip("docker not available")
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "m.py", "print('hi from sandbox')\n")

    res = ws.exec_in_sandbox(pid, "python m.py", topic_id=tid)
    assert res["exit_code"] == 0
    assert "hi from sandbox" in res["stdout"]

    # --network none → outbound network is blocked (isolation).
    net = ws.exec_in_sandbox(
        pid,
        "python -c \"import urllib.request as u; u.urlopen('http://example.com',timeout=3)\"",
        topic_id=tid,
    )
    assert net["exit_code"] != 0


def test_git_diff_rejects_option_injection(client):
    pid = _mkproject(client)
    r = client.get(f"/api/projects/{pid}/git/diff", params={"ref": "--help"})
    assert r.status_code == 422


def test_merge_folds_unsnapshotted_human_edits(client):
    """采纳前快照: a human edit (人改文件即指令) with no agent turn afterwards
    must still be delivered by the accept-merge."""
    pid = _mkproject(client)
    tid = uuid.uuid4()
    wt = ws.topic_worktree(pid, tid)
    (wt / "human.txt").write_text("edited by hand\n", encoding="utf-8")
    # NO snapshot_worktree here — merge itself must fold the pending change.
    assert ws.merge_topic(pid, tid)["merged"] is True
    assert "edited by hand" in ws.read_file(pid, "human.txt")
