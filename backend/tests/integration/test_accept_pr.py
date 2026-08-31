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

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.project.repositories import ProjectGitInstallationRepository
from app.domain.review import github_pr
from app.domain.workspace import service as ws
from tests.conftest import wait_work_idle
from tests.integration.conftest import room_text, session_auth_headers
from tests.machine_work import machine_commits


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/topics/{topic_id}").json()["data"]


def _cards_for_topic(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/accept-card").json()["data"]["data"]


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
        # Tier-2 (#468): per-sha check-run NAME sets, and update-branch capture.
        self.check_names_by_sha: dict[str, set[str]] = {}
        self.update_branch_calls: list[int] = []
        self.update_branch_result: bool = True
        # Which credential each Update-branch was made with. It matters on the
        # App lane the same way `check_state_tokens` does, in the opposite
        # direction: this call PUSHES a merge of main onto the PR branch, so the
        # read mint answers 403 and the poller retries forever.
        self.update_branch_tokens: list[str] = []
        # A GitHub failure to raise out of `check_state`, for the "the poll
        # cannot read GitHub at all" case.
        self.check_state_error: Exception | None = None
        # run id → 那次运行的 job 列表。默认（未登记的 run）给一个真的部署过的
        # job，因为绝大多数测试关心的不是这一层；「跳过了部署」和「挂在哪个
        # job 上」的用例自己登记。
        self.jobs_by_run_id: dict[int, list[github_pr.WorkflowJob]] = {}
        self.jobs_calls: list[int] = []
        self.opened: list[dict] = []
        self.merge_calls: list[dict] = []
        self.status_calls: list[int] = []
        self.head_sha_calls: list[int] = []
        # Which credential each check-runs read was made with. It matters on
        # the App lane: the write mint carries no `checks` permission, so a
        # read made with it is a 403 that the poller retries forever.
        self.check_state_tokens: list[str] = []
        # PR 回流 (review/pr_signals.py): number → 这个 PR 上的评审动静，
        # number → GitHub 的 `mergeable`（None = 它还没算完，不是「冲突」）。
        self.reviews_by_number: dict[int, list] = {}
        self.mergeable_by_number: dict[int, bool | None] = {}
        self.review_signal_calls: list[tuple[int, bool]] = []

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
            # The branch the PR is actually open on. Fed back to the poller so
            # a re-push goes to THIS PR's branch instead of one derived from
            # the topic id — the two lanes name it differently.
            head_ref=pr["head"],
            state=pr["state"],
            merged=pr["merged"],
            merge_commit_sha=pr["merge_commit_sha"],
            merged_at=pr["merged_at"],
            # 默认 None，和真 GitHub 在「还没算完」时给的一样 —— 一个默认 True 会
            # 让「冲突」这条路在所有别的用例里悄悄变成不可达。
            mergeable=self.mergeable_by_number.get(number),
            review_comment_count=sum(
                1
                for s in self.reviews_by_number.get(number, [])
                if s.kind == "comment"
            ),
        )

    def seed_pr(self, number: int, *, head: str, base: str = "main") -> str:
        """Register a PR this fake did not open itself — the App lane opens its
        PR through a different client (`GitHubPRClient.open_pr`), so the poller
        side has to be told the PR exists. Returns its head sha."""
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
        return head_sha

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
        self.check_state_tokens.append(token)
        if self.check_state_error is not None:
            raise self.check_state_error
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
                "token": token,
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

    async def review_signals(
        self, *, owner, repo, number, token, with_comments=True
    ) -> list:
        """PR 上的评审动静。默认没有 —— 绝大多数用例跟评审正交。

        `with_comments` 照实记下来（`review_signal_calls`），因为「PR 自己说没有
        行内评论时就别再问一次」是这条路上真正省下来的那次请求。
        """
        self.review_signal_calls.append((number, with_comments))
        signals = list(self.reviews_by_number.get(number, []))
        if not with_comments:
            signals = [s for s in signals if s.kind != "comment"]
        return signals

    async def check_run_names(self, *, owner, repo, ref, token) -> set[str]:
        # Default: everything required is present — existing tests exercise the
        # green/red/pending states, not the tier-2 absence valve (#468).
        if ref in self.check_names_by_sha:
            return set(self.check_names_by_sha[ref])
        return {"test", "guards", "lint", "e2e"}

    async def update_branch(self, *, owner, repo, number, token) -> bool:
        self.update_branch_calls.append(number)
        self.update_branch_tokens.append(token)
        if self.update_branch_result:
            # GitHub's Update branch MERGES base into head, so it creates a
            # commit and the PR's head moves. Modelling that is what makes the
            # rebase cap observable at all — see
            # test_accept_app_waits_for_ci.test_rebasing_stops_after_three_tries.
            pr = self.prs[number]
            pr["head_sha"] = f"{pr['head_sha']}-rebased"
        return self.update_branch_result

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
    incidental git plumbing unrelated to what's under test. The repush
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
    (via the same public ensure_repo/branch_for_tree helpers production code
    uses), not a value we made up in the test."""
    repo_path = ws.ensure_repo(project_id)
    branch = ws.branch_for_tree(topic_id)
    return subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _poll(client) -> dict:
    r = client.post("/admin/scheduler/poll-open-prs")
    assert r.status_code == 200
    return r.json()["data"]


def test_accept_with_token_opens_pr_topic_stays_active(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)

        r = client.post(
            f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
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


def test_poll_ci_green_merges_and_that_finishes_the_accept(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_sha_by_number[number] = "merge-sha-1"

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        # #206: merged IS the finish line. The card used to stay `pr_open` here
        # waiting for a deploy workflow, which is a per-project ops concept the
        # platform could not define — and which sometimes produced no run at all,
        # stranding the card forever.
        assert card["status"] == "accepted"
        assert card["pr_merged_at"] is not None
        delivered = _topic(client, tid)
        # 交付完成 ≠ 话题结束 (#442 decision 1).
        assert delivered["status"] == "active"
        assert delivered["accepted_at"] is not None
        assert fake.merge_calls[0]["number"] == number
        # Trailers ride the squash commit's BODY (2026-08-09 设计要点5)...
        assert "Reviewed-by: alice" in fake.merge_calls[0]["commit_message"]
        # ...and its title carries "(#N)", which GitHub only auto-appends to
        # the default title — an explicit commit_title replaces that default.
        # The subject is the card's own (递卡必带 --subject, review/services.py);
        # the `(#N)` is what this assertion is really about.
        assert (
            fake.merge_calls[0]["commit_title"]
            == f"chore(test): file an accept card (#{number})"
        )
    finally:
        _reset_client()


def test_poll_ci_failure_nudges_cheese_once(client, monkeypatch, stub_hooks):
    fake = _pr_ready(client, monkeypatch)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        accepted = client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")

        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
        # 平台提示统一契约: 房间里是一行 + 折叠的 `meta.detail`（`room_text` 把两半
        # 都算上），而**行动指引整段只进芝士的 prompt**，房间里根本不显示 —— 所以
        # 下面这四条断言的对象是 prompt，不是块。
        assert "pytest: 3 failed" in contents
        prompt = stub_hooks.last_prompt or ""
        # 2026-08-09 fix: 芝士's sandbox can't push to GitHub — the nudge must
        # not tell it to "推送新 commit", or it goes chasing an impossible
        # instruction (see docs/topics for the incident this caused).
        assert "推送新 commit" not in prompt
        assert "平台会自动把新提交同步到这个 PR" in prompt
        # CI失败要把日志送到芝士眼前: the nudge must also say how to read the
        # rest. Both halves matter — the token path was documented nowhere 芝士
        # can read, and `gh api repos/:owner/:repo/...` needs a repo name the
        # workspace (not a checkout of the repo) has no way to supply.
        assert "cheese gh-token" in prompt
        assert "repos/acme/widgets/actions/jobs/" in prompt
        nudge_count = contents.count("pytest: 3 failed")

        # Polling again with the SAME failing commit must not spam a second nudge.
        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
        assert contents.count("pytest: 3 failed") == nudge_count

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert _topic(client, tid)["status"] == "active"

        # 芝士 pushes a fix -> head sha moves -> a fresh failure on the NEW
        # commit must notify again (dedup is per-commit, not permanent).
        new_sha = fake.push_new_commit(number)
        fake.check_state_by_sha[new_sha] = ("failure", "pytest: 1 failed now")
        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
        assert "pytest: 1 failed now" in contents
    finally:
        _reset_client()


def test_repush_pushes_new_local_commit_and_updates_pr_head_sha(client, monkeypatch):
    """两阶段采纳 iterate loop (2026-08-09 fix): 芝士 fixing something in its
    workspace used to sit local forever — nothing ever pushed it to the PR
    branch (the platform's own `push_topic_branch_for_github_pr` was only
    ever called once, at PR-open time). This exercises the REAL local git
    plumbing that now detects and re-pushes it: the machine's own commit lands on
    the branch, and `ensure_repo`/`branch_for_tree` run for real against a real
    repo. Only the actual network hop to github.com is faked
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

        # 芝士 does real work on its machine before the card is even accepted.
        machine_commits(puid, tuid, {"work.txt": "first pass\n"})
        first_head = _real_git_head(puid, tuid)

        cid = _make_card(client, tid)
        accepted = client.post(
            f"/accept-cards/{cid}/accept",
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

        # 芝士 fixes something: it commits on its own machine and pushes the
        # branch back. Nothing on the platform made that commit — the poller
        # stopped committing on a timer, because every write it swept up moved
        # the PR and `cancel-in-progress` killed the CI run checking it.
        machine_commits(puid, tuid, {"work.txt": "fixed\n"})

        # Saying so is what puts it on the PR without waiting for the next tick.
        pushed = client.post(
            f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
        ).json()["data"]
        assert pushed["pushed"] is True
        assert len(push_calls) == 2
        second_head = push_calls[1]["head_sha"]
        assert second_head != first_head

        card = _cards_for_topic(client, tid)[0]
        assert card["pr_head_sha"] == second_head

        # Idempotent: polling again with no further local change must not
        # trigger a third push, and neither must asking again.
        _poll(client)
        assert len(push_calls) == 2
        again = client.post(
            f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
        ).json()["data"]
        assert again["pushed"] is False, "没有新东西可推时,再问一次不算错误"
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

        machine_commits(puid, tuid, {"work.txt": "first pass\n"})
        first_head = _real_git_head(puid, tuid)

        cid = _make_card(client, tid)
        accepted = client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        holder["pr_number"] = accepted["pr_number"]
        fake.prs[holder["pr_number"]]["head_sha"] = first_head

        # 芝士 fixes something and commits it, then the token goes bad before
        # the platform can re-push it (expired token / network hiccup / non-ff
        # — same degrade contract either way). The commit is the agent's own:
        # the poller reads the branch head, it does not move it.
        machine_commits(puid, tuid, {"work.txt": "fixed\n"})
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


def test_app_pr_mechanism_suppresses_the_personal_token_pr_on_accept(
    client, monkeypatch
):
    """采纳即合并 (#296) coexistence: when the App owns PR creation
    (`pr_publish.enabled()`), accepting a PR-less card must NOT open a competing
    personal-token PR. This is the guard that makes flipping `accept_via_pr` on
    safe: without it, the App publish and the accept-time personal-token path
    could both open a PR in the publish race window.

    What happens to the PR-less card instead CHANGED with the #328 regression
    fix: on a GitHub-bound project the accept now opens the App's own PR on
    the spot and merges it (see test_accept_pr_publish.py). Here the project
    is UNBOUND — no GitHub upstream — so the platform is its forge (#363) and
    the accept completes via the local merge (noop), labelled as such, still
    with zero personal-token PRs anywhere."""
    from app.domain.agent import github_app
    from app.domain.review import pr_publish
    from app.domain.review.services import AcceptService

    # A connected token AND a connected repo DO resolve — so the ONLY reason a
    # personal-token PR is not opened is the coexistence guard, not a missing
    # prerequisite.
    fake = _pr_ready(client, monkeypatch)
    try:
        monkeypatch.setattr(settings, "accept_via_pr", True)
        monkeypatch.setattr(settings, "github_app_id", 12345)
        monkeypatch.setattr(settings, "github_app_private_key_path", "/tmp/fake.pem")
        # The submit-side App publish is fire-and-forget; stub it so the test
        # doesn't spawn a real installation lookup. Its being enabled() is what
        # makes the App the owner of PR creation.
        monkeypatch.setattr(pr_publish, "dispatch", lambda *_a, **_k: None)
        # The binding check and any accept-time publish resolve App tokens
        # (the REAL resolver would trip over _pr_ready's minimal fake
        # installation); with tokens in hand the project still has no GitHub
        # upstream → unbound, platform-as-forge lane.
        _app_tokens = object()

        async def _fake_app_tokens(_project_id, _session):
            return _app_tokens

        monkeypatch.setattr(
            github_app, "github_app_tokens_for_project", _fake_app_tokens
        )
        monkeypatch.setattr(
            pr_publish, "github_app_tokens_for_project", _fake_app_tokens
        )

        opened_personal: list[dict] = []
        real_open = AcceptService._open_pr_for_accept

        async def spy_open(self, **kw):
            opened_personal.append(kw)
            return await real_open(self, **kw)

        monkeypatch.setattr(AcceptService, "_open_pr_for_accept", spy_open)

        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)  # born pending, no App PR recorded yet
        r = client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
        card = r.json()["data"]
        # No competing personal-token PR: neither the opener nor the fake GitHub
        # client was ever touched.
        assert opened_personal == []
        assert fake.opened == []
        # Unbound project → platform is the forge (#363): the local merge
        # (noop on an empty topic) IS the accept, labelled as such.
        assert card["status"] == "accepted"
        assert "本项目未接 GitHub" in card["note"]
        delivered = _topic(client, tid)
        # 交付完成 ≠ 话题结束 (#442 decision 1).
        assert delivered["status"] == "active"
        assert delivered["accepted_at"] is not None
    finally:
        _reset_client()


def test_remote_head_ff_from_local_reads_real_git_ancestry(client):
    """采纳即合并 (#296): the fast-forward pre-check reads REAL git ancestry, so
    it correctly refuses a push that could only be non-fast-forward. Two real
    snapshots give a genuine parent→child pair; the reversed direction is the
    946bf5de shape (remote ahead of local)."""
    from types import SimpleNamespace

    from app.domain.review.services import AcceptService

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    machine_commits(puid, tuid, {"a.txt": "1\n"})
    head1 = _real_git_head(puid, tuid)
    machine_commits(puid, tuid, {"a.txt": "2\n"})
    head2 = _real_git_head(puid, tuid)
    assert head1 != head2

    svc = AcceptService(SimpleNamespace())
    # remote sitting at the older head CAN fast-forward to the newer local head.
    assert svc._remote_head_ff_from_local(puid, head1, head2) is True
    # remote AHEAD of local (rewind/divergence) canNOT — the platform must not
    # force-push over it, and re-attempting the plain push is the 946bf5de loop.
    assert svc._remote_head_ff_from_local(puid, head2, head1) is False
    # remote commit not even present locally to compare → fail closed.
    assert svc._remote_head_ff_from_local(puid, "0" * 40, head2) is False


def test_repush_skips_a_doomed_non_fast_forward_and_says_so(client, monkeypatch):
    """采纳即合并 (#296): when the local topic branch has diverged from / fallen
    behind the PR branch, a plain push can only be rejected non-fast-forward.
    The poller must NOT attempt it every tick (card 946bf5de failed every ~70s)
    — it declines, says so once on the card, and never force-pushes over the
    commits already on the PR."""
    from app.domain.review.services import AcceptService

    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        pushes: list[dict] = []

        def spy_push(_pid, _tid, *, owner, repo, remote_branch, token):
            pushes.append({"remote_branch": remote_branch})
            return {"head_sha": "must-not-happen", "remote_branch": remote_branch}

        monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", spy_push)
        # 芝士's local head moved, but it diverged from the PR branch.
        monkeypatch.setattr(
            AcceptService,
            "_local_topic_branch_head",
            lambda _s, _p, _t: "moved-but-diverged",
        )
        monkeypatch.setattr(
            AcceptService, "_remote_head_ff_from_local", lambda *_a, **_k: False
        )
        # CI is red this tick too — the divergence note must win over a CI nudge,
        # since 芝士's fix never reached the PR (the red CI on record is stale).
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("failure", "lint 挂了")

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert pushes == []  # the doomed push was never attempted
        assert card["status"] == "pr_open"
        assert card["note_level"] == "error"
        assert card["note"].startswith("本地分支与 PR 分支已分叉")
        assert _topic(client, tid)["status"] == "active"

        # 60s polling: no spam, still no push on the next tick.
        note = card["note"]
        _poll(client)
        again = _cards_for_topic(client, tid)[0]
        assert pushes == []
        assert again["note"] == note
    finally:
        _reset_client()


def test_merged_pr_is_settled_without_re_pushing_a_moved_local_head(
    client, monkeypatch
):
    """采纳即合并 (#296) deliverable 4, stated literally: the poll checks
    merged/closed FIRST and收卡, so a merged PR is never re-pushed — even when
    the local head has moved since (which would otherwise trigger a re-push)."""
    from app.domain.review.services import AcceptService

    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        pushes: list[dict] = []

        def spy_push(_pid, _tid, *, owner, repo, remote_branch, token):
            pushes.append({"remote_branch": remote_branch})
            return {"head_sha": "must-not-happen", "remote_branch": remote_branch}

        monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", spy_push)
        monkeypatch.setattr(
            AcceptService,
            "_local_topic_branch_head",
            lambda _s, _p, _t: "moved-local-head",
        )
        # The PR was merged on GitHub between accept and this poll.
        fake.merge_externally(number, merge_commit_sha="merged-commit-sha")

        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert pushes == []  # merged-first guard returned before any re-push
        assert card["pr_merged_at"] is not None
        assert "人工合并" in card["note"]
        assert fake.merge_calls == []  # never tried to merge an already-merged PR
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
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["pr_number"] is None
    delivered = _topic(client, tid)
    # 交付完成 ≠ 话题结束 (#442 decision 1).
    assert delivered["status"] == "active"
    assert delivered["accepted_at"] is not None
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
            f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200
        card = r.json()["data"]

        assert card["status"] == "accepted"
        assert card["pr_number"] is None
        assert "未走 PR 采纳" in card["note"]
        assert "Base ref must be a branch" in card["note"]
        delivered = _topic(client, tid)
        # 交付完成 ≠ 话题结束 (#442 decision 1).
        assert delivered["status"] == "active"
        assert delivered["accepted_at"] is not None
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
        f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
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


def test_poll_merge_refusal_summons_cheese_once_per_reason(
    client, monkeypatch, stub_hooks
):
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
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = (
            "HTTP 405：Pull Request has merge conflicts"
        )

        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
        assert "Pull Request has merge conflicts" in contents
        # Must be actionable from inside the sandbox: 芝士 has no GitHub
        # credentials, so the same promise the CI nudge makes has to hold here.
        # 平台提示统一契约: 这句是**给芝士的指令**，只进 prompt，房间里不显示。
        assert "平台会自动把新提交同步到这个 PR" in (stub_hooks.last_prompt or "")
        first_count = contents.count("Pull Request has merge conflicts")
        assert first_count == 1

        # Same refusal next tick → no second summon.
        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
        assert contents.count("Pull Request has merge conflicts") == first_count

        # A DIFFERENT refusal is new information — summon again.
        fake.merge_blocked_by_number[number] = "HTTP 409：Head branch was modified"
        _poll(client)
        wait_work_idle()
        blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
        contents = room_text(blocks)
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
            f"/accept-cards/{cid}/accept",
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
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        number = accepted["pr_number"]
        head_sha = fake.prs[number]["head_sha"]
        fake.check_state_by_sha[head_sha] = ("failure", "lint 挂了")
        _poll(client)
        assert _cards_for_topic(client, tid)[0]["note_level"] == "error"

        fake.check_state_by_sha[head_sha] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = "HTTP 405：merge method disabled"
        _poll(client)

        # 合并被拒是新的停因，它要顶掉旧的检查失败，而不是排在它后面。
        refused = _cards_for_topic(client, tid)[0]
        assert "405" in refused["note"]
        assert "拒绝合并" in refused["note"]
        assert "lint 挂了" not in refused["note"]
    finally:
        _reset_client()


# --- PR 被外部（人工）处理掉的情况 (2026-08-10) ------------------------------
#
# 病灶：a PR merged by hand on GitHub was invisible to the poller, so its card
# sat at `pr_open` forever and never reached accepted (cards 1c7016e3 / #210
# and ceb1b9b9 / #211). These drive the poller through the same public
# endpoint every other test here uses; nothing inspects source.


def _accept_to_pr_open(client, monkeypatch) -> tuple[FakeGitHubPrClient, str, int]:
    """Common setup: a card accepted onto a real (fake-GitHub) PR."""
    fake = _pr_ready(client, monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    accepted = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    assert accepted["status"] == "pr_open"
    return fake, tid, accepted["pr_number"]


def test_poll_externally_merged_pr_finishes_the_accept(client, monkeypatch):
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
        # A human merging on GitHub is the same fact as the platform merging,
        # and since #206 that fact is the whole of what the accept waits for.
        assert card["status"] == "accepted"
        assert card["pr_merged_at"] is not None
        delivered = _topic(client, tid)
        # 交付完成 ≠ 话题结束 (#442 decision 1).
        assert delivered["status"] == "active"
        assert delivered["accepted_at"] is not None
        # The wording still has to say who merged it — an accept that reads as
        # if the platform did it hides that nobody here ran the checks.
        assert "人工合并" in card["note"]
        # The platform must NOT have tried to merge an already-merged PR.
        assert fake.merge_calls == []

        fake.workflow_state_by_sha["a34b8e12"] = ("success", "部署成功")
        _poll(client)

        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "accepted"
        topic = _topic(client, tid)
        assert topic["status"] == "active"
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
