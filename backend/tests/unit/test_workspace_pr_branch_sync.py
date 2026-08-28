"""两阶段采纳: pushing a topic branch to GitHub when the branch's workflow files
lag the target repo's default branch.

GitHub refuses a push whose `.github/workflows/` tree differs from the DEFAULT
BRANCH's when the credential has no `workflows` scope — it compares trees, not
this push's diff. Topic branches fork off the platform's local base, which only
advances on 同步上游, so almost every card was rejected and two-phase accept
degraded to direct-merge for essentially everything.

These are functional tests against real git repos on disk: a bare repo stands in
for GitHub, and its `pre-receive` hook reproduces GitHub's actual check (compare
the incoming branch's `.github/workflows` against `main`, reject with GitHub's
own wording). Nothing here reads the implementation — the assertions are about
what lands in the "GitHub" repo, what the local branch head is afterwards, and
what a caller is told when the push can't be rescued.

The degrade contract itself (a ValidationError out of the push must NOT fail the
accept — the card lands `accepted` with the reason in its note) is covered in
tests/integration/test_accept_pr.py::
test_accept_pr_open_failure_degrades_with_github_call_failed_reason; the errors
raised here travel that exact path.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.domain.workspace import service as ws

WORKFLOW = ".github/workflows/e2e.yml"
_E2E_V2 = "name: e2e\n# v2\n"

# GitHub's own refusal, as it reaches git's stderr.
_REFUSAL = (
    "refusing to allow a GitHub App to create or update workflow "
    "\\`.github/workflows/e2e.yml\\` without \\`workflows\\` permission"
)

_PRE_RECEIVE = f"""#!/bin/sh
# Stand-in for GitHub's workflows-scope check: every incoming branch is compared
# against the default branch's .github/workflows tree, NOT against the push's
# own diff — which is why a branch that merely lags gets rejected too.
while read -r old new ref; do
    case "$ref" in
        refs/heads/main) continue ;;
    esac
    if ! git diff --quiet refs/heads/main "$new" -- .github/workflows; then
        echo "{_REFUSAL}" >&2
        exit 1
    fi
done
exit 0
"""

_HOSTILE_PRE_RECEIVE = """#!/bin/sh
echo "remote: Permission to acme/widgets.git denied" >&2
exit 1
"""


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {args}: {result.stderr or result.stdout}"
    return result.stdout


def _commit_all(repo: Path, message: str) -> None:
    _run(repo, "add", "-A")
    _run(
        repo,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        message,
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(ws.settings, "workspace_root", str(root))
    return root


@pytest.fixture
def project(workspace_root) -> tuple[uuid.UUID, Path]:
    """A project whose base branch already carries a workflow file — the state
    every real project is in."""
    pid = uuid.uuid4()
    repo = ws.ensure_repo(pid)
    (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / WORKFLOW).write_text("name: e2e\n# v1\n", encoding="utf-8")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base: workflows + readme")
    return pid, repo


def _make_github(tmp_path: Path, repo: Path, *, name: str = "github.git") -> Path:
    """A bare repo standing in for the connected GitHub repo, seeded from the
    project's base branch (as 关联已有 repo would leave it) and guarded by the
    workflows-scope hook."""
    bare = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _run(repo, "push", "-q", str(bare), "main:refs/heads/main")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(_PRE_RECEIVE, encoding="utf-8")
    hook.chmod(0o755)
    return bare


def _advance_github(tmp_path: Path, bare: Path, files: dict[str, str]) -> None:
    """Move the GitHub default branch forward — the thing the platform's local
    base does not see until the next 同步上游."""
    work = tmp_path / f"gh-work-{uuid.uuid4().hex[:6]}"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    for rel, content in files.items():
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _commit_all(work, "upstream moved")
    _run(work, "push", "-q", "origin", "main")


def _topic_commit(pid: uuid.UUID, tid: uuid.UUID, rel: str, content: str) -> None:
    """A sandbox turn: native tools write into the topic's workspace, the
    platform snapshots it onto the topic branch."""
    wt = ws.topic_worktree(pid, tid)
    target = wt / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ws.snapshot_worktree(pid, tid, "芝士 edits")


def _use_github(monkeypatch, bare: Path) -> None:
    monkeypatch.setattr(ws, "_github_push_url", lambda owner, repo: str(bare))


def _push(pid: uuid.UUID, tid: uuid.UUID, remote_branch: str = "cheesex/x") -> dict:
    return ws.push_topic_branch_for_github_pr(
        pid,
        tid,
        owner="acme",
        repo="widgets",
        remote_branch=remote_branch,
        token="test-token",
    )


def _branch_head(repo: Path, tid: uuid.UUID) -> str:
    return _run(repo, "rev-parse", ws.branch_for_tree(tid)).strip()


def _remote_head(bare: Path, ref: str = "refs/heads/cheesex/x") -> str:
    return _run(bare, "rev-parse", ref).strip()


def test_branch_with_lagging_workflows_still_reaches_github(
    tmp_path, project, monkeypatch
):
    """The bug this fixes: a card that never touched a workflow file, on a
    branch forked from a base that lags GitHub's main. It used to be rejected
    outright; now the push lands, carrying both the card's work and GitHub's
    current workflow files."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(tmp_path, bare, {WORKFLOW: _E2E_V2})
    _topic_commit(pid, tid, "src/app.py", "print('hi')\n")
    _use_github(monkeypatch, bare)

    result = _push(pid, tid)

    assert _remote_head(bare) == result["head_sha"]
    assert result["remote_branch"] == "cheesex/x"
    # The card's own work is there...
    assert _run(bare, "show", f"{result['head_sha']}:src/app.py") == "print('hi')\n"
    # ...and so is GitHub's current workflow file, which is what stops the
    # rejection: the pushed tree no longer "changes" a workflow.
    assert "# v2" in _run(bare, "show", f"{result['head_sha']}:{WORKFLOW}")
    # The reported head is the real local branch head — advance_pr_card and
    # _repush_if_local_head_moved both compare against it.
    assert _branch_head(repo, tid) == result["head_sha"]


def test_push_that_github_accepts_costs_nothing_extra(tmp_path, project, monkeypatch):
    """Happy path is untouched: no sync, no fetch, no merge commit. This is what
    keeps the 60s re-push poll as cheap as it was."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)  # GitHub has NOT moved
    _topic_commit(pid, tid, "src/app.py", "print('hi')\n")
    _use_github(monkeypatch, bare)
    head_before = _branch_head(repo, tid)

    result = _push(pid, tid)

    assert result["head_sha"] == head_before  # no merge commit was created
    assert _remote_head(bare) == head_before
    # Nothing was fetched: a sync would have left the fetched base ref behind.
    assert _run(repo, "for-each-ref", "refs/cheesex/").strip() == ""


def test_repeated_pushes_after_a_sync_do_not_keep_moving_the_branch(
    tmp_path, project, monkeypatch
):
    """The re-push poll runs every 60s. Once synced, further pushes must be
    no-ops — otherwise each tick stacks another merge commit and the branch
    head never matches card.pr_head_sha again."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(tmp_path, bare, {WORKFLOW: _E2E_V2})
    _topic_commit(pid, tid, "src/app.py", "print('hi')\n")
    _use_github(monkeypatch, bare)

    first = _push(pid, tid)
    second = _push(pid, tid)
    third = _push(pid, tid)

    assert first["head_sha"] == second["head_sha"] == third["head_sha"]
    assert _branch_head(repo, tid) == first["head_sha"]
    assert _remote_head(bare) == first["head_sha"]


def test_a_later_agent_commit_builds_on_the_synced_branch(
    tmp_path, project, monkeypatch
):
    """芝士 pushes a CI fix after the PR is open. Its new commit must sit on top
    of the sync merge, not replace it — a branch that rewound would make every
    later re-push a rejected non-fast-forward."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(tmp_path, bare, {WORKFLOW: _E2E_V2})
    _topic_commit(pid, tid, "src/app.py", "print('hi')\n")
    _use_github(monkeypatch, bare)
    synced = _push(pid, tid)["head_sha"]

    _topic_commit(pid, tid, "src/app.py", "print('fixed')\n")
    fixed_head = _branch_head(repo, tid)

    assert fixed_head != synced
    contains = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", synced, fixed_head],
        capture_output=True,
    )
    assert contains.returncode == 0, "the sync merge was dropped from the branch"

    result = _push(pid, tid)
    assert result["head_sha"] == fixed_head
    assert _remote_head(bare) == fixed_head
    assert _run(bare, "show", f"{fixed_head}:src/app.py") == "print('fixed')\n"


def test_conflicting_sync_reports_the_conflict_and_leaves_the_branch_alone(
    tmp_path, project, monkeypatch
):
    """Sync is a merge, so it can conflict. The push then fails with a legible
    reason (the caller degrades to direct-merge and puts this on the card) and
    the topic branch must be left exactly where it was — never half-merged."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(
        tmp_path,
        bare,
        {WORKFLOW: _E2E_V2, "README.md": "rewritten upstream\n"},
    )
    _topic_commit(pid, tid, "README.md", "rewritten in the topic\n")
    _use_github(monkeypatch, bare)
    head_before = _branch_head(repo, tid)

    with pytest.raises(ValidationError) as excinfo:
        _push(pid, tid)

    message = str(excinfo.value)
    assert "冲突" in message
    assert "README.md" in message
    assert "test-token" not in message
    assert _branch_head(repo, tid) == head_before


def test_card_that_edits_a_workflow_itself_is_still_rejected(
    tmp_path, project, monkeypatch
):
    """Not every rejection is rescuable, and pretending otherwise would hide a
    real limit: a card whose OWN change touches a workflow file is refused even
    when the branch is level with GitHub. Expected behaviour, not a bug — but
    the reason has to say so."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)  # GitHub has NOT moved
    _topic_commit(pid, tid, WORKFLOW, "name: e2e\n# edited by the card\n")
    _use_github(monkeypatch, bare)
    head_before = _branch_head(repo, tid)

    with pytest.raises(ValidationError) as excinfo:
        _push(pid, tid)

    assert "这张卡本身" in str(excinfo.value)
    assert _branch_head(repo, tid) == head_before


def test_card_editing_a_workflow_on_a_lagging_branch_is_rejected_after_syncing(
    tmp_path, project, monkeypatch
):
    """Same limit, reached the long way: the branch also lags, so the sync runs
    and succeeds — and GitHub still refuses, because the card's own workflow
    edit survives the merge. Exactly one retry, then degrade."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(
        tmp_path, bare, {".github/workflows/release.yml": "name: release\n"}
    )
    _topic_commit(pid, tid, WORKFLOW, "name: e2e\n# edited by the card\n")
    _use_github(monkeypatch, bare)

    with pytest.raises(ValidationError) as excinfo:
        _push(pid, tid)

    assert "refusing to allow" in str(excinfo.value)


def test_other_push_failures_are_not_retried(tmp_path, project, monkeypatch):
    """The retry is narrow on purpose: a rejection that has nothing to do with
    workflow permissions (revoked token, protected branch) degrades immediately,
    with no fetch and no merge."""
    pid, repo = project
    tid = uuid.uuid4()
    bare = _make_github(tmp_path, repo)
    _advance_github(tmp_path, bare, {WORKFLOW: _E2E_V2})
    (bare / "hooks" / "pre-receive").write_text(_HOSTILE_PRE_RECEIVE, encoding="utf-8")
    _topic_commit(pid, tid, "src/app.py", "print('hi')\n")
    _use_github(monkeypatch, bare)
    head_before = _branch_head(repo, tid)

    with pytest.raises(ValidationError) as excinfo:
        _push(pid, tid)

    assert "denied" in str(excinfo.value)
    assert _branch_head(repo, tid) == head_before
    assert _run(repo, "for-each-ref", "refs/cheesex/").strip() == ""
