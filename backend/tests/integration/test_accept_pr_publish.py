"""不进 PR 的两种采纳形状 + pr-checks 展示端点。

绑定项目的点击合并本身在 tests/integration/test_accept_pr.py（#718 的主套件）。
这里剩下的是：讨论型话题 / 未接 GitHub 的项目（#363：平台自己就是 forge，
local merge 是唯一、正当的采纳），以及 /topics/{id}/pr-checks 这个只读端点。
"""

import asyncio
import uuid

import pytest

from tests.integration.conftest import session_auth_headers


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
        lambda pid, token=None: (
            recorded["syncs"].append(pid) or {"synced": True, "commits": 1}
        ),
    )

    def _local_merge(pid, tid):
        recorded["local_merges"].append(tid)
        return {"merged": False, "noop": True, "reason": "no topic branch"}

    monkeypatch.setattr(ws, "merge_topic", _local_merge)
    monkeypatch.setattr(ws, "prepare_conflict_resolution", lambda pid, tid: ["a.py"])
    _ = review_services  # imported for proximity; accept() resolves ws at call time
    return recorded


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
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo, **_: "main")


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


def test_unbound_project_with_github_upstream_pushes_nothing(
    client, pr_world, monkeypatch
):
    """A GitHub https upstream and no App installation (#718): the platform is
    the forge, so the merge lands in the platform's repo and not one git push
    or fetch runs against GitHub — there is no credential it could run with.
    The card says so, in the forge's words, once."""
    from app.domain.agent import github_app
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _enable_app_pr(monkeypatch)

    async def _no_tokens(_pid, _session):
        return None

    monkeypatch.setattr(github_app, "github_app_tokens_for_project", _no_tokens)
    # A real merge this time (the default pr_world merge is a no-op), so the
    # push-back step actually runs and can be watched.
    monkeypatch.setattr(
        ws, "merge_topic", lambda pid_, tid_: {"merged": True, "commit": "abc"}
    )
    monkeypatch.setattr(ws, "_base_branch", lambda repo: "main")
    git_calls: list[tuple] = []
    monkeypatch.setattr(
        ws, "_git", lambda repo, *args, **kw: git_calls.append(args) or ""
    )

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert [a for a in git_calls if a[0] in ("push", "fetch")] == []
    assert [c for c in _FakeClient.calls if c[0] in ("open_pr", "merge")] == []
    assert card["note"].startswith("ℹ️ 本项目未接 GitHub")
    assert card["note"].count("未接 GitHub") == 1
