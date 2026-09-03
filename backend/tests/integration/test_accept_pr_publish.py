"""PR-based accept (#188 §5.1) — the accept path dispatches on the card's PR.

Functional, through the API: a card that rides a PR is accepted by merging that
PR (never the local merge); a merge refusal lands in the same conflict flow as
a local conflict; GitHub being down falls back to the local path; a PR-less
card never touches GitHub. GitHub itself is a fake client class — the tests
assert what the accept path DOES with it, not HTTP details (unit-tested
separately in tests/unit/test_github_pr.py).

App forge 的采纳不在这里：它把卡送进 `pr_open`，轮询器等 CI 全绿才合，完整行为见
tests/integration/test_accept_app_waits_for_ci.py。这个文件覆盖的是 `_accept_via_pr`
这条仍会在采纳现场合并的路——它现在也只在 forge 说全绿（或这个仓库根本没有检查）
时才合——以及不进 PR 的两种形状。
"""

import asyncio
import uuid

import pytest

from app.domain.oauth import services as oauth_services
from app.domain.review.github_pr import GitHubPRError, GitHubPRMergeBlocked
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import FakeGitHubPrClient


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

    async def installation_token(self) -> tuple[str, str]:
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

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        as_user_token: str | None = None,
    ) -> dict:
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
        f"/accept-cards/{cid}/accept",
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
    # The squash commit that lands on main: a Conventional Commits subject with
    # the PR number, and trailers instead of "验收人：alice".
    assert merges[0][2].endswith(" (#7)")
    assert merges[0][2].startswith("chore(test): ")
    assert "Reviewed-by: alice" in merges[0][3]
    assert pr_world["local_merges"] == []
    assert len(pr_world["pushes"]) == 1  # last-minute edits re-pushed pre-merge
    assert len(pr_world["syncs"]) == 1

    # 交付完成 ≠ 话题结束 (#442 decision 1)：打交付标记，话题不归档。
    topic = client.get(f"/topics/{tid}").json()["data"]
    assert topic["status"] == "active"
    assert topic["accepted_by"] == "alice"


def test_pr_merge_refusal_lands_in_the_conflict_flow(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=8)
    _FakeClient.merge_error = GitHubPRMergeBlocked("PR #8 is not mergeable")

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "conflict"
    assert "PR #8" in card["note"]
    # Not archived — same contract as a local merge conflict; 芝士 goes to fix.
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"
    # The local base was synced so the materialized conflict matches GitHub's.
    assert len(pr_world["syncs"]) == 1

    # After the fix, retry succeeds (the branch is re-pushed and merged).
    _FakeClient.merge_error = None
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"


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
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    # Availability parity: the accept still completes, via the local path.
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert pr_world["local_merges"] != []
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"
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
        f"/accept-cards/{cid}/accept",
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
        f"/accept-cards/{cid}/accept",
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
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_pr_already_merged_on_github_is_respected(client, pr_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=10)
    _FakeClient.view = {"merged": True, "state": "closed"}

    r = client.post(
        f"/accept-cards/{cid}/accept",
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
        async def installation_token(self) -> tuple[str, str]:
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

    r = client.get(f"/topics/{tid}/pr-checks")
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


def test_pr_checks_answers_available_false_when_github_is_unreachable(
    client, monkeypatch
):
    """/pr-checks is polled on a timer, so an exception escaping it is not one
    500 — it is a 500 every few seconds, each posting a traceback into the room
    (2026-08-17: `httpx.ConnectError` out of `pr_view`, TLS handshake). The
    endpoint's contract is "never error"; the reason travels in the payload."""
    import httpx

    from app.api.routes import accept as accept_routes
    from app.domain.workspace import service as ws

    class _Tokens:
        async def installation_token(self) -> tuple[str, str]:
            return "ghs_read", "2099-01-01T00:00:00+00:00"

        async def write_token(self) -> tuple[str, str]:
            return "ghs_write", "2099-01-01T00:00:00+00:00"

    class _UnreachableClient:
        def __init__(self, owner: str, repo: str, tokens, **_):
            pass

        async def pr_view(self, number: int) -> dict:
            raise httpx.ConnectError("TLS handshake failed")

        async def check_runs(self, ref: str) -> list[dict]:
            raise AssertionError("never reached")

    async def _tokens_for_project(_project_id, _session):
        return _Tokens()

    monkeypatch.setattr(
        accept_routes, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(accept_routes, "GitHubPRClient", _UnreachableClient)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=7)

    r = client.get(f"/topics/{tid}/pr-checks")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False
    assert "ConnectError" in data["reason"]


def test_pr_checks_survives_a_failure_outside_the_github_calls(client, monkeypatch):
    """The old guard only wrapped the two GitHub calls; everything before them
    (token mint, upstream read) could still 500. Same contract applies."""
    from app.api.routes import accept as accept_routes
    from app.domain.workspace import service as ws

    class _Tokens:
        async def installation_token(self) -> tuple[str, str]:
            return "ghs_read", "2099-01-01T00:00:00+00:00"

        async def write_token(self) -> tuple[str, str]:
            return "ghs_write", "2099-01-01T00:00:00+00:00"

    async def _tokens_for_project(_project_id, _session):
        return _Tokens()

    def _boom(_pid):
        raise OSError("workspace unavailable")

    monkeypatch.setattr(
        accept_routes, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(ws, "get_upstream", _boom)

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=7)

    r = client.get(f"/topics/{tid}/pr-checks")

    assert r.status_code == 200
    assert r.json()["data"]["available"] is False


def test_prless_card_never_touches_github(client, pr_world):
    """App 机制关着（默认测试世界）：无 PR 卡照旧走本地合并，不碰 GitHub。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # no PR seeded

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert _FakeClient.calls == []  # GitHub never consulted
    assert pr_world["local_merges"] != []  # old path, unchanged


# ---- App forge 的边角：没有分支可进 PR，以及根本没接 GitHub -----------------
#
# App forge 上「采纳」本身已经不在这个文件里了：它不再合并，而是授权，然后由
# 轮询器等 CI 全绿才合（App 采纳等 CI 再合）。那条路的完整行为在
# tests/integration/test_accept_app_waits_for_ci.py 里。留在这里的是两种
# **不进 PR** 的形状，它们的采纳语义没有变：讨论型话题（没有分支，本地合并
# no-op），和未接 GitHub 的项目（#363：平台自己就是 forge）。


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
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "未接 GitHub" not in (card["note"] or "")
    assert [c for c in _FakeClient.calls if c[0] in ("open_pr", "merge")] == []
    assert pr_world["local_merges"] != []  # noop merge — nothing bypassed
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"


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
        f"/accept-cards/{cid}/accept",
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
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"


# ---- forge 说没过就不合，405 如实转译 ----------------------------------------
#
# 采纳不再「读一次检查、把状态写进 note、照合」：forge 没给出全绿的结论，平台就
# 不合，卡挂到 `pr_open` 上等它。要红着合，走署名的人工放行。判据是「这个 PR 的
# 检查过没过」而不是某一道具名检查，所以没配 CI 的仓库不受影响。
#
# 405 照旧如实转译 forge 给的理由，而不是一律说成冲突。


def _accept(client, card_id: str, handle: str = "alice"):
    return client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": handle},
        headers=session_auth_headers(handle),
    )


def _merged(calls) -> list:
    return [c for c in calls if c[0] == "merge"]


@pytest.fixture
def room(monkeypatch):
    """平台在房间里说的每一句（`content` + 折叠起来的 `meta.detail`）。

    平台提示是 fire-and-forget 发出去的（`app.core.background.spawn`），不经过
    work runner，也就不在 `/blocks` 上等得到；这里在它被交给 spawn 之前把参数记
    下来——和这条路的单元测试用的是同一个接缝。"""
    from app.domain.review import services as review_services

    said: list[dict] = []

    async def _posted() -> bool:
        return True

    def _record(_factory, **kwargs):
        said.append(kwargs)
        return _posted()

    monkeypatch.setattr(review_services.webhook_service, "post_with_retries", _record)
    return said


def _room_text(said: list[dict]) -> str:
    return "\n".join(
        f"{s.get('content') or ''}\n{(s.get('meta') or {}).get('detail') or ''}"
        for s in said
    )


def test_a_red_pr_is_not_merged_by_the_accept(client, pr_world):
    """红着的检查拦下这次合并。三件事一起钉：合并 API 一次都没被调用；卡没进
    accepted；也没有偷偷退回本地合并——那等于绕开 PR 把红的直推 main。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=14)
    _FakeClient.checks = [
        _check(name="test", conclusion="failure"),
        _check(name="build", status="in_progress", conclusion=None),
    ]

    r = _accept(client, cid)
    assert r.status_code == 200
    card = r.json()["data"]

    assert _merged(_FakeClient.calls) == []
    assert pr_world["local_merges"] == []
    assert card["status"] == "pr_open"  # 等检查，不是「已采纳」
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_at"] is None
    # 卡面说得出是哪一项红了，以及还有哪一项没跑完。
    assert "未通过：test" in card["note"]
    assert "还在跑：build" in card["note"]


def test_a_red_pr_says_in_the_room_what_failed_and_how_to_get_through(
    client, pr_world, room
):
    """房间里那张卡要够人一眼决定下一步：哪项检查红了，以及红着也要合的出口。
    「哪项红了」在 content + meta.detail 合起来找——平台提示的统一契约把 content
    压成一行人话，原话收进 detail 由前端折叠。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=17)
    _FakeClient.checks = [_check(name="empty-pr-guard", conclusion="failure")]

    assert _accept(client, cid).status_code == 200

    said = _room_text(room)
    assert "PR #17" in said
    assert "empty-pr-guard" in said
    assert "人工放行" in said


def test_a_pr_whose_checks_are_still_running_is_waited_for(client, pr_world):
    """还在跑不是「过了」，也不是「拒了让人待会儿再点」：卡进等检查状态，平台
    自己等——CI 要跑十几分钟，让人守着标签页重新点是把平台的活派给人。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=18)
    _FakeClient.checks = [_check(name="test", status="in_progress", conclusion=None)]

    r = _accept(client, cid)
    assert r.status_code == 200
    card = r.json()["data"]

    assert _merged(_FakeClient.calls) == []
    assert pr_world["local_merges"] == []
    assert card["status"] == "pr_open"
    assert "还在跑：test" in card["note"]
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_zero_checks_waits_for_the_poller_instead_of_merging_blind(client, pr_world):
    """读到零个 check-run 时，这里不再当场判定「这个仓库没有 CI」。

    这条路径是在**刚推完话题分支**之后几毫秒读的，那一刻 GitHub 还没给这个 commit
    建任何 check-run，所以「没配 CI」和「还没建出来」长得一模一样。原来把它读成
    前者，于是恰好在最该拦的场景里放行：把一个没测过的 commit 合了，卡上还留一句
    「该 PR 没有任何 CI 检查」当证据。App 那条路径为同一个姿态付过代价，PR #414
    在打开 25 秒后被合并，比它最后一个检查完成早了 16 分钟。

    分开这两种情况要靠 check-suites 加一段跨轮的宽限期，而采纳是一次性的，没有
    下一轮可等。所以判成「还在跑」，交给有宽限期的轮询器。

    这确实改了 #363 的行为：没配 CI 的仓库不再当场合并。它仍然会被采纳，只是由
    轮询在宽限期结束、确认真的没有任何 workflow 会触发之后放行。用一次一百多秒的
    等待，换掉一个会把未测代码合进主分支的读法。
    """
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=15)
    _FakeClient.checks = []

    r = _accept(client, cid)
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "pr_open"
    assert _merged(_FakeClient.calls) == []
    assert pr_world["local_merges"] == []
    assert "还没出现" in card["note"]


def test_an_unreadable_verdict_is_not_taken_for_green(client, pr_world):
    """读不到结论 ≠ 结论是绿的。平台合的必须是 forge 真答过的那个绿，所以这里
    也不合——但卡面要如实说是「没读到」，不能写成「红的」。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=16)
    _FakeClient.checks = GitHubPRError("check-runs read failed (HTTP 500)")

    r = _accept(client, cid)
    assert r.status_code == 200
    card = r.json()["data"]

    assert _merged(_FakeClient.calls) == []
    assert pr_world["local_merges"] == []
    assert card["status"] == "pr_open"
    assert "未能读取 CI 检查状态" in card["note"]
    assert "未通过" not in card["note"]


def test_a_human_can_still_force_the_red_pr_through(client, pr_world, monkeypatch):
    """默认拒绝、显式放行：房间里承诺的那个出口必须真的能走。拦下来的卡停在
    `pr_open` 上，正是人工放行认的状态——否则那句「可以人工放行」是假的。"""
    from app.domain.review import github_pr

    fake = FakeGitHubPrClient()
    github_pr.set_default_client(fake)

    async def _connected_token(_session, _handle):
        return "gho_alice", ""

    monkeypatch.setattr(
        oauth_services, "get_github_user_token_for_handle_with_reason", _connected_token
    )
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid)
        _give_card_a_pr(client, cid, number=19)
        _FakeClient.checks = [_check(name="test", conclusion="failure")]

        assert _accept(client, cid).status_code == 200
        assert _merged(_FakeClient.calls) == []

        r = client.post(
            f"/accept-cards/{cid}/merge-anyway",
            json={"reason": "CI runner 挂了，跟这次改动无关"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
        card = r.json()["data"]
        assert [m["number"] for m in fake.merge_calls] == [19]
        assert card["status"] == "accepted"
        assert "alice" in card["note"]
        assert "CI runner 挂了" in card["note"]
    finally:
        github_pr.set_default_client(None)


def test_merge_405_non_conflict_surfaces_githubs_reason(client, pr_world):
    """405 不再一律写成「合并冲突、已派芝士解决」：GitHub 拒绝合并的真实原因
    （这里：draft）原样呈现，卡保持 pending 由人处理——芝士不会被派去解一个
    不存在的冲突。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid, number=21)
    _FakeClient.merge_error = GitHubPRMergeBlocked(
        'PR #21 is not mergeable: {"message":"Draft pull requests cannot be '
        'merged","documentation_url":"https://docs.github.com/rest"}'
    )

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert "Draft pull requests cannot be merged" in r.json()["message"]

    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"  # not conflict — 芝士 stays out of it
    assert card["pr_number"] == 21
    assert pr_world["local_merges"] == []
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"

    # Someone marked the PR ready on GitHub — retry merges the SAME PR.
    _FakeClient.merge_error = None
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    merges = [c for c in _FakeClient.calls if c[0] == "merge"]
    assert [m[1] for m in merges] == [21, 21]
