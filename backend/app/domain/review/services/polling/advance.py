"""One poll tick: claiming a card, the guards on which cards it may touch, its
preflight, the crash note, and where a merge lands on its task."""

from __future__ import annotations

import uuid

from app.domain.review import github_pr, notes
from app.domain.review import services as pkg
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services import poll_claim
from app.domain.review.services._shared import logger
from app.domain.review.task_landing import delivery_landed
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic


async def advance_pr_card(
    self: pkg.AcceptService, card_id: uuid.UUID, *, chat_service, runner
) -> None:
    """Observe external merges and advance pending PR cards (#718). The
    poller does three things and nothing else: mirror the merge state
    onto the card, send the events the 「谁的活」 table names (deduped
    through the nudge ledger), and merge a card whose auto-merge is armed
    once the rules are satisfied. Called by
    `review/pr_poll.py::poll_open_prs`; never raises for a transient GitHub
    hiccup — the next poll just retries."""
    # A webhook and the reconciliation clock may observe the same card; only
    # one poll may advance it or emit its notifications (`poll_claim`).
    token = await poll_claim.claim(self._session, card_id)
    if token is None:
        return
    self._commits_before_remote = True
    try:
        await _advance_claimed(self, card_id, chat_service=chat_service, runner=runner)
        await poll_claim.release(self._session, card_id, token)
    except Exception:
        await self._session.rollback()
        await poll_claim.release(self._session, card_id, token)
        await self._session.commit()
        raise
    finally:
        self._commits_before_remote = False


async def _advance_claimed(
    self: pkg.AcceptService, card_id: uuid.UUID, *, chat_service, runner
) -> None:
    card = await self._session.get(AcceptCard, card_id, populate_existing=True)
    if card is None:
        return
    if (
        card.status not in (AcceptStatus.pending, AcceptStatus.rejected)
        or card.pr_number is None
    ):
        return
    if card.status == AcceptStatus.rejected:
        tree = (
            await TaskService(self._session).get(card.task_id)
            if card.task_id is not None
            else None
        )
        if (
            card.pr_merged_at is not None
            or tree is None
            or tree.status != TaskStatus.open
        ):
            return
    topic = await self._topic_or_404(card.topic_id)
    forge = await self._resolve_forge(topic.project_id, card=card)
    await forge.poll(self, card, topic, chat_service=chat_service, runner=runner)


async def _advance_github_card(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    *,
    chat_service,
    runner,
) -> None:
    try:
        owner, repo = await self._pr_repo_of(card, topic)
    except Exception as exc:  # pause this tick, retry next
        logger.warning("card %s: cannot resolve PR repo: %s", card.id, exc)
        return

    creds, reason = await self._pr_poll_credentials(card, topic)
    if creds is None:
        logger.warning(
            "card %s has no usable GitHub token anymore (%s); "
            "skipping this poll (will retry next tick)",
            card.id,
            reason,
        )
        # Without this the card just sits there forever and looks
        # identical to "CI still running" — no signal anyone's token died.
        if (
            card.status == AcceptStatus.pending
            and card.note_code is not notes.NoteCode.poll_paused
        ):
            notes.record(
                card,
                notes.NoteCode.poll_paused,
                f"轮询暂停，下一轮还会重试：{reason}",
            )
            await self._session.flush()
        return

    # Token is usable again → the pause note is stale. Clearing it here is
    # what makes the pause self-healing: it stops describing a condition
    # that no longer holds, AND it can no longer sit in front of a real CI
    # failure (which is how "轮询暂停" used to swallow CI 失败 notifications
    # — see the 2026-08-10 note in `_ci_nudge`). Only this exact prefix is
    # cleared; 重推失败 /
    # 拒绝合并 / 检查未通过 notes describe live conditions and stay put.
    if card.status == AcceptStatus.pending and card.note_code in (
        notes.NoteCode.poll_paused,
        notes.NoteCode.poll_failed,
    ):
        notes.clear(card)
        await self._session.flush()

    client = await self._status_client(topic.project_id)
    try:
        await self._poll_pr_card(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            chat_service=chat_service,
            runner=runner,
        )
    except github_pr.GitHubPrError as exc:
        logger.warning(
            "GitHub API hiccup polling card %s: %s — retrying next tick",
            card.id,
            exc,
        )
        # And say it on the CARD. A log line is only readable by whoever has
        # a shell on the host, and the person waiting is looking at a card
        # whose note still says 「等 CI」 — so a poll that fails every tick
        # forever is indistinguishable from checks that are simply slow.
        # That is how #575/#582 sat green-but-unmerged with nothing on
        # screen to explain it. Same treatment the credential branch above
        # already gets, for the same reason.
        if card.status == AcceptStatus.pending:
            self._note_poll_failed(card, exc)
        await self._session.flush()


async def _mark_task_merged(
    self: pkg.AcceptService, card: AcceptCard, *, delivered_head: str | None = None
) -> bool:
    """Record ``card``'s merge on its task; False when it does not land there.

    A merge the task has already had does not land again — see
    `task_landing._already_landed`. A caller that has words for a batch that
    just landed says them on this answer."""
    if card.task_id is None:
        return False

    task = await TaskService(self._session).require_in_room(card.topic_id, card.task_id)
    return await delivery_landed(
        self._session,
        task,
        by=card.decided_by,
        head=delivered_head,
        pr_number=card.pr_number,
        completes=card.completes_task,
        handed_over=card.created_at,
        record_instruction=self._record_instruction,
        task_teammate=self._task_teammate,
    )


async def note_poll_crashed(
    self: pkg.AcceptService, card_id: uuid.UUID, exc: BaseException
) -> None:
    """Same explanation as `_note_poll_failed`, for a poll that died on
    something other than a GitHub error (the poller's own catch-all).

    Its caller rolled the failed tick back, so this runs on a fresh session
    and is a no-op for a card that has since settled or lost its PR.
    """
    card = await AcceptCardRepository(self._session).get(card_id)
    if card is None or card.status != AcceptStatus.pending:
        return
    self._note_poll_failed(card, exc)
    await self._session.flush()
