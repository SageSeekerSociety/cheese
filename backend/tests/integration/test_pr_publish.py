"""PR publication on submit (PR-based accept, #188 §5.1).

The submit side: a card born pending (flag on) dispatches the PR opener; the
opener pushes the branch, opens the PR, and records pr_number/pr_url on the
card; any failure leaves the card PR-less (the accept path then falls back).
"""

import asyncio
import uuid
from pathlib import Path


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, **extra) -> str:
    r = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            **extra,
        },
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

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        as_user_token: str | None = None,
    ) -> dict:
        record = {
            "head": head,
            "base": base,
            "title": title,
            "body": body,
            "as_user_token": as_user_token,
        }
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
    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _fake_tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _FakeClient)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(
        ws, "push_topic_branch", lambda pid, tid, token: f"topic/{tid.hex[:8]}"
    )
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: Path("."))
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo, **_: "main")


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
    assert opened["title"] == "chore(test): file an accept card"
    assert opened["base"] == "main"
    # Routing bookkeeping is gone from the body — a GitHub reviewer needs the
    # change, not the platform's internal handoff.
    assert "验收人" not in opened["body"]
    assert f"Cheese-Topic: {tid}" in opened["body"]

    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] == 42
    assert card["pr_url"] == "https://github.com/acme/widgets/pull/42"
    # The agent-facing status snapshot carries the pointer too.
    snap = client.get(f"/topics/{tid}/status").json()["data"]["cards"][0]
    assert snap["pr_number"] == 42


def test_failure_leaves_the_card_prless_but_never_silent(client, monkeypatch):
    """#362 修法 1: a failed publish lands ON THE CARD — a PR-less card must
    look visibly different from one whose PR simply hasn't landed yet. A later
    successful publish (the accept-time retry uses the same record path)
    clears the failure note along with recording the PR."""
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

    # No PR recorded, card still pending — but the failure is on the card.
    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] is None
    assert card["status"] == "pending"
    assert card["note_level"] == "error"
    assert card["note"].startswith("开 PR 失败")
    assert "push refused" in card["note"]

    # The push works again → a re-publish records the PR and clears the note.
    monkeypatch.setattr(
        ws, "push_topic_branch", lambda pid_, tid_, token: f"topic/{tid_.hex[:8]}"
    )
    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )
    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] == 42
    assert card["note"] == ""


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
    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["pr_number"] is None


def test_submit_route_dispatches_when_enabled(client, monkeypatch):
    from app.core.config import settings
    from app.domain.review import pr_publish

    dispatched: list[dict] = []
    # enabled() gates on the App being configured (per-project resolution happens
    # in the task); #192 dropped the global github_app_tokens() probe, and #718
    # dropped the accept_via_pr flag — the App configured IS the switch.
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


def test_submit_route_stays_quiet_when_app_not_configured(client, monkeypatch):
    from app.domain.review import pr_publish

    dispatched: list[dict] = []
    # Flag defaults on now, but with no GitHub App configured `enabled()` is
    # still False, so a project without the App never dispatches a PR open.
    monkeypatch.setattr(
        pr_publish, "dispatch", lambda factory, **kw: dispatched.append(kw)
    )

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)

    assert dispatched == []


# ---- 提交与 PR 规范 (2026-08-16) --------------------------------------------
#
# Before this: the PR was titled with the topic's Chinese room name and bodied
# with routing bookkeeping, the squash commit inherited both, and the whole
# thing was opened by the App — so on GitHub none of it belonged to the person
# whose work it was.


def _publish(client, pid: str, tid: str, cid: str) -> dict:
    from app.domain.review import pr_publish

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )
    [opened] = _FakeClient.opened
    return opened


def test_the_card_s_subject_titles_the_pr(client, monkeypatch):
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(
        client,
        tid,
        change_subject="fix(accept): open the PR as the requester",
        change_body="App-opened PRs belong to the bot, so nobody gets credit.",
    )

    opened = _publish(client, pid, tid, cid)

    assert opened["title"] == "fix(accept): open the PR as the requester"
    assert opened["body"].startswith(
        "App-opened PRs belong to the bot, so nobody gets credit."
    )


def test_the_pr_body_claims_no_review_that_has_not_happened(client, monkeypatch):
    """The PR opens when the card is FILED; 采纳 is what merges it. A
    `Reviewed-by:` written at open time would name someone who has not looked
    at it yet."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, change_subject="fix: stop the crash")

    opened = _publish(client, pid, tid, cid)

    assert "Reviewed-by:" not in opened["body"]
    assert f"Cheese-Topic: {tid}" in opened["body"]


def test_a_malformed_subject_is_refused_at_the_card(client, monkeypatch):
    """Rejected where it can still be fixed cheaply — not silently normalised
    into history, and not discovered by a human reading `git log` next month."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.post(
        f"/topics/{tid}/accept-card",
        json={"reviewer_handle": "alice", "change_subject": "做完了分页"},
    )

    assert r.status_code == 422
    assert "Conventional Commits" in r.text
    assert client.get(f"/topics/{tid}/accept-card").json()["data"]["total"] == 0


def _forget_the_subject(client, card_id: str) -> None:
    """Turn a card into a pre-2026-08-17 row: `change_subject` is nullable, and
    every card filed before the column existed has NULL there. The API can no
    longer produce one, so this is the only way to reach the fallback."""
    from sqlalchemy import update

    from app.domain.review.models import AcceptCard

    async def _run() -> None:
        async with client.test_factory() as db:
            await db.execute(
                update(AcceptCard)
                .where(AcceptCard.id == uuid.UUID(card_id))
                .values(change_subject=None)
            )
            await db.commit()

    asyncio.run(_run())


def test_a_legacy_card_still_gets_the_fallback_title(client, monkeypatch):
    """Requiring a subject at the card must not strand the rows already in the
    table. Deleting `fallback_subject` would break the PR title and the merge
    subject of every card filed before the column existed."""
    _github_world(monkeypatch)
    from app.domain.review import pr_publish

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _forget_the_subject(client, cid)

    asyncio.run(
        pr_publish._run(
            client.test_factory,
            card_id=uuid.UUID(cid),
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
        )
    )

    [opened] = _FakeClient.opened
    # The fallback is meant to look wrong in a git log — that is the point.
    assert opened["title"] == "chore: 做一个东西"


def test_a_card_with_no_subject_at_all_is_refused(client, monkeypatch):
    """PR #500 landed as `chore: 实况文档改名` — a chat-room name as the title of
    a merged change — because filing without a subject was allowed and the
    fallback quietly filled one in. Nothing new may reach that fallback."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.post(f"/topics/{tid}/accept-card", json={"reviewer_handle": "alice"})

    assert r.status_code == 422
    # The refusal has to teach, not just refuse: the reader is an agent one
    # turn away from re-filing, so the shape AND a copy-pasteable example.
    assert "type(scope): description" in r.text
    assert "cheese accept-request" in r.text
    assert "--subject" in r.text
    assert client.get(f"/topics/{tid}/accept-card").json()["data"]["total"] == 0


def test_a_blank_subject_is_refused_like_a_missing_one(client, monkeypatch):
    """Whitespace is not a subject. Without this the string survives the
    presence check and `chore: <话题标题>` comes back through `valid_subject`."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.post(
        f"/topics/{tid}/accept-card",
        json={"reviewer_handle": "alice", "change_subject": "   "},
    )

    assert r.status_code == 422
    assert "type(scope): description" in r.text
    assert client.get(f"/topics/{tid}/accept-card").json()["data"]["total"] == 0


def test_a_valid_subject_is_stored_verbatim(client, monkeypatch):
    """The complement of the two refusals: the gate rejects, it does not
    rewrite. What the filer wrote is what enters history."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    subject = "fix(accept): require a commit subject when filing a card"

    _make_card(client, tid, change_subject=subject)

    [card] = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert card["change_subject"] == subject


def test_the_subject_is_visible_on_the_card_before_anyone_accepts(client, monkeypatch):
    """The reviewer is the last person who can object to the line that is about
    to enter the project's permanent history."""
    _github_world(monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid, change_subject="fix: stop the crash", change_body="why")

    [card] = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert card["change_subject"] == "fix: stop the crash"
    assert card["change_body"] == "why"
