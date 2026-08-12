import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ValidationError
from app.domain.project.models import AiMode
from app.domain.review import services as review_services
from app.domain.review.models import AcceptStatus
from app.domain.review.services import AcceptService
from app.domain.topic.models import TopicStatus
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws


def _accept_service() -> tuple[AcceptService, SimpleNamespace, SimpleNamespace]:
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        status=AcceptStatus.pending,
        reviewer_handle="alice",
        decided_by=None,
        decided_at=None,
        note="",
        # No PR riding this card — accept takes the local merge path.
        pr_number=None,
        pr_url=None,
    )
    topic = SimpleNamespace(
        id=card.topic_id,
        project_id=uuid.uuid4(),
        title="Deliverable",
        status=TopicStatus.active,
        accepted_by=None,
        accepted_at=None,
        archived_at=None,
    )
    project = SimpleNamespace(
        ai_mode=AiMode.collaborative,
        settings={},
        owner_handle="owner",
    )
    session = AsyncMock()
    # 采纳即归档 now cascades (accepting a room takes the unfinished work inside
    # it), so the accept path asks the DB for this topic's children. These tests
    # deliver a lone topic — answer with none rather than a bare AsyncMock,
    # which `list()` can't consume.
    session.scalars.return_value = SimpleNamespace(all=lambda: [])
    service = AcceptService(session)
    service._repo = AsyncMock()
    service._repo.get.return_value = card
    service._repo.list_approver_handles.return_value = []
    service._topics = AsyncMock()
    service._topics.get.return_value = topic
    service._projects = AsyncMock()
    service._projects.get.return_value = project
    service._enforce_protocol = AsyncMock()
    return service, card, topic


def _patch_notify(monkeypatch) -> AsyncMock:
    """merge 后结果回房间: post_with_retries is the卡1 internal function accept()
    now calls at every merge outcome — stub it so unit tests (no DB) don't try
    to open a real session, and so tests can assert what got posted."""
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook_service, "post_with_retries", notify)
    assert review_services.webhook_service is webhook_service
    return notify


async def _drain_notify() -> None:
    """accept() schedules the notify via asyncio.create_task (fire-and-forget,
    it must not block the accepter's response on a room post) — give the loop
    one tick to actually run it before asserting, same idiom as
    test_push_back.py's watch_dogfood_push scheduling test."""
    await asyncio.sleep(0)


@pytest.mark.anyio
async def test_merge_exception_keeps_acceptance_retryable(monkeypatch):
    service, card, topic = _accept_service()
    notify = _patch_notify(monkeypatch)

    def fail_merge(*_args):
        raise RuntimeError("git object database unavailable")

    monkeypatch.setattr(ws, "merge_topic", fail_merge)
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_args: None)

    with pytest.raises(ValidationError, match="could not be merged"):
        await service.accept(card_id=card.id, decided_by="alice")

    assert card.status == AcceptStatus.pending
    assert topic.status == TopicStatus.active
    assert topic.archived_at is None
    service._repo.add_approval.assert_not_awaited()
    await _drain_notify()
    notify.assert_awaited_once()
    _, kwargs = notify.await_args
    assert kwargs["project_id"] == topic.project_id
    assert kwargs["topic_id"] == topic.id
    assert kwargs["source"] == "accept"
    assert "git object database unavailable" in kwargs["content"]


@pytest.mark.anyio
async def test_empty_conflict_result_keeps_acceptance_retryable(monkeypatch):
    service, card, topic = _accept_service()
    notify = _patch_notify(monkeypatch)
    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_args: {
            "merged": False,
            "reason": "git merge failed before paths were available",
            "conflicts": [],
        },
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_args: None)

    with pytest.raises(ValidationError, match="could not be merged"):
        await service.accept(card_id=card.id, decided_by="alice")

    assert card.status == AcceptStatus.pending
    assert topic.status == TopicStatus.active
    assert topic.archived_at is None
    service._repo.add_approval.assert_not_awaited()
    await _drain_notify()
    notify.assert_awaited_once()
    _, kwargs = notify.await_args
    assert kwargs["source"] == "accept"
    assert "失败" in kwargs["content"]


@pytest.mark.anyio
async def test_conflict_with_paths_marks_card_conflict_and_notifies(monkeypatch):
    service, card, topic = _accept_service()
    notify = _patch_notify(monkeypatch)
    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_args: {
            "merged": False,
            "reason": "CONFLICT (content): Merge conflict in app/main.py",
            "conflicts": ["app/main.py"],
        },
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_args: None)

    returned = await service.accept(card_id=card.id, decided_by="alice")

    assert returned is card
    assert card.status == AcceptStatus.conflict
    assert topic.status == TopicStatus.active
    service._repo.add_approval.assert_awaited_once_with(card.id, "alice")
    await _drain_notify()
    notify.assert_awaited_once()
    _, kwargs = notify.await_args
    assert kwargs["project_id"] == topic.project_id
    assert kwargs["topic_id"] == topic.id
    assert kwargs["source"] == "accept"
    assert "冲突" in kwargs["content"]
    assert "app/main.py" in kwargs["content"]


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["no topic branch", "topic is the base branch"])
async def test_explicit_merge_noop_remains_acceptable(monkeypatch, reason):
    service, card, topic = _accept_service()
    notify = _patch_notify(monkeypatch)
    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_args: {"merged": False, "noop": True, "reason": reason},
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_args: None)

    returned = await service.accept(card_id=card.id, decided_by="alice")

    assert returned is card
    assert card.status == AcceptStatus.accepted
    assert topic.status == TopicStatus.archived
    service._repo.add_approval.assert_awaited_once_with(card.id, "alice")
    await _drain_notify()
    notify.assert_awaited_once()
    _, kwargs = notify.await_args
    assert kwargs["project_id"] == topic.project_id
    assert kwargs["topic_id"] == topic.id
    assert kwargs["source"] == "accept"
    assert "✅" in kwargs["content"]
    assert "alice" in kwargs["content"]


@pytest.mark.anyio
async def test_successful_merge_notifies_room_with_push_status(monkeypatch):
    service, card, topic = _accept_service()
    notify = _patch_notify(monkeypatch)
    monkeypatch.setattr(
        ws, "merge_topic", lambda *_args: {"merged": True, "commit": "abc123"}
    )
    monkeypatch.setattr(
        ws,
        "push_back",
        lambda *_args: {"mode": "upstream", "target": "origin/main"},
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_args: None)

    returned = await service.accept(card_id=card.id, decided_by="alice")

    assert returned is card
    assert card.status == AcceptStatus.accepted
    assert topic.status == TopicStatus.archived
    await _drain_notify()
    notify.assert_awaited_once()
    _, kwargs = notify.await_args
    assert kwargs["source"] == "accept"
    assert "✅" in kwargs["content"]
    assert "origin/main" in kwargs["content"]
