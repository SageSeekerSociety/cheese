"""两阶段采纳降级措辞分两种 (2026-08-10).

A card that falls back from the PR path to a direct merge used to read the same
way whatever went wrong: "⚠️ 未走 PR 采纳（GitHub 侧调用失败：…300 chars of raw
git rejection…）". For a card that genuinely edits `.github/workflows/` that is
not a failure at all — it is a known, permanent limitation — and the ⚠️ + git
log made it look broken every single time.

These tests pin the OBSERVABLE difference on `card.note`, and pin the invariant
that makes the prefix change safe: a degraded card never reaches the pr_open
poller whose dedup keys on `note.startswith("⚠️")`.
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
async def test_workflow_scope_rejection_reads_as_a_known_limit(monkeypatch):
    """同步后仍被 workflow 权限拒绝 → 平静措辞，且不倒原始 git 日志。"""
    service, card, topic = _accept_service()
    _stub_local_merge(monkeypatch)
    _fail_push(monkeypatch, WORKFLOW_REJECTION)

    await service.accept(card_id=card.id, decided_by="alice")
    await asyncio.sleep(0)  # let the fire-and-forget room notify run

    assert card.status == AcceptStatus.accepted
    assert not card.note.startswith("⚠️")
    assert card.note.startswith("ℹ️")
    # Explains itself in the card, without a human having to read git's output.
    assert ".github/workflows/" in card.note
    assert "workflows 权限" in card.note
    # The 300-char raw rejection is pure noise for this case — it must be gone.
    assert "refusing to allow" not in card.note
    assert "remote rejected" not in card.note
    assert "GitHub 侧调用失败" not in card.note
    # The actual outcome is still reported.
    assert "已合并并推送到上游 origin/main" in card.note


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
    ],
)
async def test_other_failures_keep_the_warning_wording(monkeypatch, message, fragment):
    """其它一切失败 → 保持 ⚠️ + 原始错误尾巴。"""
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
