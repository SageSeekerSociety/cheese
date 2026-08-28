"""一棵树的分支只有一个作者：干活的那台机器。

An agent writes its tree's branch by pushing over the project's git proxy
(`/projects/{id}/git`) — its files do not live in the backend's worktree at all.
The backend's worktree is made lazily (a file panel, a diff) and only ever read,
so by the time it exists the branch can already carry commits it has never seen.

Filing a card pushes that branch to GitHub, and the PR is worth exactly what the
branch carries.

These are functional tests against real repositories on disk. Nothing here reads
the implementation: every assertion is about a file's content in the bare repo
standing in for GitHub, about the files visible in the worktree, or about where
the tree's branch points afterwards.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.domain.workspace import service as ws


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {args}: {result.stderr or result.stdout}"
    return result.stdout


def _commit_all(repo: Path, message: str) -> None:
    _run(repo, "add", "-A")
    _run(
        repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message
    )


def _contains(repo: Path, ancestor: str, head: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, head],
            capture_output=True,
        ).returncode
        == 0
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(ws.settings, "workspace_root", str(root))
    return root


@pytest.fixture
def project(workspace_root) -> tuple[uuid.UUID, Path]:
    pid = uuid.uuid4()
    repo = ws.ensure_repo(pid)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base: readme")
    return pid, repo


def _github(tmp_path: Path, repo: Path) -> Path:
    """A bare repo standing in for the connected GitHub repo, seeded from the
    project's base branch as 关联已有 repo would leave it."""
    bare = tmp_path / "github.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _run(repo, "push", "-q", str(bare), "main:refs/heads/main")
    return bare


def _push_over_the_git_proxy(
    tmp_path: Path, repo: Path, tree_id: uuid.UUID, files: dict[str, str]
) -> str:
    """An agent pushes its work into the tree's branch the way a sandbox does:
    clone the project repo, commit, push the branch back. The backend's own
    worktree is never touched — this is the whole point."""
    work = tmp_path / f"sandbox-{uuid.uuid4().hex[:6]}"
    subprocess.run(["git", "clone", "-q", str(repo), str(work)], check=True)
    branch = ws.branch_for_tree(tree_id)
    if _run(work, "branch", "-r", "--list", f"origin/{branch}").strip():
        _run(work, "checkout", "-q", "-B", branch, f"origin/{branch}")
    for rel, content in files.items():
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _commit_all(work, "feat: the work this card delivers")
    _run(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return _run(repo, "rev-parse", branch).strip()


def _open_pr(pid: uuid.UUID, tid: uuid.UUID) -> dict:
    return ws.push_topic_branch_for_github_pr(
        pid,
        tid,
        owner="acme",
        repo="widgets",
        remote_branch="cheesex/x",
        token="test-token",
    )


def test_pr_carries_work_pushed_over_the_git_proxy(tmp_path, project, monkeypatch):
    """The bug: a card filed for a tree whose work arrived by push opened a PR
    with nothing in it, and accepting that PR merged nothing into main."""
    pid, repo = project
    tid = uuid.uuid4()
    pushed = _push_over_the_git_proxy(
        tmp_path, repo, tid, {"src/app.py": "print('hi')\n"}
    )
    bare = _github(tmp_path, repo)
    monkeypatch.setattr(ws, "_github_push_url", lambda owner, repo: str(bare))

    result = _open_pr(pid, tid)

    # What the PR's diff is made of — the thing that was 0 files.
    assert _run(bare, "diff", "--name-only", "main", result["head_sha"]).split() == [
        "src/app.py"
    ]
    assert _run(bare, "show", f"{result['head_sha']}:src/app.py") == "print('hi')\n"
    # And opening it did not move the branch out from under what was pushed.
    assert result["head_sha"] == pushed
    assert _run(repo, "rev-parse", ws.branch_for_tree(tid)).strip() == pushed


def test_first_worktree_joins_the_work_the_branch_already_carries(tmp_path, project):
    """The backend makes a tree's worktree lazily — a file panel, a sandbox
    launch, a snapshot. By then the branch can already carry work. Creating the
    worktree has to start from that work: anything else both hides the files
    from whoever opened the panel and stages the branch to be rewound."""
    pid, repo = project
    tid = uuid.uuid4()
    pushed = _push_over_the_git_proxy(
        tmp_path, repo, tid, {"src/app.py": "print('hi')\n"}
    )

    wt = ws.topic_worktree(pid, tid)

    assert _run(repo, "rev-parse", ws.branch_for_tree(tid)).strip() == pushed
    assert (wt / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
