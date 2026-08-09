"""PR publication on submit (PR-based accept, #188 §5.1).

The submit side: a card born pending (flag on) dispatches the PR opener; the
opener pushes the branch, opens the PR, and records pr_number/pr_url on the
card; any failure leaves the card PR-less (the accept path then falls back).
"""

import asyncio
import uuid
from pathlib import Path


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


def _make_card(client, topic_id: str) -> str:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": "alice", "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


class _FakeClient:
    opened: list[dict] = []

    def __init__(self, owner: str, repo: str, tokens, **_):
        pass

    async def open_pr(self, *, head: str, base: str, title: str, body: str) -> dict:
        record = {"head": head, "base": base, "title": title, "body": body}
        type(self).opened.append(record)
        return {"number": 42, "html_url": "https://github.com/acme/widgets/pull/42"}


def _github_world(monkeypatch) -> None:
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    _FakeClient.opened = []

    # #192: the installation is resolved per-project, not from a global.
    async def _fake_tokens_for_project(_project_id, _session):
        return _FakeTokens()

    # pr_publish binds these names at module import — patch them there.
    monkeypatch.setattr(pr_publish, "github_app_tokens_for_project", _fake_tokens_for_project)
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(
        ws, "push_topic_branch", lambda pid, tid, token: f"topic/{tid.hex[:8]}"
    )
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: Path("."))
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")


def test_publication_records_the_pr_on_the_card(client, monkeypatch):
    _github_world(monkeypatch)
    from app.domain.review import pr_publish

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )

    [opened] = _FakeClient.opened
    assert opened["title"] == "做一个东西"  # the topic titles the PR
    assert opened["base"] == "main"
    assert "验收人：alice" in opened["body"]

    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] == 42
    assert card["pr_url"] == "https://github.com/acme/widgets/pull/42"
    # The agent-facing status snapshot carries the pointer too.
    snap = client.get(f"/api/topics/{tid}/status").json()["data"]["cards"][0]
    assert snap["pr_number"] == 42


def test_failure_leaves_the_card_prless(client, monkeypatch):
    _github_world(monkeypatch)
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    def _boom(pid, tid, token):
        raise RuntimeError("push refused")

    monkeypatch.setattr(ws, "push_topic_branch", _boom)

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )

    # Best-effort contract: no PR recorded, card intact, accept will fall back.
    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] is None
    assert card["status"] == "pending"


def test_non_github_upstream_is_not_applicable(client, monkeypatch):
    _github_world(monkeypatch)
    from app.domain.review import pr_publish
    from app.domain.workspace import service as ws

    monkeypatch.setattr(ws, "get_upstream", lambda pid: "/home/repos/widgets")

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )

    assert _FakeClient.opened == []
    card = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] is None


def test_submit_route_dispatches_when_enabled(client, monkeypatch):
    from app.core.config import settings
    from app.domain.review import pr_publish

    dispatched: list[dict] = []
    monkeypatch.setattr(settings, "accept_via_pr", True)
    # enabled() gates on the App being configured (per-project resolution happens
    # in the task); #192 dropped the global github_app_tokens() probe.
    monkeypatch.setattr(settings, "github_app_id", 12345)
    monkeypatch.setattr(settings, "github_app_private_key_path", "/tmp/fake-app.pem")
    monkeypatch.setattr(
        pr_publish, "dispatch", lambda factory, **kw: dispatched.append(kw)
    )

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)

    [kw] = dispatched
    assert str(kw["topic_id"]) == tid
    assert str(kw["project_id"]) == pid


def test_submit_route_stays_quiet_when_disabled(client, monkeypatch):
    from app.domain.review import pr_publish

    dispatched: list[dict] = []
    monkeypatch.setattr(
        pr_publish, "dispatch", lambda factory, **kw: dispatched.append(kw)
    )
    # accept_via_pr defaults to False — the flag ships dark.

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)

    assert dispatched == []
