"""Integration tests for 两阶段采纳 (PR迭代式, 2026-08-09).

Exercises both accept() branches:
- degrade path (no connected token / no connected repo): behaves exactly like
  the pre-existing direct-merge accept (see test_accept.py) — covered there,
  not duplicated here.
- PR path: accept() pushes a branch + opens a PR (a fake GitHubPrClient
  stands in for real GitHub) and the topic stays ACTIVE; the scheduler's
  poller (SchedulerService.poll_open_prs, exposed as
  POST /api/admin/scheduler/poll-open-prs for tests) advances the card
  through PR-CI-green -> merge -> deploy-workflow-green -> archive. Archive
  timing is the point of this feature (2026-08-09 拍板: merge alone is not
  enough), so every stage explicitly asserts the topic is still "active"
  until the very last step.
"""

import subprocess
import uuid as _uuid
from datetime import UTC, datetime

import httpx

from app.core.errors import ValidationError
from app.domain.project.repositories import ProjectGitInstallationRepository
from app.domain.review import github_pr
from app.domain.workspace import service as ws
from tests.conftest import wait_turns_idle
from tests.integration.conftest import session_auth_headers


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/api/topics/{topic_id}").json()["data"]


def _cards_for_topic(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]


class FakeGitHubPrClient:
    """两阶段采纳 (PR迭代式) test double — no real GitHub calls. State is plain
    dicts keyed by PR number / commit sha so a test can move it forward
    between polls."""

    def __init__(self) -> None:
        self._next_number = 100
        self.prs: dict[int, dict] = {}
        self.check_state_by_sha: dict[str, tuple[str, str]] = {}
        self.merge_sha_by_number: dict[int, str | None] = {}
        # number → GitHub's refusal reason (405/409); wins over a sha.
        self.merge_blocked_by_number: dict[int, str] = {}
        self.workflow_state_by_sha: dict[str, tuple[str, str]] = {}
        # 人类授权动作前移: sha → the PR diff at that sha, as [(status, path)].
        # None models GitHub's oversized-compare response (no `files` key).
        # Unset shas answer with an empty diff, which is what every test that
        # never pushes a second commit wants (授权时的 head == 现在的 head, so
        # the poller doesn't even ask).
        self.files_by_sha: dict[str, list[tuple[str, str]] | None] = {}
        self.compare_calls: list[tuple[str, str]] = []
        self.opened: list[dict] = []
        self.merge_calls: list[dict] = []
        self.status_calls: list[int] = []
        self.head_sha_calls: list[int] = []

    async def open_pull_request(
        self, *, owner, repo, head, base, title, body, token
    ) -> github_pr.PullRequest:
        self._next_number += 1
        number = self._next_number
        head_sha = f"sha-{head}-1"
        self.prs[number] = {
            "head": head,
            "base": base,
            "head_sha": head_sha,
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "merged_at": None,
        }
        self.opened.append(
            {
                "owner": owner,
                "repo": repo,
                "head": head,
                "base": base,
                "title": title,
                "body": body,
                "token": token,
            }
        )
        return github_pr.PullRequest(
            number=number,
            url=f"https://github.com/{owner}/{repo}/pull/{number}",
            head_sha=head_sha,
        )

    async def pull_request_head_sha(self, *, owner, repo, number, token) -> str:
        self.head_sha_calls.append(number)
        return self.prs[number]["head_sha"]

    async def pull_request_status(
        self, *, owner, repo, number, token
    ) -> github_pr.PullRequestStatus:
        pr = self.prs[number]
        self.status_calls.append(number)
        return github_pr.PullRequestStatus(
            head_sha=pr["head_sha"],
            state=pr["state"],
            merged=pr["merged"],
            merge_commit_sha=pr["merge_commit_sha"],
            merged_at=pr["merged_at"],
        )

    def merge_externally(
        self,
        number: int,
        *,
        merge_commit_sha: str | None = "human-merge-sha",
        merged_at: datetime | None = None,
    ) -> None:
        """Test helper: someone merged this PR on GitHub themselves — the
        platform never called merge. GitHub reports a merged PR as
        `state: closed` + `merged: true`."""
        self.prs[number].update(
            state="closed",
            merged=True,
            merge_commit_sha=merge_commit_sha,
            merged_at=merged_at,
        )

    def close_unmerged(self, number: int) -> None:
        """Test helper: someone closed the PR on GitHub without merging it."""
        self.prs[number].update(state="closed", merged=False)

    def push_new_commit(self, number: int) -> str:
        """Test helper: simulate 芝士 pushing a fix — moves the PR's head."""
        new_sha = self.prs[number]["head_sha"] + "x"
        self.prs[number]["head_sha"] = new_sha
        return new_sha

    async def check_state(self, *, owner, repo, ref, token) -> tuple[str, str]:
        return self.check_state_by_sha.get(ref, ("pending", "还没跑"))

    async def compare_files(
        self, *, owner, repo, base, head, token
    ) -> list[tuple[str, str]] | None:
        self.compare_calls.append((base, head))
        return self.files_by_sha.get(head, [])

    async def merge_pull_request(
        self, *, owner, repo, number, token, commit_title=None, commit_message=None
    ) -> github_pr.MergeResult:
        self.merge_calls.append(
            {
                "number": number,
                "commit_title": commit_title,
                "commit_message": commit_message,
            }
        )
        blocked = self.merge_blocked_by_number.get(number)
        if blocked is not None:
            return github_pr.MergeResult(blocked_reason=blocked)
        return github_pr.MergeResult(
            sha=self.merge_sha_by_number.get(number, "merge-sha-default")
        )

    async def workflow_run_state(
        self, *, owner, repo, workflow_file, head_sha, token
    ) -> tuple[str, str]:
        return self.workflow_state_by_sha.get(head_sha, ("pending", "还没触发"))


def _fake_installation(repo: str = "acme/widgets"):
    class _Installation:
        pass

    inst = _Installation()
    inst.repo = repo
    return inst


def _pr_ready(
    client, monkeypatch, *, handle: str = "alice", patch_local_head: bool = True
) -> FakeGitHubPrClient:
    """Wires up: a usable connected GitHub token for `handle`, a project
    connected to a repo (#192), a fake branch push (no real git/network), and
    a fake GitHub API client. Returns the fake client for per-test state.

    `patch_local_head=True` (the default) also fakes AcceptService's local
    branch-head lookup to always report "nothing to push" — every test in
    this file EXCEPT the repush ones below is testing something orthogonal
    to the repush mechanism (CI states, merge, deploy, nudging) and none of
    them ever create a real workspace commit, so a real lookup would just be
    incidental git/jj plumbing unrelated to what's under test. The repush
    tests pass `patch_local_head=False` to exercise the real thing."""

    async def fake_token(_session, h, *, provider_id="github_app"):
        return ("test-token", None) if h == handle else (None, "not_connected")

    async def fake_get_by_project(_self, _project_id):
        return _fake_installation()

    def fake_push(_project_id, _topic_id, *, owner, repo, remote_branch, token):
        return {"head_sha": f"sha-{remote_branch}-1", "remote_branch": remote_branch}

    def fake_base_branch(_project_id):
        return "main"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle_with_reason",
        fake_token,
    )
    monkeypatch.setattr(
        ProjectGitInstallationRepository, "get_by_project", fake_get_by_project
    )
    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", fake_push)
    monkeypatch.setattr(ws, "pr_base_branch", fake_base_branch)
    if patch_local_head:
        from app.domain.review.services import AcceptService

        def fake_local_head(_self, _project_id, _topic_id):
            return None

        monkeypatch.setattr(AcceptService, "_local_topic_branch_head", fake_local_head)

    fake_client = FakeGitHubPrClient()
    github_pr.set_default_client(fake_client)
    return fake_client


def _reset_client():
    github_pr.set_default_client(None)


def _real_git_head(project_id: _uuid.UUID, topic_id: _uuid.UUID) -> str:
    """The REAL current head of a topic's local git branch — used by the
    repush tests below to prove the platform actually reads real git state
    (via the same public ensure_repo/branch_for_topic helpers production code
    uses), not a value we made up in the test."""
    repo_path = ws.ensure_repo(project_id)
    branch = ws.branch_for_topic(topic_id)
    return subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _poll(client) -> dict:
    r = client.post("/api/admin/scheduler/poll-open-prs")
    assert r.status_code == 200
    return r.json()["data"]


def test_accept_with_token_opens_pr_topic_stays_active(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)

        r = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200
        card = r.json()["data"]
        assert card["status"] == "pr_open"
        assert card["pr_number"] is not None
        assert card["pr_repo"] == "acme/widgets"
        assert card["pr_url"]

        # 决策3: 点了采纳到 PR 真正合并之前，话题必须还是 active，容器不停.
        assert _topic(client, tid)["status"] == "active"

        opened = fake.opened[0]
        assert opened["owner"] == "acme"
        assert opened["repo"] == "widgets"
        # 设计要点5: PR 描述里标清芝士代表谁 (Reviewed-by = 批准人).
        assert "Reviewed-by: alice" in opened["body"]
    finally:
        _reset_client()


def test_accept_pr_open_failure_degrades_with_github_call_failed_reason(
    client, monkeypatch
):
    """Token connected AND repo connected — prerequisites are fully met —
    but the push/PR-open call itself fails (expired token by the time it
    actually hits GitHub, network hiccup, etc). Must still degrade to the
    direct-merge path (拍板 decision 2) AND the card must say THIS is what
    happened, distinct from "never had a token" or "repo not connected"."""
    _pr_ready(client, monkeypatch)

    def failing_push(_project_id, _topic_id, *, owner, repo, remote_branch, token):
        raise ValidationError("git push failed: 403 rejected")

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", failing_push)

    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)

        r = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200
        card = r.json()["data"]
        assert card["status"] == "accepted"  # degraded to the local path
        assert card["pr_number"] is None
        assert "未走 PR 采纳" in card["note"]
        assert "GitHub 侧调用失败" in card["note"]
        assert "403 rejected" in card["note"]  # the real cause is legible
        assert "token" not in card["note"].lower()
    finally:
        _reset_client()


def test_poll_ci_pending_no_change(client, monkeypatch):
    _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        # No check_state configured -> defaults to "pending".
        _poll(client)
        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["pr_merged_at"] is None
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_poll_token_gone_pauses_with_visible_reason(client, monkeypatch):
    """轮询时批准人的 GitHub token 没了(过期/撤销/账号解绑)——之前只有
    logger.warning，卡片永远停在原地不动，外部观感跟"一切正常只是 CI 还没跑
    完"完全一样。现在卡片必须说清楚原因（不能泄漏 token 本身），且重复轮询同一
    个持续失败不能刷屏。"""
    _pr_ready(client, monkeypatch)
    holder: dict = {"reason": None}

    async def fake_token(_session, h, *, provider_id="github_app"):
        if h == "alice" and holder["reason"] is None:
            return "test-token", None
        return None, holder["reason"] or "not_connected"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle_with_reason",
        fake_token,
    )
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )

        # Token goes bad after the PR is already open (key rotated / expired
        # with no refresh — same observable shape either way).
        holder["reason"] = "undecryptable"

        result = _poll(client)
        assert result["errors"] == []  # transient — not a hard poller error
        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"  # not permanently stuck/failed
        assert "轮询暂停" in card["note"]
        assert "无法解密" in card["note"]
        assert "test-token" not in card["note"]

        # Polling is every 60s — repeated failures must not spam the note.
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"] == card["note"]
    finally:
        _reset_client()


def test_poll_ci_green_merges_but_topic_stays_active_until_deploy(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_sha_by_number[number] = "merge-sha-1"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"  # 还没真正完成
        assert card["pr_merged_at"] is not None
        # 归档时机测试的核心：PR merge 成功了，但部署还没完成——topic 必须还是 active.
        assert _topic(client, tid)["status"] == "active"
        assert fake.merge_calls[0]["number"] == number
        # Trailers ride the squash commit's BODY (2026-08-09 设计要点5)...
        assert "Reviewed-by: alice" in fake.merge_calls[0]["commit_message"]
        # ...and its title carries "(#N)", which GitHub only auto-appends to
        # the default title — an explicit commit_title replaces that default.
        assert fake.merge_calls[0]["commit_title"] == f"采纳 做一个东西 (#{number})"
    finally:
        _reset_client()


def test_poll_deploy_success_finally_archives(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_sha_by_number[number] = "merge-sha-1"
        _poll(client)
        assert _topic(client, tid)["status"] == "active"

        # Deploy workflow still pending -> still active.
        _poll(client)
        assert _topic(client, tid)["status"] == "active"

        fake.workflow_state_by_sha["merge-sha-1"] = ("success", "部署成功")
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        topic = _topic(client, tid)
        assert topic["status"] == "archived"
        assert topic["accepted_by"] == "alice"
    finally:
        _reset_client()


def test_poll_ci_failure_nudges_cheese_once(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")

        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert "pytest: 3 failed" in contents
        # 2026-08-09 fix: 芝士's sandbox can't push to GitHub — the nudge must
        # not tell it to "推送新 commit", or it goes chasing an impossible
        # instruction (see docs/topics for the incident this caused).
        assert "推送新 commit" not in contents
        assert "平台会自动把新提交同步到这个 PR" in contents
        # CI失败要把日志送到芝士眼前: the nudge must also say how to read the
        # rest. Both halves matter — the token path was documented nowhere 芝士
        # can read, and `gh api repos/:owner/:repo/...` needs a repo name the
        # workspace (not a checkout of the repo) has no way to supply.
        assert "cheese gh-token" in contents
        assert "repos/acme/widgets/actions/jobs/" in contents
        nudge_count = contents.count("pytest: 3 failed")

        # Polling again with the SAME failing commit must not spam a second nudge.
        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert contents.count("pytest: 3 failed") == nudge_count

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert _topic(client, tid)["status"] == "active"

        # 芝士 pushes a fix -> head sha moves -> a fresh failure on the NEW
        # commit must notify again (dedup is per-commit, not permanent).
        new_sha = fake.push_new_commit(number)
        fake.check_state_by_sha[new_sha] = ("failure", "pytest: 1 failed now")
        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert "pytest: 1 failed now" in contents
    finally:
        _reset_client()


def test_poll_deploy_failure_keeps_topic_active_no_retry(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_sha_by_number[number] = "merge-sha-1"
        _poll(client)  # merges

        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "build-did-not-produce-images",
        )
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        # 2026-08-09 拍板 (wangchangxin 建议默认值，评估后采纳): 不归档、不自动重试.
        assert card["status"] == "pr_open"
        assert card["note"].startswith("❌")
        assert _topic(client, tid)["status"] == "active"

        # A second poll (no retry configured) must not merge/archive on its own.
        _poll(client)
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_repush_pushes_new_local_commit_and_updates_pr_head_sha(client, monkeypatch):
    """两阶段采纳 iterate loop (2026-08-09 fix): 芝士 fixing something in its
    workspace used to sit local forever — nothing ever pushed it to the PR
    branch (the platform's own `push_topic_branch_for_github_pr` was only
    ever called once, at PR-open time). This exercises the REAL local git
    plumbing that now detects and re-pushes it: `ensure_repo`/
    `branch_for_topic`/`snapshot_worktree` run for real against a real
    jj-colocated repo. Only the actual network hop to github.com is faked
    (the sandbox has no route there — see docs/topics for that constraint);
    the fake still computes the pushed head_sha via a real `git rev-parse`,
    exactly like the production function does. Also proves the platform does
    NOT push on every poll tick when nothing local has changed."""
    fake = _pr_ready(client, monkeypatch, patch_local_head=False)
    push_calls: list[dict] = []
    holder: dict = {"pr_number": None}

    def real_head_push(project_id, topic_id, *, owner, repo, remote_branch, token):
        head_sha = _real_git_head(project_id, topic_id)
        push_calls.append({"remote_branch": remote_branch, "head_sha": head_sha})
        if holder["pr_number"] is not None:
            # A real push moves what GitHub reports as the PR's head too.
            fake.prs[holder["pr_number"]]["head_sha"] = head_sha
        return {"head_sha": head_sha, "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", real_head_push)

    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

        # 芝士 does real work in its workspace before the card is even accepted.
        wt = ws.topic_worktree(puid, tuid)
        (wt / "work.txt").write_text("first pass\n")
        ws.snapshot_worktree(puid, tuid)
        first_head = _real_git_head(puid, tuid)

        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        assert accepted["status"] == "pr_open"
        assert accepted["pr_head_sha"] == first_head
        assert len(push_calls) == 1
        holder["pr_number"] = accepted["pr_number"]
        # Reconcile the fake GitHub's reported head with what we actually
        # pushed (open_pull_request's own synthetic head_sha predates knowing
        # what push_topic_branch_for_github_pr really pushed).
        fake.prs[holder["pr_number"]]["head_sha"] = first_head

        # CI still pending, nothing changed locally -> polling must NOT push again.
        _poll(client)
        assert len(push_calls) == 1
        assert _cards_for_topic(client, tid)[0]["pr_head_sha"] == first_head

        # 芝士 fixes something — a plain edit in the same workspace, no special
        # "please push" step (that's the whole point of the fix: it can't).
        (wt / "work.txt").write_text("fixed\n")

        _poll(client)
        assert len(push_calls) == 2
        second_head = push_calls[1]["head_sha"]
        assert second_head != first_head

        card = _cards_for_topic(client, tid)[0]
        assert card["pr_head_sha"] == second_head

        # Idempotent: polling again with no further local change must not
        # trigger a third push.
        _poll(client)
        assert len(push_calls) == 2
    finally:
        _reset_client()


def test_repush_failure_degrades_without_failing_the_card(client, monkeypatch):
    """push 失败(token 失效/网络/非快进)必须降级得体面: 不能让一次 push 失败
    把整张卡搞成永久失败, 也不能静默吞掉。This proves both — a failing repush
    logs and leaves the card in `pr_open` with no crash and no entry in the
    poller's `errors` list (the same "transient GitHub hiccup" treatment as
    `GitHubPrError`), AND that it isn't PERMANENT: once the transient failure
    clears, the very next poll succeeds and catches up."""
    fake = _pr_ready(client, monkeypatch, patch_local_head=False)
    holder: dict = {"pr_number": None, "fail": False}

    def flaky_push(project_id, topic_id, *, owner, repo, remote_branch, token):
        head_sha = _real_git_head(project_id, topic_id)
        if holder["fail"]:
            raise ValidationError("git push failed: 401 Bad credentials")
        if holder["pr_number"] is not None:
            fake.prs[holder["pr_number"]]["head_sha"] = head_sha
        return {"head_sha": head_sha, "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", flaky_push)

    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

        wt = ws.topic_worktree(puid, tuid)
        (wt / "work.txt").write_text("first pass\n")
        ws.snapshot_worktree(puid, tuid)
        first_head = _real_git_head(puid, tuid)

        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        holder["pr_number"] = accepted["pr_number"]
        fake.prs[holder["pr_number"]]["head_sha"] = first_head

        # 芝士 fixes something, then the token goes bad before the platform
        # can re-push it (expired token / network hiccup / non-ff — same
        # degrade contract either way).
        (wt / "work.txt").write_text("fixed\n")
        holder["fail"] = True

        result = _poll(client)
        assert result["errors"] == []  # not surfaced as a hard poller error
        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"  # not permanently failed
        assert card["pr_head_sha"] == first_head  # unchanged — push never landed
        assert _topic(client, tid)["status"] == "active"
        # 可见性 (this card's whole point): 芝士 has no host SSH to read
        # logger.warning — the failure and its cause must be on the card.
        assert "重推失败" in card["note"]
        assert "401 Bad credentials" in card["note"]

        # Retrying while still failing must stay just as graceful (no crash,
        # no permanent-failure state) — not just tolerate one failure. And,
        # since polling is every 60s, repeated failures must NOT spam the
        # note with duplicate copies of the same message.
        result = _poll(client)
        assert result["errors"] == []
        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].count("重推失败") == 1

        # Once the transient issue clears, the very next poll catches up.
        holder["fail"] = False
        _poll(client)
        card = _cards_for_topic(client, tid)[0]
        assert card["pr_head_sha"] == _real_git_head(puid, tuid)
        assert card["pr_head_sha"] != first_head
        assert "重推失败" not in card["note"]  # cleared once the push succeeds
    finally:
        _reset_client()


def test_accept_without_token_or_repo_degrades_to_direct_merge(client):
    """No monkeypatching at all here: default test env has no connected
    token/repo, so this must behave EXACTLY like the pre-existing direct
    merge accept (test_accept.py's happy path) — the point of 拍板 decision 2."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["pr_number"] is None
    assert _topic(client, tid)["status"] == "archived"
    # 降级原因可见性: WHY it skipped the PR path must be legible on the card,
    # not indistinguishable from "never eligible in the first place" — and
    # must never contain a token or ciphertext.
    assert "未走 PR 采纳" in card["note"]
    assert "批准人未连接 GitHub 账号" in card["note"]
    assert "token" not in card["note"].lower()


# ---- 422「PR 已存在」→ 认领，不降级 (2026-08-10) --------------------------
#
# These two drive the REAL HttpxGitHubPrClient over a mocked HTTP transport
# instead of FakeGitHubPrClient: the whole behaviour under test is how the
# client reads GitHub's 422 body, which a fake client would define away. The
# incident: accepting `0bbc3403` twice raced two PR-open calls, the loser read
# 422 already-exists as "mechanism unavailable", degraded to a local merge +
# direct push to main, and left PR #234 open forever on code that had already
# landed — plus two contradictory room messages.


def _real_client_over(handler) -> None:
    from app.domain.review.github_pr import HttpxGitHubPrClient

    github_pr.set_default_client(
        HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    )


def _pushed_sha(topic_id: str) -> str:
    """What `_pr_ready`'s fake push reports as the branch head — the value the
    already-open PR's head must line up with."""
    return f"sha-{github_pr.pr_branch_name(_uuid.UUID(topic_id))}-1"


def test_accept_claims_the_pr_that_already_exists_on_the_branch(client, monkeypatch):
    """422 already-exists → adopt PR #234 and stay on the PR path: card fields
    match the real PR, status is pr_open, topic stays active. No degrade, so no
    orphan."""
    _pr_ready(client, monkeypatch)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [
                        {
                            "resource": "PullRequest",
                            "code": "custom",
                            "message": (
                                "A pull request already exists for "
                                "acme:cheesex/0bbc3403."
                            ),
                        }
                    ],
                },
            )
        branch = request.url.params["head"].split(":", 1)[1]
        return httpx.Response(
            200,
            json=[
                {
                    "number": 234,
                    "html_url": "https://github.com/acme/widgets/pull/234",
                    "head": {"ref": branch, "sha": f"sha-{branch}-1"},
                    "base": {"ref": "main"},
                }
            ],
        )

    _real_client_over(handler)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)

        r = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200
        card = r.json()["data"]

        assert card["status"] == "pr_open"
        assert card["pr_number"] == 234
        assert card["pr_url"] == "https://github.com/acme/widgets/pull/234"
        assert card["pr_repo"] == "acme/widgets"
        # Same head the existing PR reports — the branch was (re)pushed just
        # before the claim, so the card and GitHub agree on what's being tested.
        assert card["pr_head_sha"] == _pushed_sha(tid)
        assert card["pr_merged_at"] is None
        assert "未走 PR 采纳" not in (card["note"] or "")
        assert "已认领" in card["note"]

        assert _topic(client, tid)["status"] == "active"
        assert seen == [
            "POST /repos/acme/widgets/pulls",
            "GET /repos/acme/widgets/pulls",
        ]
    finally:
        _reset_client()


def test_accept_still_degrades_on_a_422_that_is_not_already_exists(client, monkeypatch):
    """The other half: a genuine validation failure must keep degrading to the
    direct-merge path exactly as before, and must never go looking for a PR to
    adopt."""
    _pr_ready(client, monkeypatch)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        assert request.method == "POST", "must not list PRs for a non-existence 422"
        return httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequest",
                        "field": "base",
                        "code": "invalid",
                        "message": "Base ref must be a branch",
                    }
                ],
            },
        )

    _real_client_over(handler)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)

        r = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200
        card = r.json()["data"]

        assert card["status"] == "accepted"
        assert card["pr_number"] is None
        assert "未走 PR 采纳" in card["note"]
        assert "Base ref must be a branch" in card["note"]
        assert _topic(client, tid)["status"] == "archived"
        assert seen == ["POST"]
    finally:
        _reset_client()


def test_poll_open_prs_ignores_non_pr_open_cards(client, monkeypatch):
    """A plain (degrade-path) accepted card must not be touched by the poller
    — regression guard for list_pr_open_on_active_topics filtering correctly."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    result = _poll(client)
    assert result["cards_checked"] == 0
    assert result["errors"] == []


def test_poll_merge_refused_puts_the_reason_on_the_card(client, monkeypatch):
    """A 405 used to vanish: the card sat at pr_open with an empty note while
    the poller retried forever. Outside, that looked identical to a healthy PR
    still waiting on CI — which is how the squash-only bug hid for half a day."""
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = (
            "HTTP 405：Merge commits are not allowed on this repository"
        )

        result = _poll(client)
        assert result["errors"] == []  # retryable — not a hard poller error

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["pr_merged_at"] is None
        assert "405" in card["note"]
        assert "Merge commits are not allowed" in card["note"]
        assert _topic(client, tid)["status"] == "active"

        # Polling is every 60s — an unchanging reason must not rewrite the note.
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"] == card["note"]
        assert len(fake.merge_calls) == 2  # …but it does keep retrying the merge
    finally:
        _reset_client()


def test_poll_merge_refusal_summons_cheese_once_per_reason(client, monkeypatch):
    """A note nobody is looking at is not a notification (2026-08-11): a PR the
    platform can't merge — typically merge conflicts, which 芝士 can fix in its
    own workspace — must wake 芝士 up, or the card sits at pr_open forever
    (真实案例: PR #242). The 60s poll means it must wake it exactly once per
    reason."""
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = (
            "HTTP 405：Pull Request has merge conflicts"
        )

        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert "Pull Request has merge conflicts" in contents
        # Must be actionable from inside the sandbox: 芝士 has no GitHub
        # credentials, so the same promise the CI nudge makes has to hold here.
        assert "平台会自动把新提交同步到这个 PR" in contents
        first_count = contents.count("Pull Request has merge conflicts")
        assert first_count == 1

        # Same refusal next tick → no second summon.
        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert contents.count("Pull Request has merge conflicts") == first_count

        # A DIFFERENT refusal is new information — summon again.
        fake.merge_blocked_by_number[number] = "HTTP 409：Head branch was modified"
        _poll(client)
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert "Head branch was modified" in contents

        assert _cards_for_topic(client, tid)[0]["status"] == "pr_open"
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_poll_merge_refusal_reason_updates_when_it_changes(client, monkeypatch):
    """Dedup must not freeze the FIRST reason forever: a 405 that becomes a 409
    is new information."""
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = "HTTP 405：merge method disabled"
        _poll(client)

        fake.merge_blocked_by_number[number] = "HTTP 409：Head branch was modified"
        _poll(client)

        note = _cards_for_topic(client, tid)[0]["note"]
        assert "409" in note
        assert "Head branch was modified" in note
    finally:
        _reset_client()


def test_poll_merge_refusal_replaces_a_stale_ci_failure_note(client, monkeypatch):
    """The ⚠️ CI note describes checks that have since turned green — the merge
    refusal is the current truth and must take the note over.

    (`_note_merge_blocked` also refuses to overwrite a `❌ 部署失败` note. That
    one is unreachable by construction — a deploy note only exists after the PR
    merged, and a merged card never re-enters the merge path — so it is a guard,
    not a scenario this test can drive.)"""
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/api/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("failure", "lint 挂了")
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"].startswith("⚠️")

        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = "HTTP 405：merge method disabled"
        _poll(client)

        note = _cards_for_topic(client, tid)[0]["note"]
        assert "405" in note
        assert not note.startswith("⚠️")
    finally:
        _reset_client()


# --- PR 被外部（人工）处理掉的情况 (2026-08-10) ------------------------------
#
# 病灶：a PR merged by hand on GitHub was invisible to the poller, so its card
# sat at `pr_open` forever and the topic never archived (cards 1c7016e3 / #210
# and ceb1b9b9 / #211). These drive the poller through the same public
# endpoint every other test here uses; nothing inspects source.


def _accept_to_pr_open(client, monkeypatch) -> tuple[FakeGitHubPrClient, str, int]:
    """Common setup: a card accepted onto a real (fake-GitHub) PR."""
    fake = _pr_ready(client, monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    accepted = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    assert accepted["status"] == "pr_open"
    return fake, tid, accepted["pr_number"]


def test_poll_externally_merged_pr_settles_into_the_deploy_stage(client, monkeypatch):
    """Someone merged the PR on GitHub themselves. The card must book it like
    our own merge — merged-at recorded, head moved to the MERGE COMMIT — and
    then archive on the deploy that merge triggered.

    The PR's checks are left RED on purpose: #210 was human-merged while an
    auto-review was still failing, and a red gate makes the poller return
    before it ever calls merge. Detecting the merge only from the merge call's
    405 would leave exactly this card stuck."""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("failure", "lint 挂了")
        merged_at = datetime(2026, 8, 9, 22, 3, 59, tzinfo=UTC)
        fake.merge_externally(number, merge_commit_sha="a34b8e12", merged_at=merged_at)

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["pr_merged_at"] is not None  # stage 2 now, not stage 1
        assert card["status"] == "pr_open"  # merge alone never archives
        assert _topic(client, tid)["status"] == "active"
        assert "人工合并" in card["note"]
        # The platform must NOT have tried to merge an already-merged PR.
        assert fake.merge_calls == []

        # Proof the card is tracking the merge commit and not the branch head:
        # the deploy gate is keyed by sha, so only a run on `a34b8e12` archives.
        fake.workflow_state_by_sha[fake.prs[number]["head_sha"]] = (
            "success",
            "分支 head 上的部署——不该被采信",
        )
        _poll(client)
        assert _topic(client, tid)["status"] == "active"

        fake.workflow_state_by_sha["a34b8e12"] = ("success", "部署成功")
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        topic = _topic(client, tid)
        assert topic["status"] == "archived"
        assert topic["accepted_by"] == "alice"
    finally:
        _reset_client()


def test_poll_externally_merged_pr_uses_githubs_merged_at(client, monkeypatch):
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.merge_externally(
            number, merged_at=datetime(2026, 8, 9, 22, 3, 59, tzinfo=UTC)
        )
        _poll(client)
        merged_at = _cards_for_topic(client, tid)[0]["pr_merged_at"]
        assert merged_at is not None
        assert "2026-08-09T22:03:59" in merged_at
    finally:
        _reset_client()


def test_poll_externally_merged_without_a_merge_sha_does_not_settle(
    client, monkeypatch
):
    """`merged: true` but no merge-commit sha: the stage-2 deploy gate is keyed
    BY that sha, so settling on a guess means waiting for a deploy run that can
    never exist. Stay in stage 1, say why, retry next tick."""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.merge_externally(number, merge_commit_sha=None)

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["pr_merged_at"] is None
        assert card["status"] == "pr_open"
        assert "合并提交 sha" in card["note"]
        assert _topic(client, tid)["status"] == "active"

        # It recovers by itself once GitHub reports the sha.
        fake.merge_externally(number, merge_commit_sha="late-sha")
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["pr_merged_at"] is not None
        fake.workflow_state_by_sha["late-sha"] = ("success", "部署成功")
        _poll(client)
        assert _topic(client, tid)["status"] == "archived"
    finally:
        _reset_client()


def test_poll_merge_blocked_still_only_notes_and_never_settles(client, monkeypatch):
    """The other side of the same coin: GitHub genuinely REFUSING the merge
    (405/409) must not be mistaken for "already merged" — no merged-at, no
    stage 2, no archive, just the reason on the card."""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = (
            "HTTP 405：Merge commits are not allowed on this repository"
        )

        _poll(client)
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["pr_merged_at"] is None
        assert card["status"] == "pr_open"
        assert "GitHub 拒绝合并" in card["note"]
        assert "405" in card["note"]
        assert _topic(client, tid)["status"] == "active"
        assert len(fake.merge_calls) == 2  # kept retrying, as before
    finally:
        _reset_client()


def test_poll_pr_closed_unmerged_says_so_and_stops_merging(client, monkeypatch):
    """Closed WITHOUT merging is a human saying "not this". The platform must
    not merge it anyway, must not archive, and must say what happened instead
    of the misleading "checks green but GitHub refused"."""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.close_unmerged(number)

        result = _poll(client)
        assert result["errors"] == []

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["pr_merged_at"] is None
        assert "关闭" in card["note"] and "没有合并" in card["note"]
        assert _topic(client, tid)["status"] == "active"
        assert fake.merge_calls == []

        # 60s polling: the note is stated once, not rewritten every tick.
        note = card["note"]
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"] == note
        assert fake.merge_calls == []

        # Reopened on GitHub -> the poller picks up where it left off.
        fake.prs[number].update(state="open")
        fake.merge_sha_by_number[number] = "merge-sha-after-reopen"
        _poll(client)
        assert len(fake.merge_calls) == 1
        assert _cards_for_topic(client, tid)[0]["pr_merged_at"] is not None
    finally:
        _reset_client()


def test_poll_steady_state_costs_no_extra_pr_read(client, monkeypatch):
    """The merged-check reuses the PR read the poller already did every tick —
    a quiet card must not double its GitHub API calls."""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        _poll(client)
        _poll(client)
        assert fake.status_calls == [number, number]
        assert fake.head_sha_calls == []  # nothing pushed -> no second read
    finally:
        _reset_client()
