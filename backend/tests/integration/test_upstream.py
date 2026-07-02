"""Upstream repo link + sync (spec §6.3 关联已有 repo): a project can bind an
existing git repo and pull its history in — the productized version of the
one-off dogfooding seed."""

import subprocess
from pathlib import Path


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _make_upstream(tmp_path: Path, name: str = "up") -> Path:
    """A real local git repo with one commit, to act as the upstream."""
    repo = tmp_path / name
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

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
    r = client.get(f"/api/projects/{pid}/file", params={"path": "hello.txt"})
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
    r = client.get(f"/api/projects/{pid}/file", params={"path": "more.txt"})
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
