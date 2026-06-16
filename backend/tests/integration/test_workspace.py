"""Project git workspace — files / git log / diff (Phase 4)."""

import uuid

from app.domain.workspace import service as ws


def test_write_file_then_browse_and_diff(client):
    pid = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]

    # 芝士 would call write_file via its tool; exercise the same service path.
    ws.write_file(uuid.UUID(pid), path="src/app.py", content="print('hi')\n")

    files = client.get(f"/api/projects/{pid}/files").json()["data"]["data"]
    assert any(f["path"] == "src/app.py" for f in files)

    content = client.get(f"/api/projects/{pid}/file?path=src/app.py").json()["data"][
        "content"
    ]
    assert "print('hi')" in content

    log = client.get(f"/api/projects/{pid}/git/log").json()["data"]["data"]
    assert len(log) >= 1

    diff = client.get(f"/api/projects/{pid}/git/diff").json()["data"]["diff"]
    assert "print('hi')" in diff


def test_topic_branch_isolated_then_merged(client):
    # spec §6.3: a topic's writes live on its own branch; 采纳 = merge to base.
    pr = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = uuid.UUID(pr["id"])
    tid = uuid.uuid4()

    ws.write_file(pid, path="feat.txt", content="branch work\n", topic_id=tid)
    # The change is on the topic branch vs the base (not yet merged).
    assert "branch work" in ws.topic_diff(pid, tid)

    res = ws.merge_topic(pid, tid)
    assert res["merged"] is True

    # The base branch now contains the file.
    assert any(f["path"] == "feat.txt" for f in ws.list_files(pid))


def test_project_write_goes_to_base_not_topic_branch(client):
    # Project-level writes land on base; a topic's file stays on its branch until
    # merged (no cross-contamination).
    pr = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = uuid.UUID(pr["id"])
    tid = uuid.uuid4()
    ws.write_file(pid, path="topic_only.txt", content="t\n", topic_id=tid)
    ws.write_file(pid, path="project_wide.txt", content="p\n")  # no topic → base

    names = {f["path"] for f in ws.list_files(pid)}  # working tree is now base
    assert "project_wide.txt" in names
    assert "topic_only.txt" not in names  # still isolated on the topic branch


def test_git_diff_rejects_option_injection(client):
    pr = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = uuid.UUID(pr["id"])
    ws.write_file(pid, path="a.txt", content="x\n")
    r = client.get(f"/api/projects/{pid}/git/diff", params={"ref": "--help"})
    assert r.status_code == 422
