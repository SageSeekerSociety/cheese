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
    open_pr_number: int = 21
    checks: list[dict] | Exception = []

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

    async def open_pr(self, *, head: str, base: str, title: str, body: str) -> dict:
        type(self).calls.append(("open_pr", head, base, title))
        number = type(self).open_pr_number
        return {
            "number": number,
            "html_url": f"https://github.com/acme/widgets/pull/{number}",
        }

    async def check_runs(self, ref: str) -> list[dict]:
        type(self).calls.append(("check_runs", ref))
        if isinstance(type(self).checks, Exception):
            raise type(self).checks
        return type(self).checks


def _check(
    name: str = "test", status: str = "completed", conclusion: str | None = "success"
) -> dict:
    """One simplified check-run, the shape GitHubPRClient.check_runs returns."""
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "url": f"https://github.com/acme/widgets/runs/{name}",
    }


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
    _FakeClient.checks = [_check()]  # green CI unless a test scripts otherwise

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
        headers=session_auth_headers("alice"),
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
        headers=session_auth_headers("alice"),
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
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_github_down_falls_back_to_the_local_path(client, pr_world):
    """App 机制关着的世界（enabled() False — 没配 App 或 .env 关掉）：可用性
    契约保持 #328 之前的样子，GitHub 不可达时降级到本地合并、⚠️ 留痕。绑定
    GitHub 的项目（App 世界）走的是相反的契约——见下面 bound_project 系列。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=9)
    _FakeClient.view = GitHubPRError("GitHub unreachable")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
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
    """App 机制关着的世界：PR 在 GitHub 上被直接关闭但没合并（人手动关的，
    或别的自动化关的）——降级到本地合并时原因必须留在卡上，否则跟"这张卡
    从来没走过 PR 路径"外部观感一样。绑定 GitHub 的项目（App 世界）不再
    降级——见 test_bound_project_closed_pr_stops_the_accept。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=11)
    _FakeClient.view = {"merged": False, "state": "closed"}

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
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
        headers=session_auth_headers("alice"),
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
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "已在 GitHub 合并" in card["note"]
    # No merge attempt, no push — just bookkeeping + sync down.
    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
    assert pr_world["pushes"] == []
    assert len(pr_world["syncs"]) == 1


def test_pr_checks_endpoint_mirrors_forge_check_runs(client, monkeypatch):
    """采纳即合并 (#296) deliverable 2: the card's green comes from the FORGE,
    not a platform gate. `/pr-checks` reads the PR's live state + check-run
    conclusions for the card's PR via the App's checks:read token, at the PR's
    real head sha."""
    from app.api.routes import accept as accept_routes
    from app.domain.workspace import service as ws

    class _Tokens:
        async def readonly_token(self) -> tuple[str, str]:
            return "ghs_read", "2099-01-01T00:00:00+00:00"

        async def write_token(self) -> tuple[str, str]:
            return "ghs_write", "2099-01-01T00:00:00+00:00"

    class _Client:
        checked_ref: str | None = None

        def __init__(self, owner: str, repo: str, tokens, **_):
            pass

        async def pr_view(self, number: int) -> dict:
            return {
                "merged": False,
                "state": "open",
                "mergeable": True,
                "head": {"sha": "abc123"},
            }

        async def check_runs(self, ref: str) -> list[dict]:
            type(self).checked_ref = ref
            return [
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": "success",
                    "url": "https://github.com/acme/widgets/runs/1",
                }
            ]

    async def _tokens_for_project(_project_id, _session):
        return _Tokens()

    monkeypatch.setattr(
        accept_routes, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(accept_routes, "GitHubPRClient", _Client)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=7)

    r = client.get(f"/api/topics/{tid}/pr-checks")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is True
    assert data["pr_number"] == 7
    assert data["state"] == "open"
    assert data["mergeable"] is True
    assert data["checks"] == [
        {
            "name": "test",
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.com/acme/widgets/runs/1",
        }
    ]
    # The checks were read at the PR's real head, not the branch name.
    assert _Client.checked_ref == "abc123"


def test_prless_card_never_touches_github(client, pr_world):
    """App 机制关着（默认测试世界）：无 PR 卡照旧走本地合并，不碰 GitHub。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # no PR seeded

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert _FakeClient.calls == []  # GitHub never consulted
    assert pr_world["local_merges"] != []  # old path, unchanged


# ---- 存量无 PR 卡的采纳 (#296 stage 1 生产回归修复) --------------------------
#
# #328 上线后 dev 的 main 收到了没有对应 PR 的采纳 merge commit 直推
# （c33cfabf、8f9b9d94）：这些卡在 accept_via_pr 部署之前就 pending，身上没有
# App 开的 PR，共存守卫又跳过了个人 token 路径，于是掉进本地合并直推。下面的
# 测试钉住修复后的行为：采纳现场补开 App PR 并合并那个 PR；开不出来就停下亮
# 警告，绝不静默直推。


def _enable_app_pr(monkeypatch) -> None:
    """The world AFTER the accept_via_pr deploy: flag on, App configured, and
    pr_publish's own GitHub seams faked. Cards in these tests are created
    BEFORE this runs — exactly the production incident's shape (pending cards
    from before the deploy have no App PR, and no publish was dispatched for
    them at filing time)."""
    from pathlib import Path

    from app.core.config import settings
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    monkeypatch.setattr(settings, "accept_via_pr", True)
    monkeypatch.setattr(settings, "github_app_id", 12345)
    monkeypatch.setattr(settings, "github_app_private_key_path", "/tmp/fake-app.pem")

    async def _fake_tokens_for_project(_project_id, _session):
        return _FakeTokens()

    # pr_publish binds these names at module import — patch them there (the
    # pr_world fixture patches the github_app/github_pr modules, which covers
    # only the accept side's lazy imports).
    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _fake_tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: Path("."))
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")


def test_legacy_prless_card_gets_its_pr_opened_at_accept(client, pr_world, monkeypatch):
    """存量无 PR 卡采纳 = 现场补开 App PR + 合并那个 PR。main 不再收到无 PR 的
    直推。这条同时覆盖改 .github/workflows/ 的卡的成功侧：App 自 2026-08-12 起
    持有 workflows:write，推送在这个 seam 上与普通卡无异，mock GitHub 接受。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # filed while the App path was still off
    _enable_app_pr(monkeypatch)  # the deploy lands after the card was filed

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["pr_number"] == 21
    assert "PR #21" in card["note"]

    # The PR was opened, then merged — and the local merge NEVER ran.
    opens = [c for c in _FakeClient.calls if c[0] == "open_pr"]
    assert len(opens) == 1
    merges = [c for c in _FakeClient.calls if c[0] == "merge"]
    assert len(merges) == 1
    assert merges[0][1] == 21
    assert pr_world["local_merges"] == []
    # Two pushes: the publish's own, then _accept_via_pr's pre-merge re-push.
    assert len(pr_world["pushes"]) == 2

    # 采纳即归档.
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_pr_open_failure_stops_the_accept_and_lands_on_the_card(
    client, pr_world, monkeypatch
):
    """开 PR 失败（这里：推分支被拒，含 workflows 权限拒绝这种老理由）→ 采纳
    停下（422），失败原因亮在卡片上，卡保持 pending，main 一个直推都收不到。
    人处理后重试采纳，同一张卡走 PR 路走通。"""
    from app.core.errors import ValidationError
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _enable_app_pr(monkeypatch)

    def _rejected_push(pid_, tid_, token):
        raise ValidationError(
            "git push failed: ! [remote rejected] topic/abcd -> topic/abcd "
            "(refusing to allow a GitHub App to create or update workflow "
            "`.github/workflows/build.yml` without `workflows` permission)"
        )

    monkeypatch.setattr(ws, "push_topic_branch", _rejected_push)

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert "无法为这张卡开 PR" in r.json()["message"]

    # The failure is ON THE CARD (persisted despite the accept's rollback),
    # the card is still pending (retryable), and nothing was direct-pushed.
    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"
    assert card["note"].startswith("⚠️ 采纳未完成：无法为这张卡开 PR")
    assert "refusing to allow" in card["note"]
    assert "ℹ️" not in card["note"]  # no calm "known limitation" wording
    assert pr_world["local_merges"] == []
    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"

    # A human looked, the cause is gone (permission granted / network back) —
    # the SAME card now accepts through its PR.
    monkeypatch.setattr(
        ws,
        "push_topic_branch",
        lambda pid_, tid_, token: (
            pr_world["pushes"].append((tid_, token)) or f"topic/{tid_.hex[:8]}"
        ),
    )
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "PR #21" in card["note"]
    assert pr_world["local_merges"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_discussion_topic_on_bound_project_accepts_without_forge_label(
    client, pr_world, monkeypatch
):
    """绑定了 GitHub 的项目里的讨论型话题：没有分支、没有可进 PR 的改动——
    本地合并 no-op 完成采纳，什么都没绕过，也不该戴「未接 GitHub」的标。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _enable_app_pr(monkeypatch)
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid_, tid_: False)

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "未接 GitHub" not in (card["note"] or "")
    assert [c for c in _FakeClient.calls if c[0] in ("open_pr", "merge")] == []
    assert pr_world["local_merges"] != []  # noop merge — nothing bypassed
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


@pytest.mark.parametrize("missing", ["upstream", "installation"])
def test_unbound_project_local_merge_is_legitimate_and_labelled(
    client, pr_world, monkeypatch, missing
):
    """未接 GitHub 的项目 (#363)：平台自己就是 forge，local merge 是唯一、
    正当的采纳语义——不是降级、不拦人。但这件事要写在卡上（ℹ️ 不是 ⚠️），
    让它和「该走 PR 却没走」的卡一眼可分。"""
    from app.domain.agent import github_app
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _enable_app_pr(monkeypatch)
    if missing == "upstream":
        monkeypatch.setattr(ws, "get_upstream", lambda pid_: "/srv/repos/widgets")
    else:

        async def _no_tokens(_pid, _session):
            return None

        monkeypatch.setattr(github_app, "github_app_tokens_for_project", _no_tokens)

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["note"].startswith("ℹ️ 本项目未接 GitHub")
    assert "⚠️" not in card["note"]
    assert [c for c in _FakeClient.calls if c[0] in ("open_pr", "merge")] == []
    assert pr_world["local_merges"] != []  # the only accept such a project has
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_bound_project_github_down_stops_the_accept(client, pr_world, monkeypatch):
    """绑定 GitHub 的项目 (#363)：GitHub 不可达时采纳停下（可重试），永不落
    local merge——「可用性不回退」在绑定项目上让位给「不绕过 PR 和 CI」。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=17)
    _enable_app_pr(monkeypatch)
    _FakeClient.view = GitHubPRError("GitHub unreachable")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert "PR 未能合并" in r.json()["message"]
    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"
    assert card["note"].startswith("⚠️ 采纳未完成：PR 未能合并")
    assert "GitHub unreachable" in card["note"]
    assert pr_world["local_merges"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"

    # GitHub is back → the same card accepts through its PR.
    _FakeClient.view = {
        "merged": False,
        "state": "open",
        "base": {"ref": "main"},
        "head": {"sha": "abc123"},
    }
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert pr_world["local_merges"] == []


def test_bound_project_closed_pr_stops_the_accept(client, pr_world, monkeypatch):
    """绑定 GitHub 的项目：PR 在 GitHub 被关闭未合并——forge 说了不。采纳停下
    亮出来（重开 PR 或作废卡，由人决定），绝不把被否掉的改动本地合并直推。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=18)
    _enable_app_pr(monkeypatch)
    _FakeClient.view = {"merged": False, "state": "closed"}

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"
    assert card["note"].startswith("⚠️ 采纳未完成：PR 未能合并")
    assert "关闭" in card["note"]
    assert pr_world["local_merges"] == []
    assert [c for c in _FakeClient.calls if c[0] == "merge"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


# ---- CI 镜像与 405 如实转译 (#362, 对齐 GitHub) ------------------------------
#
# 平台不发明 GitHub 没有的拦截：没有 branch protection 时 GitHub 让人看着红色
# 的 ✗ 自己决定合不合——平台持同一姿态，把合并那一刻的检查状态写进卡片留痕，
# 而不是拒绝合并。405 也一样：如实转译 forge 给的理由，而不是一律说成冲突。


def test_red_checks_do_not_block_but_are_mirrored(client, pr_world):
    """红着的 CI 不拦采纳（对齐 GitHub），但合并那一刻的检查状态必须留在卡片
    note 上：人看着红点采纳是合法决定，决定的上下文要事后可读。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=14)
    _FakeClient.checks = [
        _check(name="test", conclusion="failure"),
        _check(name="build", status="in_progress", conclusion=None),
    ]

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "PR #14" in card["note"]
    assert "未通过：test" in card["note"]
    assert "还在跑：build" in card["note"]
    # The merge DID happen — mirror, not gate.
    assert [c for c in _FakeClient.calls if c[0] == "merge"] != []
    assert pr_world["local_merges"] == []


def test_absent_checks_are_mirrored(client, pr_world):
    """没配 CI 的项目照常采纳，但「这次合并没有任何检查把关」要写在卡上——
    #362 的四层静默失效里，最后一层就是没人说得出这句话。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=15)
    _FakeClient.checks = []

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "没有任何 CI 检查" in card["note"]


def test_checks_read_failure_does_not_block_the_merge(client, pr_world):
    """镜像读取失败不拦合并（它只是镜像），但「没读到」也要如实写上。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=16)
    _FakeClient.checks = GitHubPRError("check-runs read failed (HTTP 500)")

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "未能读取 CI 检查状态" in card["note"]
    assert [c for c in _FakeClient.calls if c[0] == "merge"] != []


def test_merge_405_non_conflict_surfaces_githubs_reason(client, pr_world, monkeypatch):
    """405 不再一律写成「合并冲突、已派芝士解决」：GitHub 拒绝合并的真实原因
    （这里：draft）原样呈现，卡保持 pending 由人处理——芝士不会被派去解一个
    不存在的冲突。顺带钉住采纳现场补开的 PR 是事务外持久化的：这次失败回滚
    后 PR 仍在卡上，重试直接走它、不再重开。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # no PR — the accept opens it on the spot
    _enable_app_pr(monkeypatch)
    _FakeClient.merge_error = GitHubPRMergeBlocked(
        'PR #21 is not mergeable: {"message":"Draft pull requests cannot be '
        'merged","documentation_url":"https://docs.github.com/rest"}'
    )

    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert "Draft pull requests cannot be merged" in r.json()["message"]

    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"  # not conflict — 芝士 stays out of it
    assert card["pr_number"] == 21  # durable record survived the rollback
    assert pr_world["local_merges"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"

    # Someone marked the PR ready on GitHub — retry merges the SAME PR.
    _FakeClient.merge_error = None
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert len([c for c in _FakeClient.calls if c[0] == "open_pr"]) == 1
    merges = [c for c in _FakeClient.calls if c[0] == "merge"]
    assert [m[1] for m in merges] == [21, 21]
