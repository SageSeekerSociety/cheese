"""两阶段采纳降级措辞 (2026-08-10, revised 2026-08-13).

A card that falls back from the personal-token PR path to a direct merge keeps
the reason on `card.note`: "⚠️ 未走 PR 采纳（GitHub 侧调用失败：…raw tail…）".
There used to be a second, calm ℹ️ shape for workflow-permission rejections,
premised on those being a KNOWN PERMANENT limitation of the platform's
credential — that premise died on 2026-08-12 when the GitHub App was granted
`workflows:write` (installation 152342238), so the sentinel is deleted and a
workflow rejection now reads exactly like every other GitHub-side failure:
⚠️, needing a human, never a calm auto-direct-merge.

These tests pin that single wording on `card.note`, and pin the invariant that
keeps the prefix safe: a degraded card never reaches the pr_open poller whose
dedup keys on `note.startswith("⚠️")`.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import ValidationError
from app.domain.project.models import AiMode
from app.domain.review import services as review_services
from app.domain.review.models import AcceptStatus
from app.domain.review.services import AcceptService
from app.domain.topic.models import TopicStatus
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws

# The verbatim shape GitHub sends back, as quoted in
# workspace.service._is_workflow_permission_rejection's docstring.
WORKFLOW_REJECTION = (
    "git push failed: To https://github.com/acme/cheese.git\n"
    " ! [remote rejected] topic/abcd -> cheesex/abcd (refusing to allow a "
    "GitHub App to create or update workflow `.github/workflows/build.yml` "
    "without `workflows` permission)\n"
    "error: failed to push some refs to 'https://github.com/acme/cheese.git'"
)


def _accept_service() -> tuple[AcceptService, SimpleNamespace, SimpleNamespace]:
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        status=AcceptStatus.pending,
        reviewer_handle="alice",
        decided_by=None,
        decided_at=None,
        note="",
        # No PR riding this card yet — accept tries to OPEN one, and it is that
        # attempt which degrades.
        pr_number=None,
        pr_url=None,
        pr_repo=None,
        pr_head_sha=None,
        pr_merged_at=None,
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
    # Prereqs resolved: a connected token and a connected repo, so the accept
    # really does attempt the two-phase push (that is what we want to fail).
    service._resolve_pr_prerequisites = AsyncMock(
        return_value=(("gho_token", "acme", "cheese"), "")
    )
    return service, card, topic


def _stub_local_merge(monkeypatch) -> None:
    """The direct-merge path the accept degrades onto — succeeds, so the note
    ends up as "<degrade prefix>；已合并并推送到上游 origin/main"."""
    monkeypatch.setattr(
        ws, "merge_topic", lambda *_a: {"merged": True, "commit": "abc"}
    )
    monkeypatch.setattr(
        ws, "push_back", lambda *_a: {"mode": "upstream", "target": "origin/main"}
    )
    monkeypatch.setattr(ws, "stop_topic_container", lambda *_a: None)
    monkeypatch.setattr(
        webhook_service, "post_with_retries", AsyncMock(return_value=True)
    )
    assert review_services.webhook_service is webhook_service


def _fail_push(monkeypatch, message: str) -> None:
    def boom(*_args, **_kwargs):
        raise ValidationError(message)

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", boom)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "fragment"),
    [
        # Credential died.
        (
            "git push failed: fatal: Authentication failed for 'https://…'",
            "Authentication failed",
        ),
        # GitHub / network hiccup.
        (
            "git push failed: fatal: unable to access: Could not resolve host",
            "Could not resolve host",
        ),
        # Sync itself could not complete. Mentions "workflow" but is NOT a
        # rejection — a human has to look at this one, so it keeps ⚠️.
        (
            "话题分支的 workflow 文件与 GitHub 默认分支不一致且无法同步"
            "（merge conflict）",
            "无法同步",
        ),
        # A workflow-permission rejection that survived the sync-and-retry in
        # push_topic_branch_for_github_pr. No longer a calm ℹ️ "known
        # permanent limitation" — the App holds `workflows:write` since
        # 2026-08-12, and on this personal-token path it means the approver's
        # own token lacks the workflow scope: a human should look.
        (WORKFLOW_REJECTION, "refusing to allow"),
    ],
)
async def test_all_failures_keep_the_warning_wording(monkeypatch, message, fragment):
    """一切失败 → ⚠️ + 原始错误尾巴，没有平静的 ℹ️ 例外。"""
    service, card, topic = _accept_service()
    _stub_local_merge(monkeypatch)
    _fail_push(monkeypatch, message)

    await service.accept(card_id=card.id, decided_by="alice")
    await asyncio.sleep(0)

    assert card.status == AcceptStatus.accepted
    assert card.note.startswith("⚠️ 未走 PR 采纳（GitHub 侧调用失败：")
    assert fragment in card.note
    assert "已合并并推送到上游 origin/main" in card.note


@pytest.mark.anyio
async def test_degraded_card_never_reaches_the_pr_poller(monkeypatch):
    """The invariant that makes changing the prefix safe: `_nudge_pr_fix` dedups
    on `note.startswith("⚠️")`, but it only ever runs for `pr_open` cards, and a
    card carrying a degrade note is `accepted`. Drive the poller with such a
    card and it must do nothing at all — no note rewrite, no 芝士 summon."""
    service, card, _topic = _accept_service()
    _stub_local_merge(monkeypatch)
    _fail_push(monkeypatch, WORKFLOW_REJECTION)
    await service.accept(card_id=card.id, decided_by="alice")
    await asyncio.sleep(0)
    degrade_note = card.note

    runner = MagicMock()
    await service.advance_pr_card(card.id, chat_service=MagicMock(), runner=runner)

    assert card.note == degrade_note
    runner.submit.assert_not_called()


@pytest.mark.anyio
async def test_nudge_dedup_still_suppresses_a_repeat_ci_failure():
    """The ⚠️ dedup itself is untouched: a second poll tick on the same failing
    commit must not re-notify 芝士."""
    service, card, topic = _accept_service()
    runner = MagicMock()

    service._nudge_pr_fix(
        card=card,
        topic=topic,
        tail="pytest failed",
        stage="CI",
        chat_service=MagicMock(),
        runner=runner,
    )
    first_note = card.note
    assert first_note.startswith("⚠️")
    assert runner.submit.call_count == 1

    service._nudge_pr_fix(
        card=card,
        topic=topic,
        tail="pytest failed",
        stage="CI",
        chat_service=MagicMock(),
        runner=runner,
    )

    assert card.note == first_note
    assert runner.submit.call_count == 1
