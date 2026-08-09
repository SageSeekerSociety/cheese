import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ValidationError
from app.domain.project.models import AiMode
from app.domain.review.models import AcceptStatus
from app.domain.review.services import AcceptService
from app.domain.topic.models import TopicStatus
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


@pytest.mark.anyio
async def test_merge_exception_keeps_acceptance_retryable(monkeypatch):
    service, card, topic = _accept_service()

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


@pytest.mark.anyio
async def test_empty_conflict_result_keeps_acceptance_retryable(monkeypatch):
    service, card, topic = _accept_service()
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


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["no topic branch", "topic is the base branch"])
async def test_explicit_merge_noop_remains_acceptable(monkeypatch, reason):
    service, card, topic = _accept_service()
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
