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
