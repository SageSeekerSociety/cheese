"""Upstream repo link + sync (spec §6.3 关联已有 repo): a project can bind an
existing git repo and pull its history in — the productized version of the
one-off dogfooding seed."""

import subprocess
from pathlib import Path


def _owner(client, handle: str = "alice") -> dict[str, str]:
    """The file routes return the source, so they need a caller with a claim on
    the project."""
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, handle)}"}


def _project(client) -> str:
    return client.post(
        "/api/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]["id"]


def _make_upstream(tmp_path: Path, name: str = "up") -> Path:
    """A real local git repo with one commit, to act as the upstream."""
    repo = tmp_path / name
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "hello.txt").write_text("hi from upstream\n")
    git("add", "-A")
    git("commit", "-q", "-m", "upstream: hello")
    return repo


def test_upstream_unset_by_default(client):
    pid = _project(client)
    r = client.get(f"/api/projects/{pid}/upstream")
    assert r.status_code == 200 and r.json()["data"]["url"] is None
    # Syncing without a link is a no-op with a clear reason, not an error.
    r = client.post(f"/api/projects/{pid}/upstream/sync")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["synced"] is False and "未关联" in d["reason"]


def test_link_sync_and_resync(client, tmp_path):
    up = _make_upstream(tmp_path)
    pid = _project(client)

    r = client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})
    assert r.status_code == 200 and r.json()["data"]["url"] == str(up)
    assert client.get(f"/api/projects/{pid}/upstream").json()["data"]["url"] == str(up)

    # First sync: unrelated histories merge in cleanly, upstream file appears.
    r = client.post(f"/api/projects/{pid}/upstream/sync")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["synced"] is True and d["commits"] >= 1
    r = client.get(
        f"/api/projects/{pid}/file",
        params={"path": "hello.txt"},
        headers=_owner(client),
    )
    assert r.status_code == 200
    assert "hi from upstream" in r.json()["data"]["content"]

    # Nothing new upstream → up to date, no merge commit spam.
    d = client.post(f"/api/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] == 0

    # New upstream commit → next sync picks it up.
    (up / "more.txt").write_text("again\n")
    subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(up), "commit", "-q", "-m", "upstream: more"], check=True
    )
    d = client.post(f"/api/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] >= 1
    r = client.get(
        f"/api/projects/{pid}/file", params={"path": "more.txt"}, headers=_owner(client)
    )
    assert r.status_code == 200


def test_sync_conflict_aborts_and_names_files(client, tmp_path):
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    # Give the project's main a commit whose hello.txt differs from upstream's —
    # the unrelated-histories merge then hits an add/add conflict.
    repo = ws.ensure_repo(_uuid.UUID(pid))
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )

    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})
    d = client.post(f"/api/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is False
    assert "hello.txt" in d["reason"] and d["conflicts"] == ["hello.txt"]
    # Aborted cleanly: no half-merge left behind, local content intact.
    assert (repo / "hello.txt").read_text() == "local version\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_invalid_upstream_url_rejected(client):
    pid = _project(client)
    for bad in ["-x", "ext::sh -c id", "a b", "http://insecure", "file:///etc"]:
        r = client.put(f"/api/projects/{pid}/upstream", json={"url": bad})
        assert r.status_code == 422, bad


def test_unlink_upstream(client, tmp_path):
    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})
    r = client.put(f"/api/projects/{pid}/upstream", json={"url": ""})
    assert r.status_code == 200 and r.json()["data"]["url"] is None
    assert client.get(f"/api/projects/{pid}/upstream").json()["data"]["url"] is None


def test_accept_pushes_back_and_fires_hook(client, tmp_path):
    """采纳即上线: accepting a topic pushes the merged base branch to a local
    upstream as dogfood/<topic> and runs the repo's on-dogfood-push.sh hook."""
    import time
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    marker = up / "hook-ran.txt"
    hook = up / "scripts" / "on-dogfood-push.sh"
    hook.parent.mkdir()
    hook.write_text('#!/bin/sh\necho "$1" > hook-ran.txt\n')
    hook.chmod(0o755)

    pid = _project(client)
    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})
    r = client.post(
        "/api/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    )
    tid = r.json()["data"]["id"]

    # Simulate a sandbox turn's edit, then run the accept flow end-to-end.
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)
    wt = ws.topic_worktree(puid, tuid)
    (wt / "work.txt").write_text("accepted work\n")
    ws.snapshot_worktree(puid, tuid)

    card = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "u", "routing_reason": ""},
    ).json()["data"]["id"]
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200

    branch = f"dogfood/{tuid.hex[:8]}"
    out = subprocess.run(
        ["git", "-C", str(up), "show", f"{branch}:work.txt"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0 and "accepted work" in out.stdout
    # The detached hook runs asynchronously — give it a moment.
    for _ in range(30):
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.read_text().strip() == branch


def test_accept_conflict_is_a_state_not_a_lie(client):
    """采纳冲突 (spec §6.3): a conflicting merge must NOT archive the topic —
    the card enters `conflict`, the workspace gets the materialized merge for
    芝士 to resolve, and a retry after resolution completes the accept."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    pid = _project(client)
    r = client.post(
        "/api/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    )
    tid = r.json()["data"]["id"]
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    # Branch edits f.txt one way…
    wt = ws.topic_worktree(puid, tuid)
    (wt / "f.txt").write_text("branch version\n")
    ws.snapshot_worktree(puid, tuid)
    # …and base edits it the other way → guaranteed conflict.
    repo = ws.ensure_repo(puid)
    (repo / "f.txt").write_text("base version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "base change"], check=True
    )

    card = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "u", "routing_reason": ""},
    ).json()["data"]["id"]
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "conflict"
    assert "f.txt" in d["note"]
    # The topic is NOT archived — the work is not stranded silently.
    t = client.get(f"/api/topics/{tid}").json()["data"]
    assert t["status"] == "active"
    # The workspace holds the materialized conflict for 芝士.
    content = (wt / "f.txt").read_text()
    assert "<<<<<<<" in content or "base version" in content

    # Simulate 芝士 resolving: write the merged truth, snapshot.
    (wt / "f.txt").write_text("merged version\n")
    ws.snapshot_worktree(puid, tuid, "解决采纳冲突")

    # Retry accept → clean merge, archived, base has the resolution.
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "accepted"
    t = client.get(f"/api/topics/{tid}").json()["data"]
    assert t["status"] == "archived"
    assert ws.read_file(puid, "f.txt") == "merged version\n"


def test_upstream_conflict_materializes_and_accepting_completes_the_sync(
    client, tmp_path
):
    """同步上游冲突不是死路 (the gap this closes): 同步上游 aborting cleanly is
    correct for the shared repo, but on its own it leaves the project unable to
    ever pull — every later sync hits the same wall. The conflict must land in a
    topic's workspace with markers, and accepting that topic must finish the
    sync that aborted."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    puid = _uuid.UUID(pid)

    # Local main and upstream both have hello.txt with different content → the
    # unrelated-histories merge is an add/add conflict.
    repo = ws.ensure_repo(puid)
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})
    d = client.post(f"/api/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is False and d["conflicts"] == ["hello.txt"]

    # A topic to resolve it in, then materialize the conflict there.
    tid = client.post(
        "/api/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    ).json()["data"]["id"]
    tuid = _uuid.UUID(tid)
    files = ws.prepare_upstream_conflict_resolution(puid, tuid)
    assert files == ["hello.txt"]

    # Both sides are visible in the working copy — 芝士 can actually merge them
    # by hand rather than guessing which side to keep.
    body = (ws.topic_worktree(puid, tuid) / "hello.txt").read_text()
    assert "<<<<<<<" in body
    assert "local version" in body and "hi from upstream" in body

    # 芝士 resolves; the platform snapshots as it does after any turn.
    (ws.topic_worktree(puid, tuid) / "hello.txt").write_text("merged by hand\n")
    ws.snapshot_worktree(puid, tuid, "解决同步上游冲突")

    card = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "u", "routing_reason": ""},
    ).json()["data"]["id"]
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "accepted"

    # The resolution is on base…
    assert ws.read_file(puid, "hello.txt") == "merged by hand\n"
    # …and the sync is genuinely DONE: upstream is now an ancestor of base, so
    # the next sync has nothing left to bring over. This is the assertion that
    # proves the merge carried the upstream history, not just the file edit.
    d = client.post(f"/api/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] == 0


def test_sync_conflict_dispatches_cheese_at_the_materialized_merge(client, tmp_path):
    """The exit, wired to the button people actually press: a conflicting 同步上游
    by a logged-in caller creates a resolution task in their 1:1 room with 芝士
    and materializes the merge there. Without this the route reported a conflict
    and stopped, and the project could never pull again."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    puid = _uuid.UUID(pid)
    repo = ws.ensure_repo(puid)
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})

    d = client.post(
        f"/api/projects/{pid}/upstream/sync", headers=_owner(client, "alice")
    ).json()["data"]

    # The sync itself still tells the truth: aborted, and which files.
    assert d["synced"] is False and d["conflicts"] == ["hello.txt"]
    # …and now there is somewhere to go.
    assert d["dispatched"]["files"] == ["hello.txt"]
    tid = d["dispatched"]["topic_id"]

    # The task is real, carries the conflict in its workspace, and hangs under
    # the caller's 1:1 room rather than polluting the project's topic list.
    t = client.get(f"/api/topics/{tid}").json()["data"]
    assert t["title"] == "解决同步上游冲突"
    body = (ws.topic_worktree(puid, _uuid.UUID(tid)) / "hello.txt").read_text()
    assert "<<<<<<<" in body
    assert "local version" in body and "hi from upstream" in body

    # The shared repo is untouched — dispatching must not half-merge either.
    assert (repo / "hello.txt").read_text() == "local version\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_second_sync_reuses_the_open_resolution_task(client, tmp_path):
    """Pressing 同步上游 again while a resolution is open must point back at it,
    not start a second one: re-materializing would overwrite whatever 芝士 has
    already resolved, and the room would fill with identical dead tasks."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    repo = ws.ensure_repo(_uuid.UUID(pid))
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/api/projects/{pid}/upstream", json={"url": str(up)})

    headers = _owner(client, "alice")
    first = client.post(f"/api/projects/{pid}/upstream/sync", headers=headers).json()[
        "data"
    ]["dispatched"]

    # 芝士 has started resolving — this content must survive a second press.
    wt = ws.topic_worktree(_uuid.UUID(pid), _uuid.UUID(first["topic_id"]))
    (wt / "hello.txt").write_text("half-resolved by 芝士\n")

    second = client.post(f"/api/projects/{pid}/upstream/sync", headers=headers).json()[
        "data"
    ]["dispatched"]
    assert second["topic_id"] == first["topic_id"] and second["reused"] is True
    assert (wt / "hello.txt").read_text() == "half-resolved by 芝士\n"
