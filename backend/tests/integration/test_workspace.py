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
    resp = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()
    return uuid.UUID(resp["data"]["id"])


def _owner(client) -> dict[str, str]:
    """These routes return the source, so they need a caller with a claim on the
    project; the tests used to reach them with no credential at all."""
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _native_edit(pid: uuid.UUID, topic_id: uuid.UUID, path: str, content: str) -> None:
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
    files = client.get(f"/api/projects/{pid}/files", headers=_owner(client)).json()[
        "data"
    ]["data"]
    assert any(f["path"] == "feat.txt" for f in files)
    log = client.get(f"/api/projects/{pid}/git/log", headers=_owner(client)).json()[
        "data"
    ]["data"]
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
    r = client.get(
        f"/api/projects/{pid}/git/diff",
        params={"ref": "--help"},
        headers=_owner(client),
    )
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


def test_merge_leaves_no_worktree_debris(client):
    """merge_topic runs the actual merge in a throwaway worktree (2026-08-09
    incident fix) — it must be fully cleaned up on both success and conflict,
    never left for a human to notice and clear by hand."""
    pid = _mkproject(client)
    ok_tid, conflict_tid = uuid.uuid4(), uuid.uuid4()
    _native_edit(pid, ok_tid, "clean.txt", "clean merge\n")
    assert ws.merge_topic(pid, ok_tid)["merged"] is True

    # A guaranteed conflict: branch and base disagree on the same file.
    wt = ws.topic_worktree(pid, conflict_tid)
    (wt / "f.txt").write_text("branch version\n", encoding="utf-8")
    ws.snapshot_worktree(pid, conflict_tid)
    repo = ws.ensure_repo(pid)
    (repo / "f.txt").write_text("base version\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "base change"], check=True
    )
    result = ws.merge_topic(pid, conflict_tid)
    assert result["merged"] is False and result["conflicts"] == ["f.txt"]

    merge_root = ws._merge_worktree_path(pid)
    assert not merge_root.exists() or list(merge_root.iterdir()) == []
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_concurrent_accepts_on_the_same_project_dont_block_each_other(
    client, monkeypatch
):
    """2026-08-09 incident scenario: two different topics on the same project
    accept at nearly the same time; one's git subprocess is stuck. Isolated
    worktrees mean the other's merge must complete on its own — not wait for,
    and not be corrupted by, the stuck one."""
    import threading
    import time

    pid = _mkproject(client)
    hang_tid, fast_tid = uuid.uuid4(), uuid.uuid4()
    _native_edit(pid, hang_tid, "hang.txt", "hang work\n")
    _native_edit(pid, fast_tid, "fast.txt", "fast work\n")

    hang_branch = ws.branch_for_topic(hang_tid)
    real_run = ws._run_subprocess
    hang_entered = threading.Event()

    def fake_run(argv, cwd, timeout, env=None):
        if "merge" in argv and "--no-ff" in argv and hang_branch in argv:
            hang_entered.set()
            time.sleep(1.5)  # simulate the git process being stuck
            raise ws.GitTimeoutError("simulated hang, already confirmed dead")
        return real_run(argv, cwd, timeout, env)

    monkeypatch.setattr(ws, "_run_subprocess", fake_run)

    results: dict[str, dict] = {}

    def run_hang() -> None:
        results["hang"] = ws.merge_topic(pid, hang_tid)

    t = threading.Thread(target=run_hang)
    t.start()
    assert hang_entered.wait(timeout=5), "the hung merge never started"

    start = time.monotonic()
    fast_result = ws.merge_topic(pid, fast_tid)
    elapsed = time.monotonic() - start

    t.join(timeout=5)
    assert not t.is_alive()

    assert fast_result["merged"] is True
    assert elapsed < 1.0, "the fast merge waited on the hung one — isolation failed"
    assert results["hang"]["merged"] is False

    files = {f["path"] for f in ws.list_files(pid)}
    assert "fast.txt" in files
    assert "hang.txt" not in files  # the stuck merge never landed anything
