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
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings
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


def _deployed_job(name: str = "deploy") -> github_pr.WorkflowJob:
    """一次**真的部署过**的 job：步骤全成功，没有一步被跳过。"""
    return github_pr.WorkflowJob(
        name=name,
        conclusion="success",
        steps=[
            ("Check out the built commit", "success"),
            ("Docker deploy this commit", "success"),
        ],
    )


def _docs_only_job() -> github_pr.WorkflowJob:
    """`deploy-dev.yml` 碰到 docs-only 提交时的样子：job 报 success，但登录和
    部署两步是 skipped —— 盒子上什么都没变。"""
    return github_pr.WorkflowJob(
        name="deploy",
        conclusion="success",
        steps=[
            ("Check out the built commit", "success"),
            ("Skip docs-only commits", "success"),
            ("Log in to ghcr", "skipped"),
            ("Docker deploy this commit", "skipped"),
        ],
    )


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
        # 被顶替判定 (2026-08-11): the deploy workflow's run history (newest
        # first, like GitHub's) and commit ancestry as compare reports it,
        # keyed (base, head) → status. Unset pairs answer None ("GitHub gave
        # nothing usable"), which must never read as "contained".
        self.workflow_runs: list[github_pr.WorkflowRun] = []
        self.compare_status_by_pair: dict[tuple[str, str], str] = {}
        self.compare_status_calls: list[tuple[str, str]] = []
        # run id → 那次运行的 job 列表。默认（未登记的 run）给一个真的部署过的
        # job，因为绝大多数测试关心的不是这一层；「跳过了部署」和「挂在哪个
        # job 上」的用例自己登记。
        self.jobs_by_run_id: dict[int, list[github_pr.WorkflowJob]] = {}
        self.jobs_calls: list[int] = []
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

    async def recent_workflow_runs(
        self, *, owner, repo, workflow_file, token, limit: int = 30
    ) -> list[github_pr.WorkflowRun]:
        return self.workflow_runs[:limit]

    async def compare_status(self, *, owner, repo, base, head, token) -> str | None:
        self.compare_status_calls.append((base, head))
        return self.compare_status_by_pair.get((base, head))

    async def workflow_run_jobs(
        self, *, owner, repo, run_id, token
    ) -> list[github_pr.WorkflowJob]:
        self.jobs_calls.append(run_id)
        return self.jobs_by_run_id.get(run_id, [_deployed_job()])


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


def _merged_awaiting_deploy(client, monkeypatch) -> tuple[FakeGitHubPrClient, str]:
    """Drive a card to exactly the state the 被顶替 tests below care about:
    PR merged as `merge-sha-1`, topic still active, waiting on that commit's
    deploy run. Returns (fake client, topic id)."""
    fake = _pr_ready(client, monkeypatch)
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
    fake.merge_sha_by_number[number] = "merge-sha-1"
    _poll(client)  # merges; the card now waits on merge-sha-1's deploy
    assert _topic(client, tid)["status"] == "active"
    return fake, tid


def _deploy_run(
    sha: str,
    *,
    conclusion: str,
    minutes: int,
    status: str = "completed",
    run_id: int = 900,
) -> github_pr.WorkflowRun:
    """A deploy run `minutes` after "now" (i.e. after the merge the helper
    above just made) — negative means before it."""
    return github_pr.WorkflowRun(
        head_sha=sha,
        status=status,
        conclusion=conclusion,
        created_at=datetime.now(UTC) + timedelta(minutes=minutes),
        url=f"https://github.com/acme/widgets/actions/runs/{run_id}",
        branch="main",
        id=run_id,
    )


def test_poll_deploy_cancelled_but_superseded_by_later_success_archives(
    client, monkeypatch
):
    """2026-08-11, PR #251 的真实事故：这张卡自己那次部署是 `cancelled`（被后一次
    部署顶替，concurrency 组的正常行为），平台却判成「需要人」，话题白白搁浅 4 小时
    ——代码其实早就上线了。更晚的那次成功部署包含这个合并提交，就该正常归档。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：cancelled",
        )
        fake.workflow_runs = [_deploy_run("8470ac05", conclusion="success", minutes=5)]
        # base = 那次成功部署的 commit, head = 我们的合并提交 → behind = 它包含我们.
        fake.compare_status_by_pair[("8470ac05", "merge-sha-1")] = "behind"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        topic = _topic(client, tid)
        assert topic["status"] == "archived"
        assert topic["accepted_by"] == "alice"
        assert fake.compare_status_calls == [("8470ac05", "merge-sha-1")]
        # 归档理由必须写清楚：不是「本次部署成功了」，而是被更晚的成功部署带上线。
        assert not card["note"].startswith("❌")
        assert "cancelled" in card["note"]
        assert "8470ac05" in card["note"]
    finally:
        _reset_client()


def test_poll_deploy_cancelled_without_any_later_success_still_asks_a_human(
    client, monkeypatch
):
    """现状不回退：没有任何更晚的成功部署时，仍然保持 active 并把判断交给人。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：cancelled",
        )
        # 部署历史里只有这次被取消的、和一次也失败了的 —— 一次成功都没有。
        fake.workflow_runs = [
            _deploy_run("merge-sha-1", conclusion="cancelled", minutes=1),
            _deploy_run("older-sha", conclusion="failure", minutes=-30),
        ]

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].startswith("❌")
        assert _topic(client, tid)["status"] == "active"
        # 一次成功的都没有 → 根本不必去问 GitHub 包含关系.
        assert fake.compare_status_calls == []
    finally:
        _reset_client()


def test_poll_deploy_with_no_run_at_all_archives_once_something_carries_it(
    client, monkeypatch
):
    """2026-08-11 实测的主症状，也是最危险的一种：`deploy-dev.yml` 的并发组名是
    固定字符串，合并一密集，后来的 `workflow_run` 触发会被并发组吞掉，**连 run 都
    不会被创建**（482ca022e / 611e43f02 按 sha 查 100 条终态全是 0）。按 head_sha
    精确匹配的老逻辑对这种卡永远是 pending —— 不是「还在等」，是死等。

    祖先关系是主判据，不是 cancelled 的补丁：它一视同仁地覆盖这一种。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        # 这个 sha 一条 run 都没有 —— 状态永远停在 pending。
        assert "merge-sha-1" not in fake.workflow_state_by_sha
        fake.workflow_runs = [
            _deploy_run("45b6169a", conclusion="success", minutes=5, run_id=910)
        ]
        fake.compare_status_by_pair[("45b6169a", "merge-sha-1")] = "behind"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        assert _topic(client, tid)["status"] == "archived"
        assert "45b6169a" in card["note"]
    finally:
        _reset_client()


def test_poll_deploy_pending_stays_quiet_inside_the_grace_window(client, monkeypatch):
    """刚合并、部署还没跑完，是完全正常的：不写备注、不发通知、更不归档。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert not card["note"].startswith("⏳")
        assert _topic(client, tid)["status"] == "active"
    finally:
        _reset_client()


def test_poll_deploy_stalled_past_the_grace_says_so_once(client, monkeypatch):
    """主判据也定不了案（既没上线、也没有结论）时仍然保持 active——但不再默默
    等着。默默等正是这个话题要治的病：#267 那样躺了三个多小时没人知道。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        monkeypatch.setattr(settings, "accept_deploy_stale_after_minutes", 0)

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].startswith("⏳")
        assert _topic(client, tid)["status"] == "active"
        stalled_note = card["note"]

        # 60 秒一轮，这条不能每轮重写、每轮再通知一遍。
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note"] == stalled_note
    finally:
        _reset_client()


def test_poll_commit_without_its_own_image_archives_when_something_carried_it(
    client, monkeypatch
):
    """**一个 commit 不需要有自己的镜像才算上线。** 这张卡自己的 build 403 推不动
    镜像（deploy 的守卫 job 如实报了 no images were pushed），但后来一个包含它、
    并且真的部署过的提交把它的源码送上了盒子——它就是上线了。

    「镜像在不在」这条判据被提过两次又撤回（2026-08-11）：它只是「代码上没上线」
    的一个坏代理。按它判，#267/#270/#274 会永远等一个自己的镜像，而它们三个都已经
    是 16:11 那次成功部署 45b6169a4 的祖先。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：failure",
        )
        fake.workflow_runs = [
            _deploy_run("45b6169a", conclusion="success", minutes=5, run_id=930),
            _deploy_run("merge-sha-1", conclusion="failure", minutes=1, run_id=931),
        ]
        fake.compare_status_by_pair[("45b6169a", "merge-sha-1")] = "behind"
        # 我们自己那次：build 没产出镜像，守卫 job 红着。
        fake.jobs_by_run_id[931] = [
            github_pr.WorkflowJob(
                name="build-did-not-produce-images",
                conclusion="failure",
                steps=[("Say why nothing was deployed", "failure")],
            ),
        ]

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        assert _topic(client, tid)["status"] == "archived"
        assert "45b6169a" in card["note"]
    finally:
        _reset_client()


def test_poll_deploy_later_success_that_skipped_the_deploy_does_not_count(
    client, monkeypatch
):
    """成功 ≠ 真的部署过。`deploy-dev.yml` 对 docs-only 提交会跳过登录和部署两步，
    job 照样报 success —— 盒子上什么都没变。这样一次「成功部署」顶替掉我们那次，
    代码并没有上线，绝不能当成归档的依据。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：cancelled",
        )
        fake.workflow_runs = [
            _deploy_run("docs-only-sha", conclusion="success", minutes=5, run_id=801)
        ]
        # 包含关系是成立的 —— 只是那次运行根本没部署。
        fake.compare_status_by_pair[("docs-only-sha", "merge-sha-1")] = "behind"
        fake.jobs_by_run_id[801] = [_docs_only_job()]

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].startswith("❌")
        assert _topic(client, tid)["status"] == "active"
        # 确实去 job 那一层看过了，不是靠运行级 conclusion 下的结论.
        assert 801 in fake.jobs_calls
    finally:
        _reset_client()


def test_poll_deploy_failure_says_which_job_failed(client, monkeypatch):
    """2026-08-11 的另一半：build 403 没推成镜像，deploy 的守卫 job
    `build-did-not-produce-images` 报错，盒子上跑的**确实还是旧代码**。运行级的
    tail 只会说「失败：failure」，两种情况一个样 —— 把 GitHub 自己的 job 名带上，
    人一眼就能分清是「部署跑了但挂了」还是「压根没产出镜像」。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：failure",
        )
        fake.workflow_runs = [
            _deploy_run("merge-sha-1", conclusion="failure", minutes=1, run_id=701)
        ]
        fake.jobs_by_run_id[701] = [
            github_pr.WorkflowJob(
                name="build-did-not-produce-images",
                conclusion="failure",
                steps=[("Say why nothing was deployed", "failure")],
            ),
            github_pr.WorkflowJob(name="deploy", conclusion="skipped", steps=[]),
        ]

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        # 没上线就是没上线：不归档，保持 active，交给人。
        assert card["status"] == "pr_open"
        assert _topic(client, tid)["status"] == "active"
        assert "build-did-not-produce-images" in card["note"]
    finally:
        _reset_client()


def test_poll_deploy_later_success_that_does_not_contain_the_commit_does_not_count(
    client, monkeypatch
):
    """更晚 + 成功还不够，必须**包含**这个提交：compare 说 diverged（比如那次部署
    跑在另一条线上）时，代码并没有上线，闸门不满足。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：cancelled",
        )
        fake.workflow_runs = [
            _deploy_run("other-line", conclusion="success", minutes=5)
        ]
        fake.compare_status_by_pair[("other-line", "merge-sha-1")] = "diverged"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].startswith("❌")
        assert _topic(client, tid)["status"] == "active"
        assert fake.compare_status_calls == [("other-line", "merge-sha-1")]
    finally:
        _reset_client()


def test_poll_deploy_earlier_success_never_counts(client, monkeypatch):
    """边界：只认更晚的成功部署。早于这次合并的部署即便报 behind 也不算数
    ——它跑的时候这个提交还不存在，不可能把它带上线。"""
    fake, tid = _merged_awaiting_deploy(client, monkeypatch)
    try:
        fake.workflow_state_by_sha["merge-sha-1"] = (
            "failure",
            "部署 workflow 失败：cancelled",
        )
        fake.workflow_runs = [
            _deploy_run("stale-sha", conclusion="success", minutes=-60)
        ]
        fake.compare_status_by_pair[("stale-sha", "merge-sha-1")] = "behind"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["note"].startswith("❌")
        assert _topic(client, tid)["status"] == "active"
        assert fake.compare_status_calls == []
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
