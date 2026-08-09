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

from app.core.errors import ValidationError
from app.core.tokens import mint_session_token
from app.domain.project.repositories import ProjectGitInstallationRepository
from app.domain.review import github_pr
from app.domain.workspace import service as ws
from tests.conftest import wait_turns_idle


def _auth(handle: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_session_token(handle=handle, user_id=None)}"
    }


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
        self.workflow_state_by_sha: dict[str, tuple[str, str]] = {}
        self.opened: list[dict] = []
        self.merge_calls: list[dict] = []

    async def open_pull_request(
        self, *, owner, repo, head, base, title, body, token
    ) -> github_pr.PullRequest:
        self._next_number += 1
        number = self._next_number
        head_sha = f"sha-{head}-1"
        self.prs[number] = {"head": head, "base": base, "head_sha": head_sha}
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
        return self.prs[number]["head_sha"]

    def push_new_commit(self, number: int) -> str:
        """Test helper: simulate 芝士 pushing a fix — moves the PR's head."""
        new_sha = self.prs[number]["head_sha"] + "x"
        self.prs[number]["head_sha"] = new_sha
        return new_sha

    async def check_state(self, *, owner, repo, ref, token) -> tuple[str, str]:
        return self.check_state_by_sha.get(ref, ("pending", "还没跑"))

    async def merge_pull_request(
        self, *, owner, repo, number, token, commit_message=None
    ) -> str | None:
        self.merge_calls.append({"number": number, "commit_message": commit_message})
        return self.merge_sha_by_number.get(number, "merge-sha-default")

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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
        )
        # No check_state configured -> defaults to "pending".
        _poll(client)
        card = _cards_for_topic(client, tid)[0]
        assert card["status"] == "pr_open"
        assert card["pr_merged_at"] is None
        assert _topic(client, tid)["status"] == "active"
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
            headers=_auth("alice"),
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
        assert "Reviewed-by: alice" in fake.merge_calls[0]["commit_message"]
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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
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
            headers=_auth("alice"),
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

        # Retrying while still failing must stay just as graceful (no crash,
        # no permanent-failure state) — not just tolerate one failure.
        result = _poll(client)
        assert result["errors"] == []
        assert _cards_for_topic(client, tid)[0]["status"] == "pr_open"

        # Once the transient issue clears, the very next poll catches up.
        holder["fail"] = False
        _poll(client)
        card = _cards_for_topic(client, tid)[0]
        assert card["pr_head_sha"] == _real_git_head(puid, tuid)
        assert card["pr_head_sha"] != first_head
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
        headers=_auth("alice"),
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


def test_poll_open_prs_ignores_non_pr_open_cards(client, monkeypatch):
    """A plain (degrade-path) accepted card must not be touched by the poller
    — regression guard for list_by_status filtering correctly."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    result = _poll(client)
    assert result["cards_checked"] == 0
    assert result["errors"] == []
