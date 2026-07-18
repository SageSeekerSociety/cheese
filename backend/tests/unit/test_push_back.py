"""push_back: remote (GitHub) upstreams get the dogfood/<topic> push (their own
CI takes over); local-path upstreams additionally run the trusted hook; no
upstream = clean skip."""

import uuid

import pytest

from app.domain.workspace import service as ws


@pytest.fixture
def repo_stub(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: tmp_path)
    monkeypatch.setattr(ws, "_base_branch", lambda repo: "main")
    monkeypatch.setattr(
        ws, "_git", lambda repo, *args, **kw: calls.append(args) or ""
    )
    return calls


def test_remote_upstream_pushes_without_hook(monkeypatch, repo_stub):
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    tid = uuid.uuid4()
    out = ws.push_back(uuid.uuid4(), tid)
    assert out == {
        "pushed": True,
        "branch": f"dogfood/{tid.hex[:8]}",
        "hook": False,
    }
    assert any(a[0] == "push" for a in repo_stub)


def test_no_upstream_skips(monkeypatch, repo_stub):
    monkeypatch.setattr(ws, "get_upstream", lambda pid: None)
    out = ws.push_back(uuid.uuid4(), uuid.uuid4())
    assert out["pushed"] is False
    assert not repo_stub  # nothing pushed
