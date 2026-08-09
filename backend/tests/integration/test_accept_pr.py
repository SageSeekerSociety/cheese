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


def _pr_ready(client, monkeypatch, *, handle: str = "alice") -> FakeGitHubPrClient:
    """Wires up: a usable connected GitHub token for `handle`, a project
    connected to a repo (#192), a fake branch push (no real git/network), and
    a fake GitHub API client. Returns the fake client for per-test state."""

    async def fake_token(_session, h, *, provider_id="github_app"):
        return "test-token" if h == handle else None

    async def fake_get_by_project(_self, _project_id):
        return _fake_installation()

    def fake_push(_project_id, _topic_id, *, owner, repo, remote_branch, token):
        return {"head_sha": f"sha-{remote_branch}-1", "remote_branch": remote_branch}

    def fake_base_branch(_project_id):
        return "main"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle", fake_token
    )
    monkeypatch.setattr(
        ProjectGitInstallationRepository, "get_by_project", fake_get_by_project
    )
    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", fake_push)
    monkeypatch.setattr(ws, "pr_base_branch", fake_base_branch)

    fake_client = FakeGitHubPrClient()
    github_pr.set_default_client(fake_client)
    return fake_client


def _reset_client():
    github_pr.set_default_client(None)


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
%%%%%%% diff from: mstkvkmw 36ec831c "采纳 topic/6196e41a → main"
\\\\\\\        to: ssssymsq e6cbcb5f "同步上游 upstream/main → main"
+"""PR-based accept (#188 §5.1) — the accept path dispatches on the card's PR.
+
+Functional, through the API: a card that rides a PR is accepted by merging that
+PR (never the local merge); a merge refusal lands in the same conflict flow as
+a local conflict; GitHub being down falls back to the local path; a PR-less
+card never touches GitHub. GitHub itself is a fake client class — the tests
+assert what the accept path DOES with it, not HTTP details (unit-tested
+separately in tests/unit/test_github_pr.py).
+"""
+
+import asyncio
+import uuid
+
+import pytest
+
+from app.domain.review.github_pr import GitHubPRError, GitHubPRMergeBlocked
+
+
+def _auth(handle: str) -> dict[str, str]:
+    """Accept requires the authenticated reviewer (accept-authorization)."""
+    from app.core.tokens import mint_session_token
+
+    return {
+        "Authorization": f"Bearer {mint_session_token(handle=handle, user_id=None)}"
+    }
+
+
+def _make_project(client) -> str:
+    r = client.post("/api/projects", json={"name": "P"})
+    assert r.status_code == 200
+    return r.json()["data"]["id"]
+
+
+def _make_topic(client, project_id: str) -> str:
+    r = client.post(
+        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
+    )
+    assert r.status_code == 200
+    return r.json()["data"]["id"]
+
+
+def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
+    r = client.post(
+        f"/api/topics/{topic_id}/accept-card",
+        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
+    )
+    assert r.status_code == 200
+    return r.json()["data"]["id"]
+
+
+def _give_card_a_pr(client, card_id: str, number: int = 7) -> None:
+    """Seed the card as pr_publish would have: it rides PR #<number>."""
+
+    async def _do() -> None:
+        from app.domain.review.repositories import AcceptCardRepository
+
+        async with client.test_factory() as session:
+            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
+            assert card is not None
+            card.pr_number = number
+            card.pr_url = f"https://github.com/acme/widgets/pull/{number}"
+            await session.commit()
+
+    asyncio.run(_do())
+
+
+class _FakeTokens:
+    async def write_token(self) -> tuple[str, str]:
+        return "ghs_write", "2099-01-01T00:00:00+00:00"
+
+    async def readonly_token(self) -> tuple[str, str]:
+        return "ghs_read", "2099-01-01T00:00:00+00:00"
+
+
+class _FakeClient:
+    """Stands in for GitHubPRClient; scripted per test via class attributes."""
+
+    calls: list[tuple] = []
+    view: dict | Exception = {}
+    merge_error: Exception | None = None
+
+    def __init__(self, owner: str, repo: str, tokens, **_):
+        type(self).calls.append(("init", owner, repo))
+
+    async def pr_view(self, number: int) -> dict:
+        type(self).calls.append(("view", number))
+        if isinstance(type(self).view, Exception):
+            raise type(self).view
+        return type(self).view
+
+    async def merge_pr(self, number: int, *, title: str, message: str) -> dict:
+        type(self).calls.append(("merge", number, title, message))
+        if type(self).merge_error is not None:
+            raise type(self).merge_error
+        return {"merged": True, "sha": "deadbeef"}
+
+
+@pytest.fixture
+def pr_world(monkeypatch):
+    """A world where the card's project has a GitHub upstream and the App is
+    configured — with every workspace side effect recorded, not executed."""
+    from app.domain.agent import github_app
+    from app.domain.review import github_pr as github_pr_module
+    from app.domain.review import services as review_services
+    from app.domain.workspace import service as ws
+
+    _FakeClient.calls = []
+    _FakeClient.view = {
+        "merged": False,
+        "state": "open",
+        "base": {"ref": "main"},
+        "head": {"sha": "abc123"},
+    }
+    _FakeClient.merge_error = None
+
+    recorded: dict[str, list] = {"pushes": [], "syncs": [], "local_merges": []}
+
+    # #192: the accept path resolves the installation per-project, not globally.
+    async def _fake_tokens_for_project(_project_id, _session):
+        return _FakeTokens()
+
+    monkeypatch.setattr(
+        github_app, "github_app_tokens_for_project", _fake_tokens_for_project
+    )
+    monkeypatch.setattr(github_pr_module, "GitHubPRClient", _FakeClient)
+    monkeypatch.setattr(
+        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
+    )
+    monkeypatch.setattr(
+        ws,
+        "push_topic_branch",
+        lambda pid, tid, token: (
+            recorded["pushes"].append((tid, token)) or f"topic/{tid.hex[:8]}"
+        ),
+    )
+    monkeypatch.setattr(
+        ws,
+        "sync_upstream",
+        lambda pid: recorded["syncs"].append(pid) or {"synced": True, "commits": 1},
+    )
+
+    def _local_merge(pid, tid):
+        recorded["local_merges"].append(tid)
+        return {"merged": False, "noop": True, "reason": "no topic branch"}
+
+    monkeypatch.setattr(ws, "merge_topic", _local_merge)
+    monkeypatch.setattr(ws, "prepare_conflict_resolution", lambda pid, tid: ["a.py"])
+    _ = review_services  # imported for proximity; accept() resolves ws at call time
+    return recorded
+
+
+def test_pr_card_is_accepted_by_merging_the_pr(client, pr_world):
+    pid = _make_project(client)
+    tid = _make_topic(client, pid)
+    cid = _make_card(client, tid)
+    _give_card_a_pr(client, cid, number=7)
+
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    card = r.json()["data"]
+    assert card["status"] == "accepted"
+    assert "PR #7" in card["note"]
+
+    # The real PR was merged; the local merge never ran; main synced DOWN.
+    merges = [c for c in _FakeClient.calls if c[0] == "merge"]
+    assert len(merges) == 1
+    assert merges[0][1] == 7
+    assert "采纳 topic/" in merges[0][2]  # commit title keeps the platform shape
+    assert "验收人：alice" in merges[0][3]
+    assert pr_world["local_merges"] == []
+    assert len(pr_world["pushes"]) == 1  # last-minute edits re-pushed pre-merge
+    assert len(pr_world["syncs"]) == 1
+
+    # 采纳即归档.
+    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"
+
+
+def test_pr_merge_refusal_lands_in_the_conflict_flow(client, pr_world):
+    pid = _make_project(client)
+    tid = _make_topic(client, pid)
+    cid = _make_card(client, tid)
+    _give_card_a_pr(client, cid, number=8)
+    _FakeClient.merge_error = GitHubPRMergeBlocked("PR #8 is not mergeable")
+
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    card = r.json()["data"]
+    assert card["status"] == "conflict"
+    assert "PR #8" in card["note"]
+    # Not archived — same contract as a local merge conflict; 芝士 goes to fix.
+    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"
+    # The local base was synced so the materialized conflict matches GitHub's.
+    assert len(pr_world["syncs"]) == 1
+
+    # After the fix, retry succeeds (the branch is re-pushed and merged).
+    _FakeClient.merge_error = None
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    assert r.json()["data"]["status"] == "accepted"
+    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"
+
+
+def test_github_down_falls_back_to_the_local_path(client, pr_world):
+    pid = _make_project(client)
+    tid = _make_topic(client, pid)
+    cid = _make_card(client, tid)
+    _give_card_a_pr(client, cid, number=9)
+    _FakeClient.view = GitHubPRError("GitHub unreachable")
+
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    # Availability parity: the accept still completes, via the local path.
+    assert r.json()["data"]["status"] == "accepted"
+    assert pr_world["local_merges"] != []
+    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"
+
+
+def test_pr_already_merged_on_github_is_respected(client, pr_world):
+    pid = _make_project(client)
+    tid = _make_topic(client, pid)
+    cid = _make_card(client, tid)
+    _give_card_a_pr(client, cid, number=10)
+    _FakeClient.view = {"merged": True, "state": "closed"}
+
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    card = r.json()["data"]
+    assert card["status"] == "accepted"
+    assert "已在 GitHub 合并" in card["note"]
+    # No merge attempt, no push — just bookkeeping + sync down.
+    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
+    assert pr_world["pushes"] == []
+    assert len(pr_world["syncs"]) == 1
+
+
+def test_prless_card_never_touches_github(client, pr_world):
+    pid = _make_project(client)
+    tid = _make_topic(client, pid)
+    cid = _make_card(client, tid)  # no PR seeded
+
+    r = client.post(
+        f"/api/accept-cards/{cid}/accept",
+        json={"decided_by": "alice"},
+        headers=_auth("alice"),
+    )
+    assert r.status_code == 200
+    assert r.json()["data"]["status"] == "accepted"
+    assert _FakeClient.calls == []  # GitHub never consulted
+    assert pr_world["local_merges"] != []  # old path, unchanged
>>>>>>> conflict 1 of 1 ends
