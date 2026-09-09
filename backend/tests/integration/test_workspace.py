"""Project workspace — per-topic workspaces + git merge/diff (Phase 4).

Files are authored by native tools (Bash/Write/Edit) on the machine the turn ran
on, which commits and pushes the topic branch back. These tests do the same
(`tests.machine_work`) rather than writing into the platform's own checkout,
because that checkout is only ever read.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.domain.workspace import service as ws
from tests.machine_work import machine_commits

_MSG = "chore: land the branch under test\n\nRequested-by: alice"


def _mkproject(client) -> uuid.UUID:
    resp = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()
    return uuid.UUID(resp["data"]["id"])


def _mktopic(client, pid: uuid.UUID) -> uuid.UUID:
    response = client.post("/topics", json={"project_id": str(pid), "title": "Files"})
    assert response.status_code == 200
    return uuid.UUID(response.json()["data"]["id"])


def _owner(client) -> dict[str, str]:
    """These routes return the source, so they need a caller with a claim on the
    project; the tests used to reach them with no credential at all."""
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _native_edit(pid: uuid.UUID, topic_id: uuid.UUID, path: str, content: str) -> None:
    """One turn: the machine writes a file, commits it, and pushes the branch."""
    machine_commits(pid, topic_id, {path: content})


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

    assert ws.merge_topic(pid, tid, message=_MSG)["merged"] is True
    # The base branch now contains the file (browsable via the API).
    files = client.get(f"/projects/{pid}/files", headers=_owner(client)).json()["data"][
        "data"
    ]
    assert any(f["path"] == "feat.txt" for f in files)
    log = client.get(f"/projects/{pid}/git/log", headers=_owner(client)).json()["data"][
        "data"
    ]
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


def test_file_endpoints_survive_the_agent_committing(client):
    """The file panel's 422 outage, at the level the user saw it.

    芝士 committing in its workspace writes into the project's SHARED store
    (mounted into every sandbox container), and every backend file read reaches
    for that same store. While the backend ran as a different uid than the
    sandbox, one side's writes locked the other out and 422'd the file list,
    the file body, and the raw bytes — for every topic in the project, not just
    this one. Same uid on both sides → the endpoints keep answering.
    """
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _native_edit(pid, tid, "note.md", "hello\n")

    wt = ws.topic_worktree(pid, tid)
    (wt / "from_the_agent.md").write_text(
        "written in the container\n", encoding="utf-8"
    )
    for args in (
        ["add", "-A"],
        [
            "-c",
            "user.name=芝士",
            "-c",
            "user.email=c@z.l",
            "commit",
            "-m",
            "feat: work",
        ],
    ):
        subprocess.run(["git", *args], cwd=wt, check=True, capture_output=True)

    listed = client.get(
        f"/projects/{pid}/files", params={"topic": str(tid)}, headers=_owner(client)
    )
    assert listed.status_code == 200
    paths = {f["path"] for f in listed.json()["data"]["data"]}
    assert {"note.md", "from_the_agent.md"} <= paths

    body = client.get(
        f"/projects/{pid}/file",
        params={"topic": str(tid), "path": "note.md"},
        headers=_owner(client),
    )
    assert body.status_code == 200

    # A second topic in the same project — the blast radius that made this a P0.
    other = _mktopic(client, pid)
    _native_edit(pid, other, "other.md", "still fine\n")
    assert (
        client.get(
            f"/projects/{pid}/files",
            params={"topic": str(other)},
            headers=_owner(client),
        ).status_code
        == 200
    )


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
        f"/projects/{pid}/git/diff",
        params={"ref": "--help"},
        headers=_owner(client),
    )
    assert r.status_code == 422


def test_merge_delivers_the_branch_and_nothing_else(client):
    """采纳 = 合并那个分支。An edit sitting in the checkout uncommitted was never
    delivered, and the merge must not quietly deliver it for whoever wrote it."""
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "committed.txt", "pushed by the machine\n")
    wt = ws.topic_worktree(pid, tid)
    (wt / "human.txt").write_text("edited by hand, never committed\n", encoding="utf-8")

    assert ws.merge_topic(pid, tid, message=_MSG)["merged"] is True
    assert "pushed by the machine" in ws.read_file(pid, "committed.txt")
    with pytest.raises(ValidationError):
        ws.read_file(pid, "human.txt")


def test_merge_leaves_no_worktree_debris(client):
    """merge_topic runs the actual merge in a throwaway worktree (2026-08-09
    incident fix) — it must be fully cleaned up on both success and conflict,
    never left for a human to notice and clear by hand."""
    pid = _mkproject(client)
    ok_tid, conflict_tid = uuid.uuid4(), uuid.uuid4()
    _native_edit(pid, ok_tid, "clean.txt", "clean merge\n")
    assert ws.merge_topic(pid, ok_tid, message=_MSG)["merged"] is True

    # A guaranteed conflict: branch and base disagree on the same file.
    _native_edit(pid, conflict_tid, "f.txt", "branch version\n")
    repo = ws.ensure_repo(pid)
    (repo / "f.txt").write_text("base version\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "base change"], check=True
    )
    result = ws.merge_topic(pid, conflict_tid, message=_MSG)
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

    hang_branch = ws.branch_for_tree(hang_tid)
    real_run = ws._run_subprocess
    hang_entered = threading.Event()

    def fake_run(argv, cwd, timeout, env=None):
        if "merge" in argv and "--squash" in argv and hang_branch in argv:
            hang_entered.set()
            time.sleep(1.5)  # simulate the git process being stuck
            raise ws.GitTimeoutError("simulated hang, already confirmed dead")
        return real_run(argv, cwd, timeout, env)

    monkeypatch.setattr(ws, "_run_subprocess", fake_run)

    results: dict[str, dict] = {}

    def run_hang() -> None:
        results["hang"] = ws.merge_topic(pid, hang_tid, message=_MSG)

    t = threading.Thread(target=run_hang)
    t.start()
    assert hang_entered.wait(timeout=5), "the hung merge never started"

    start = time.monotonic()
    fast_result = ws.merge_topic(pid, fast_tid, message=_MSG)
    elapsed = time.monotonic() - start

    t.join(timeout=5)
    assert not t.is_alive()

    assert fast_result["merged"] is True
    assert elapsed < 1.0, "the fast merge waited on the hung one — isolation failed"
    assert results["hang"]["merged"] is False

    files = {f["path"] for f in ws.list_files(pid)}
    assert "fast.txt" in files
    assert "hang.txt" not in files  # the stuck merge never landed anything


def test_merge_squashes_the_branch_into_one_authored_commit(client):
    """拍板 #363 (2026-09-07): the platform forge merges the way the GitHub lane
    does — the whole branch lands as ONE squash commit carrying the caller's
    message, authored by the human the work belongs to, committed by 芝士.
    `--no-ff` used to drag every branch commit (含芝士的自动快照) into main."""
    from app.domain.workspace import identity

    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "one.txt", "first\n")
    _native_edit(pid, tid, "two.txt", "second\n")  # a second commit to squash

    repo = ws.ensure_repo(pid)

    def _count() -> int:
        done = subprocess.run(
            ["git", "rev-list", "--count", "main"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return int(done.stdout.strip())

    before = _count()
    message = (
        "feat: deliver both files\n\nWhy this change exists.\n\n"
        "Requested-by: alice\nCheese-Topic: t\nCheese-Card: c"
    )
    author = identity.GitIdentity("Alice", "1+alice@users.noreply.github.com")
    result = ws.merge_topic(pid, tid, message=message, author=author)
    assert result["merged"] is True

    assert _count() == before + 1  # two branch commits → one delivery commit
    done = subprocess.run(
        ["git", "log", "-1", "--format=%P%x00%an%x00%ae%x00%cn%x00%ce%x00%B", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    parents, a_name, a_email, c_name, c_email, body = done.stdout.split("\x00")
    assert len(parents.split()) == 1  # a squash commit, not a merge commit
    assert (a_name, a_email) == ("Alice", "1+alice@users.noreply.github.com")
    assert (c_name, c_email) == (identity.CHEESE_NAME, identity.CHEESE_EMAIL)
    assert body.strip() == message
    # Both files delivered even though their commits are gone from main.
    assert "first" in ws.read_file(pid, "one.txt")
    assert "second" in ws.read_file(pid, "two.txt")


def test_merge_without_an_author_falls_back_to_cheese(client):
    """Nobody resolvable behind the topic (no GitHub connection) degrades to the
    platform identity, never to an invented address."""
    from app.domain.workspace import identity

    pid = _mkproject(client)
    tid = uuid.uuid4()
    _native_edit(pid, tid, "a.txt", "hi\n")
    assert ws.merge_topic(pid, tid, message=_MSG)["merged"] is True
    done = subprocess.run(
        ["git", "log", "-1", "--format=%an%x00%ae", "main"],
        cwd=ws.ensure_repo(pid),
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip().split("\x00") == [
        identity.CHEESE_NAME,
        identity.CHEESE_EMAIL,
    ]


def test_accepting_an_upstream_resolution_still_joins_the_histories(client):
    """merge_topic's ONE non-squash case. A topic that resolves an upstream sync
    conflict exists to JOIN the upstream history; squashing it would land the
    resolved content while upstream's commits stay unreachable from base, so the
    next sync counts itself behind, re-merges, and re-hits the same conflict —
    forever. The proof of the join: after accepting, sync reports 已是最新."""
    import tempfile

    pid = _mkproject(client)
    seeded = uuid.uuid4()
    _native_edit(pid, seeded, "f.txt", "local version\n")
    assert ws.merge_topic(pid, seeded, message=_MSG)["merged"] is True

    upstream = Path(tempfile.mkdtemp(prefix="cheese-upstream-"))
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    (upstream / "f.txt").write_text("upstream version\n", encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.name=up", "-c", "user.email=u@p", "commit", "-q", "-m", "up"],
    ):
        subprocess.run(["git", *args], cwd=upstream, check=True, capture_output=True)

    ws.set_upstream(pid, str(upstream))
    synced = ws.sync_upstream(pid)
    assert synced["synced"] is False and synced["conflicts"] == ["f.txt"]

    tid = uuid.uuid4()
    assert ws.prepare_upstream_conflict_resolution(pid, tid) == ["f.txt"]
    wt = ws.topic_worktree(pid, tid)
    (wt / "f.txt").write_text("resolved version\n", encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.name=芝士", "-c", "user.email=c@z.l", "commit", "-q", "-m", "fix"],
    ):
        subprocess.run(["git", *args], cwd=wt, check=True, capture_output=True)

    assert ws.merge_topic(pid, tid, message=_MSG)["merged"] is True
    assert "resolved version" in ws.read_file(pid, "f.txt")
    again = ws.sync_upstream(pid)
    assert again == {"synced": True, "commits": 0, "reason": "已是最新"}
