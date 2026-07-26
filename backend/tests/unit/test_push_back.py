"""push_back: 采纳 IS the merge, so a remote upstream receives THAT merge on its
own default branch (fast-forward, never forced). A refused push falls back to a
side branch and SAYS so; local-path upstreams keep the branch + trusted hook; no
upstream = clean skip. Landing must never require cheese-specific setup in the
target repo."""

import uuid

import pytest

from app.domain.workspace import service as ws


@pytest.fixture
def repo_stub(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: tmp_path)
    monkeypatch.setattr(ws, "_base_branch", lambda repo: "main")
    monkeypatch.setattr(ws, "_git", lambda repo, *args, **kw: calls.append(args) or "")
    return calls


def test_remote_upstream_fast_forwards_its_default_branch(monkeypatch, repo_stub):
    """The accepted merge lands on the upstream trunk itself — no side branch, no
    workflow the target repo has to install first."""
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "trunk")
    out = ws.push_back(uuid.uuid4(), uuid.uuid4())
    assert out == {"pushed": True, "mode": "upstream", "target": "trunk"}
    pushes = [a for a in repo_stub if a[0] == "push"]
    assert pushes == [("push", ws.UPSTREAM_REMOTE, "main:trunk")]
    assert all("-f" not in a for a in pushes), "the upstream trunk is never forced"


def test_refused_push_falls_back_to_a_branch_and_reports_why(monkeypatch, tmp_path):
    """A protected/diverged trunk must not silently swallow the work: keep it on a
    branch AND return the reason so the accept can show it."""
    calls: list[tuple] = []

    def fake_git(repo, *args, **kw):
        calls.append(args)
        if args[:2] == ("push", ws.UPSTREAM_REMOTE) and "-f" not in args:
            raise ws.ValidationError("protected branch hook declined")
        return ""

    monkeypatch.setattr(ws, "ensure_repo", lambda pid: tmp_path)
    monkeypatch.setattr(ws, "_base_branch", lambda repo: "main")
    monkeypatch.setattr(ws, "_git", fake_git)
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    tid = uuid.uuid4()
    out = ws.push_back(uuid.uuid4(), tid)
    assert out["mode"] == "branch"
    assert out["branch"] == f"dogfood/{tid.hex[:8]}"
    assert "protected branch" in out["reason"]


def test_no_upstream_skips(monkeypatch, repo_stub):
    monkeypatch.setattr(ws, "get_upstream", lambda pid: None)
    out = ws.push_back(uuid.uuid4(), uuid.uuid4())
    assert out["pushed"] is False
    assert not repo_stub  # nothing pushed
