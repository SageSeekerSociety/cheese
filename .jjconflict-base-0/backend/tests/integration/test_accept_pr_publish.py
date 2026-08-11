"""PR-based accept (#188 §5.1) — the accept path dispatches on the card's PR.

Functional, through the API: a card that rides a PR is accepted by merging that
PR (never the local merge); a merge refusal lands in the same conflict flow as
a local conflict; GitHub being down falls back to the local path; a PR-less
card never touches GitHub. GitHub itself is a fake client class — the tests
assert what the accept path DOES with it, not HTTP details (unit-tested
separately in tests/unit/test_github_pr.py).
"""

import asyncio
import uuid

import pytest

from app.domain.review.github_pr import GitHubPRError, GitHubPRMergeBlocked


def _auth(handle: str) -> dict[str, str]:
    """Accept requires the authenticated reviewer (accept-authorization)."""
    from app.core.tokens import mint_session_token

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


def _give_card_a_pr(client, card_id: str, number: int = 7) -> None:
    """Seed the card as pr_publish would have: it rides PR #<number>."""

    async def _do() -> None:
        from app.domain.review.repositories import AcceptCardRepository

        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.pr_number = number
            card.pr_url = f"https://github.com/acme/widgets/pull/{number}"
            await session.commit()

    asyncio.run(_do())


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"

    async def readonly_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"


class _FakeClient:
    """Stands in for GitHubPRClient; scripted per test via class attributes."""

    calls: list[tuple] = []
    view: dict | Exception = {}
    merge_error: Exception | None = None

    def __init__(self, owner: str, repo: str, tokens, **_):
        type(self).calls.append(("init", owner, repo))

    async def pr_view(self, number: int) -> dict:
        type(self).calls.append(("view", number))
        if isinstance(type(self).view, Exception):
            raise type(self).view
        return type(self).view

    async def merge_pr(self, number: int, *, title: str, message: str) -> dict:
        type(self).calls.append(("merge", number, title, message))
        if type(self).merge_error is not None:
            raise type(self).merge_error
        return {"merged": True, "sha": "deadbeef"}


@pytest.fixture
def pr_world(monkeypatch):
    """A world where the card's project has a GitHub upstream and the App is
    configured — with every workspace side effect recorded, not executed."""
    from app.domain.agent import github_app
    from app.domain.review import github_pr as github_pr_module
    from app.domain.review import services as review_services
    from app.domain.workspace import service as ws

    _FakeClient.calls = []
    _FakeClient.view = {
        "merged": False,
        "state": "open",
        "base": {"ref": "main"},
        "head": {"sha": "abc123"},
    }
    _FakeClient.merge_error = None

    recorded: dict[str, list] = {"pushes": [], "syncs": [], "local_merges": []}

    # #192: the accept path resolves the installation per-project, not globally.
    async def _fake_tokens_for_project(_project_id, _session):
        return _FakeTokens()

    monkeypatch.setattr(
        github_app, "github_app_tokens_for_project", _fake_tokens_for_project
    )
    monkeypatch.setattr(github_pr_module, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(
        ws,
        "push_topic_branch",
        lambda pid, tid, token: (
            recorded["pushes"].append((tid, token)) or f"topic/{tid.hex[:8]}"
        ),
    )
    monkeypatch.setattr(
        ws,
        "sync_upstream",
        lambda pid: recorded["syncs"].append(pid) or {"synced": True, "commits": 1},
    )

    def _local_merge(pid, tid):
        recorded["local_merges"].append(tid)
        return {"merged": False, "noop": True, "reason": "no topic branch"}

    monkeypatch.setattr(ws, "merge_topic", _local_merge)
    monkeypatch.setattr(ws, "prepare_conflict_resolution", lambda pid, tid: ["a.py"])
    _ = review_services  # imported for proximity; accept() resolves ws at call time
    return recorded


def test_pr_card_is_accepted_by_merging_the_pr(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=7)

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "PR #7" in card["note"]

    # The real PR was merged; the local merge never ran; main synced DOWN.
    merges = [c for c in _FakeClient.calls if c[0] == "merge"]
    assert len(merges) == 1
    assert merges[0][1] == 7
    assert "采纳 topic/" in merges[0][2]  # commit title keeps the platform shape
    assert "验收人：alice" in merges[0][3]
    assert pr_world["local_merges"] == []
    assert len(pr_world["pushes"]) == 1  # last-minute edits re-pushed pre-merge
    assert len(pr_world["syncs"]) == 1

    # 采纳即归档.
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_pr_merge_refusal_lands_in_the_conflict_flow(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=8)
    _FakeClient.merge_error = GitHubPRMergeBlocked("PR #8 is not mergeable")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "conflict"
    assert "PR #8" in card["note"]
    # Not archived — same contract as a local merge conflict; 芝士 goes to fix.
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"
    # The local base was synced so the materialized conflict matches GitHub's.
    assert len(pr_world["syncs"]) == 1

    # After the fix, retry succeeds (the branch is re-pushed and merged).
    _FakeClient.merge_error = None
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_github_down_falls_back_to_the_local_path(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=9)
    _FakeClient.view = GitHubPRError("GitHub unreachable")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    # Availability parity: the accept still completes, via the local path.
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert pr_world["local_merges"] != []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"
    # 可见性: 之前这个降级只有 logger.exception，卡片上完全看不出走过 PR
    # 路径又失败了——现在原因(哪个PR、GitHub报了什么)必须留在 note 上。
    assert "未走 PR 采纳" in card["note"]
    assert "PR #9" in card["note"]
    assert "GitHub unreachable" in card["note"]


def test_pr_closed_unmerged_falls_back_with_visible_reason(client, pr_world):
    """PR 在 GitHub 上被直接关闭但没合并（人手动关的，或别的自动化关的）：
    之前只有 logger.warning，卡片降级到本地合并后完全看不出"其实本来有个
    PR，只是被关掉了"——跟"这张卡从来没走过 PR 路径"外部观感一样。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=11)
    _FakeClient.view = {"merged": False, "state": "closed"}

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"  # degraded to the local merge path
    assert "未走 PR 采纳" in card["note"]
    assert "PR #11" in card["note"]
    assert "关闭" in card["note"]
    assert "未合并" in card["note"]
    # No merge/push attempted against a PR that's already closed.
    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
    assert pr_world["local_merges"] != []


def test_pr_conflict_sync_upstream_failure_visible_in_note(
    client, pr_world, monkeypatch
):
    """合并冲突后平台会同步上游 main 好让materialize出来的冲突匹配 GitHub
    的真实状态——这一步失败之前只有 logger.exception，冲突提示看起来跟
    正常冲突一模一样，没人知道冲突可能建立在陈旧的 base 上。"""
    from app.domain.workspace import service as ws

    def failing_sync(_project_id):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(ws, "sync_upstream", failing_sync)

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=12)
    _FakeClient.merge_error = GitHubPRMergeBlocked("PR #12 is not mergeable")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "conflict"
    assert "PR #12" in card["note"]
    assert "合并冲突" in card["note"]
    assert "同步上游失败" in card["note"]
    assert "network unreachable" in card["note"]
    # Same contract as any other merge conflict — topic stays active either way.
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_pr_already_merged_on_github_is_respected(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=10)
    _FakeClient.view = {"merged": True, "state": "closed"}

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "已在 GitHub 合并" in card["note"]
    # No merge attempt, no push — just bookkeeping + sync down.
    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
    assert pr_world["pushes"] == []
    assert len(pr_world["syncs"]) == 1


def test_prless_card_never_touches_github(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # no PR seeded

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert _FakeClient.calls == []  # GitHub never consulted
    assert pr_world["local_merges"] != []  # old path, unchanged
