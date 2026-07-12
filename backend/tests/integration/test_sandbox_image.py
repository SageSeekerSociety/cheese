"""Per-project env image (spec §9.1): a project can run its agent on a specific
sandbox image (e.g. cheesex-dev:v0 for dogfooding on this repo)."""

import uuid

from app.core.config import settings
from app.domain.agent.compute import LocalDockerProvider
from app.domain.workspace import service as ws


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _current(client, pid: str):
    return client.get(f"/api/projects/{pid}/sandbox-image").json()["data"]["current"]


def test_get_defaults_to_pool_image(client):
    pid = _project(client)
    d = client.get(f"/api/projects/{pid}/sandbox-image").json()["data"]
    assert d["current"] is None  # unset = using the pool default
    assert d["default"] == settings.sandbox_image
    assert any(o["image"] == "cheesex-dev:v0" for o in d["options"])


def test_set_and_clear_sandbox_image(client):
    pid = _project(client)
    r = client.put(
        f"/api/projects/{pid}/sandbox-image", json={"image": "cheesex-dev:v0"}
    )
    assert r.status_code == 200 and r.json()["data"]["current"] == "cheesex-dev:v0"
    assert _current(client, pid) == "cheesex-dev:v0"
    # Empty → revert to the pool default.
    r = client.put(f"/api/projects/{pid}/sandbox-image", json={"image": ""})
    assert r.json()["data"]["current"] is None
    assert _current(client, pid) is None


def test_invalid_image_rejected(client):
    pid = _project(client)
    for bad in ["bad image", "a;rm -rf", "-x/y"]:
        r = client.put(f"/api/projects/{pid}/sandbox-image", json={"image": bad})
        assert r.status_code == 422, bad


def test_sandbox_config_uses_project_image(monkeypatch, tmp_path):
    # The plumbing: a resolved image lands in the container's SBX_IMAGE env; when
    # unset it falls back to the pool default.
    monkeypatch.setattr(ws, "topic_worktree", lambda p, t: tmp_path / "wt")
    monkeypatch.setattr(ws, "container_name", lambda t: "c")
    monkeypatch.setattr(ws, "session_dir", lambda p, t: tmp_path / "sess")
    monkeypatch.setattr("app.domain.agent.compute.mint_scoped_token", lambda **_: "tok")
    prov = LocalDockerProvider(
        agent=None, workspace_root=str(tmp_path), sandbox_enabled=True
    )
    pid, tid = uuid.uuid4(), uuid.uuid4()
    sbx, _ = prov._sandbox_config(
        pid,
        tid,
        memory_scope=None,
        owner=None,
        turn_id=None,
        sandbox_image="cheesex-dev:v0",
    )
    assert sbx["env"]["SBX_IMAGE"] == "cheesex-dev:v0"

    sbx2, _ = prov._sandbox_config(
        pid,
        tid,
        memory_scope=None,
        owner=None,
        turn_id=None,
        sandbox_image=None,
    )
    assert sbx2["env"]["SBX_IMAGE"] == settings.sandbox_image
