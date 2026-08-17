"""422「PR 已存在」→ 认领，而不是降级 (2026-08-10).

Accepting topic `0bbc3403` twice raced two PR-open calls at the same head
branch. The loser got GitHub's 422 "a pull request already exists", which
`accept()` read as "PR mechanism unavailable" and degraded on: local merge +
direct push to main. The damage was two-fold and both halves are pinned here:

- an ORPHAN PR — #234 stayed open with its CI burning, while the code it
  contained had already landed on main by another route, so it could never be
  merged and its branch was never cleaned up;
- two CONTRADICTORY room messages — "🔁 已开 PR #234 …话题保持 active" from the
  attempt that won the race, immediately followed by "✅ …采纳并合并" +
  archive from the one that degraded.

So these tests assert the room traffic, not just the card: after a claim there
must be exactly one message and it must be the PR one; after a genuine degrade
there must be exactly one and it must NOT mention a PR being opened.

The client is faked here on purpose — WHICH 422 counts as "already exists" is
the client's job and is tested against real GitHub payloads in
tests/unit/test_github_pr_open_pull_request.py.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.project.models import AiMode
from app.domain.review import github_pr
from app.domain.review.github_pr import GitHubPrError, PullRequest
from app.domain.review.models import AcceptStatus
from app.domain.review.services import AcceptService
from app.domain.topic.models import TopicStatus
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws

PUSHED_SHA = "b8359385"


def _accept_service() -> tuple[AcceptService, SimpleNamespace, SimpleNamespace]:
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        status=AcceptStatus.pending,
        reviewer_handle="alice",
        routing_reason="最懂",
        decided_by=None,
        decided_at=None,
        note="",
        pr_number=None,
        pr_url=None,
        pr_repo=None,
        pr_head_sha=None,
        pr_merged_at=None,
    )
    topic = SimpleNamespace(
        id=card.topic_id,
        project_id=uuid.uuid4(),
        parent_id=None,
        title="做一个东西",
        status=TopicStatus.active,
        created_by="cheese",
        accepted_by=None,
        accepted_at=None,
        archived_at=None,
    )
    project = SimpleNamespace(
        ai_mode=AiMode.collaborative, settings={}, owner_handle="owner"
    )
    service = AcceptService(AsyncMock())
    service._repo = AsyncMock()
    service._repo.get.return_value = card
    service._repo.list_approver_handles.return_value = []
    service._topics = AsyncMock()
    service._topics.get.return_value = topic
    service._projects = AsyncMock()
    service._projects.get.return_value = project
    service._machines = AsyncMock()
    service._enforce_protocol = AsyncMock()
    service._resolve_pr_prerequisites = AsyncMock(
        return_value=(("gho_token", "acme", "widgets"), "")
    )
    # Who the change is credited to. Reads the topic's roster, so on an
    # AsyncMock session it resolves to nothing anyway — stubbed rather than
    # left to fail silently, which leaks an un-awaited coroutine into every
    # test in this file. Attribution is not what these assert.
    service._attribution = AsyncMock(return_value=(None, None))
    return service, card, topic


class _ClaimingClient:
    """Stands in for `HttpxGitHubPrClient` after it resolved a 422
    already-exists into the PR that was already open on the branch."""

    def __init__(self, number: int = 234) -> None:
        self.number = number
        self.calls = 0

    async def open_pull_request(self, **kwargs) -> PullRequest:
        self.calls += 1
        return PullRequest(
            number=self.number,
            url=f"https://github.com/acme/widgets/pull/{self.number}",
            head_sha=PUSHED_SHA,
            already_existed=True,
        )


class _FailingClient:
    """Every other GitHub failure, including every other 422."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def open_pull_request(self, **kwargs) -> PullRequest:
        raise GitHubPrError(self.message)


def _wire(monkeypatch, client) -> list[str]:
    """Common stubs; returns the list room messages accumulate into."""
    posted: list[str] = []

    async def fake_post(_factory, *, project_id, topic_id, content, source):
        posted.append(content)
        return True

    monkeypatch.setattr(webhook_service, "post_with_retries", fake_post)
    monkeypatch.setattr(
        ws,
        "push_topic_branch_for_github_pr",
        lambda *_a, **_kw: {"head_sha": PUSHED_SHA},
    )
    monkeypatch.setattr(ws, "pr_base_branch", lambda *_a: "main")
    # The degrade path, wired to succeed — so a test that expects NO degrade is
    # proving the claim really stopped it, not that the fallback was broken.
    monkeypatch.setattr(
        ws, "merge_topic", lambda *_a: {"merged": True, "commit": "abc"}
    )
    monkeypatch.setattr(
        ws, "push_back", lambda *_a: {"mode": "upstream", "target": "origin/main"}
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_a: None)
    github_pr.set_default_client(client)
    return posted


@pytest.mark.anyio
async def test_claimed_pr_puts_the_card_on_the_pr_path(monkeypatch):
    """认领成功 → 卡片进 pr_open 并带上真实 PR 的 number/url/head_sha，话题不归档。"""
    service, card, topic = _accept_service()
    client = _ClaimingClient(number=234)
    _wire(monkeypatch, client)
    try:
        await service.accept(card_id=card.id, decided_by="alice")
        await asyncio.sleep(0)  # let the fire-and-forget room notify run
    finally:
        github_pr.set_default_client(None)

    assert card.status == AcceptStatus.pr_open
    assert card.pr_number == 234
    assert card.pr_url == "https://github.com/acme/widgets/pull/234"
    assert card.pr_repo == "acme/widgets"
    assert card.pr_head_sha == PUSHED_SHA
    assert card.pr_merged_at is None
    # 采纳只是授权，卡还在 pr_open 等 CI —— 交付标记要等合并才落。
    assert topic.status == TopicStatus.active
    assert topic.accepted_at is None
    assert topic.archived_at is None
    # No degrade happened, so no degrade wording leaked onto the note.
    assert "未走 PR 采纳" not in card.note
    assert "已认领" in card.note
    assert "#234" in card.note


@pytest.mark.anyio
async def test_claim_emits_only_the_pr_message_never_the_archive_one(monkeypatch):
    """消息不能自相矛盾: 认领后房间里只该有「PR 开着、话题保持 active」这一条。"""
    service, card, _topic = _accept_service()
    posted = _wire(monkeypatch, _ClaimingClient(number=234))
    try:
        await service.accept(card_id=card.id, decided_by="alice")
        await asyncio.sleep(0)
    finally:
        github_pr.set_default_client(None)

    assert len(posted) == 1
    (message,) = posted
    assert "已认领该分支上已存在的 PR #234" in message
    assert "话题保持 active" in message
    # The message that contradicted it in the incident ("✅ 话题已被 alice
    # 采纳并合并。"). Note this one legitimately ends "…部署也成功后才会归档" —
    # a promise about later, not a claim that it happened.
    assert "采纳并合并" not in message
    assert "✅" not in message


@pytest.mark.anyio
async def test_other_github_failures_still_degrade_and_say_so(monkeypatch):
    """其它 422（真正的校验失败）仍然降级，且房间里只有降级那一条消息 —
    降级路径和 PR 路径不会同时发通知。"""
    service, card, topic = _accept_service()
    posted = _wire(
        monkeypatch,
        _FailingClient(
            "GitHub 拒绝开 PR（HTTP 422）："
            '{"message":"Validation Failed","errors":[{"resource":"PullRequest",'
            '"field":"base","code":"invalid"}]}'
        ),
    )
    try:
        await service.accept(card_id=card.id, decided_by="alice")
        await asyncio.sleep(0)
    finally:
        github_pr.set_default_client(None)

    assert card.status == AcceptStatus.accepted
    assert card.pr_number is None
    assert card.note.startswith("⚠️ 未走 PR 采纳（GitHub 侧调用失败：")
    assert "已合并并推送到上游 origin/main" in card.note
    # 交付完成 ≠ 话题结束 (#442 decision 1)：本地合并这条路同样不归档。
    assert topic.status == TopicStatus.active
    assert topic.accepted_at is not None
    assert topic.archived_at is None

    assert len(posted) == 1
    (message,) = posted
    assert "采纳并合并" in message
    assert "已开 PR" not in message
    assert "已认领" not in message


@pytest.mark.anyio
async def test_claimed_pr_is_opened_exactly_once_per_accept(monkeypatch):
    """No second POST behind the claim — the retry lives inside the client."""
    service, card, _topic = _accept_service()
    client = _ClaimingClient()
    _wire(monkeypatch, client)
    try:
        await service.accept(card_id=card.id, decided_by="alice")
        await asyncio.sleep(0)
    finally:
        github_pr.set_default_client(None)

    assert client.calls == 1
