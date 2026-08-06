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


def test_upstream_is_synced_before_landing(monkeypatch, repo_stub):
    """The upstream moves; taking its commits first is what keeps 采纳 landing on
    the trunk instead of silently degrading to a side branch."""
    calls: list[str] = []
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")
    monkeypatch.setattr(
        ws,
        "sync_upstream",
        lambda pid: calls.append("sync") or {"synced": True, "commits": 3},
    )
    out = ws.push_back(uuid.uuid4(), uuid.uuid4())
    assert calls == ["sync"], "sync must run before the push"
    assert out["mode"] == "upstream"


def test_failed_sync_blocks_the_push(monkeypatch, repo_stub):
    """A conflicted sync must NOT be papered over by pushing a stale base."""
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")
    monkeypatch.setattr(
        ws, "sync_upstream", lambda pid: {"synced": False, "reason": "合并冲突：a.py"}
    )
    out = ws.push_back(uuid.uuid4(), uuid.uuid4())
    assert out["mode"] == "blocked"
    assert "a.py" in out["reason"]
    assert not [a for a in repo_stub if a[0] == "push"], "nothing may be pushed"


def test_remote_upstream_fast_forwards_its_default_branch(monkeypatch, repo_stub):
    """The accepted merge lands on the upstream trunk itself — no side branch, no
    workflow the target repo has to install first."""
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/org/repo.git"
    )
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "trunk")
    monkeypatch.setattr(ws, "sync_upstream", lambda pid: {"synced": True})
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
    monkeypatch.setattr(ws, "sync_upstream", lambda pid: {"synced": True})
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
