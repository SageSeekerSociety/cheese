"""PR 回流去重（nudge ledger）：同一个失败的重复轮询不重复叫芝士。"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.review.notes import NoteCode
from app.domain.review.services import AcceptService


def _service_and_card():
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        note="",
        note_code=None,
        nudge_state={},
        pr_number=7,
        pr_url="https://github.com/acme/widgets/pull/7",
        pr_head_sha="abc123",
    )
    topic = SimpleNamespace(id=card.topic_id, project_id=uuid.uuid4())
    service = AcceptService(MagicMock())
    return service, card, topic


@pytest.mark.anyio
async def test_nudge_dedup_still_suppresses_a_repeat_ci_failure():
    """A second poll tick on the same failing commit must not re-notify 芝士."""
    service, card, topic = _service_and_card()
    runner = MagicMock()

    def _tick() -> None:
        nudge = service._ci_nudge(
            card=card, tail="pytest failed", stage="CI", owner="acme", repo="widgets"
        )
        service._dispatch_nudges(
            card=card,
            topic=topic,
            pending=[nudge] if nudge is not None else [],
            chat_service=MagicMock(),
            runner=runner,
        )

    _tick()
    first_note = card.note
    assert card.note_code is NoteCode.checks_failed
    assert runner.submit.call_count == 1

    _tick()

    assert card.note == first_note
    assert runner.submit.call_count == 1
