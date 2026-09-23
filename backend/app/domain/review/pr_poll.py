"""Polling the forge, because a forge without a webhook tells us nothing.

结论 16: PR / 上游轮询是托管方实现在没有 webhook 时的内部物理事实——不是调度，是
review 领域自己补上的一条读路径。产出落各自的卡和房间，和 webhook 送来的事件走同
一条路 (`forge_repository_changed`)。

So this is the review domain's own clock. `app/core/background.py` decides how
often it turns; everything about WHAT a tick does is here and in the services it
calls.
"""

import logging
import uuid

import httpx
from sqlalchemy import select

from app.domain.agent.chat import ChatService

logger = logging.getLogger("cheesex.review.pr_poll")

# A poller against a network misses sometimes: a DNS blip, the connection owner
# restarting mid-release, a TLS handshake that never finished. The first miss is
# not news — the next tick is a minute away and usually fixes it — and reporting
# each one as an error made 39 of the alert channel's first 600 messages, none
# of which anybody acted on. What IS news is that the retries are not working,
# so a card has to miss this many ticks in a row before it is reported as an
# error. Everything else still fails loudly on the first occurrence: a bug in
# the poller is not something a later tick repairs.
TRANSIENT_MISSES_BEFORE_ERROR = 3

#: Consecutive ticks each card has lost to the network, so that a blip and an
#: outage do not read the same. Module level because the clock that drives this
#: is process-wide: one backend, one count per card.
_transient_misses: dict[uuid.UUID, int] = {}


async def poll_open_prs(chat: ChatService, project_id: uuid.UUID | None = None) -> dict:
    """Reconcile returned batches and advance pending PR cards (#718) one step —
    mirror its merge state, send the events the 「谁的活」 table names, and merge
    an armed auto-merge card whose rules are satisfied
    (`AcceptService.advance_pr_card`). One DB transaction per card so one card's
    failure can't roll back another's progress."""
    from app.api.deps import get_work_runner
    from app.domain.review.services import AcceptService

    sessions = chat.session_factory
    runner = get_work_runner()
    checked = 0
    errors: list[str] = []
    async with sessions() as session:
        # 孤儿卡修复 (2026-08-10): cards on ARCHIVED topics are deliberately NOT
        # in this list — driving them means using the approver's GitHub token on
        # work nobody tracks any more. 那条判据留在 review 领域里
        # （open_pr_card_ids），这里只管拿 id。
        card_ids = await AcceptService(session).open_pr_card_ids(project_id)
    for card_id in card_ids:
        async with sessions() as session:
            try:
                await AcceptService(session).advance_pr_card(
                    card_id, chat_service=chat, runner=runner
                )
                await session.commit()
                checked += 1
                _transient_misses.pop(card_id, None)
            except Exception as exc:  # noqa: BLE001 — one card must not stop the rest
                await session.rollback()
                errors.append(f"{card_id}: {exc}")
                _report_card_failure(card_id, exc)
                await _note_card_poll_crashed(chat, card_id, exc)
    return {"cards_checked": checked, "errors": errors}


def _report_card_failure(card_id: uuid.UUID, exc: BaseException) -> None:
    """Loudly, unless the network is the only thing that went wrong and the
    retries have not yet run out of excuses."""
    if not isinstance(exc, httpx.TransportError):
        _transient_misses.pop(card_id, None)
        logger.exception("poll_open_prs failed for card %s", card_id)
        return
    misses = _transient_misses.get(card_id, 0) + 1
    _transient_misses[card_id] = misses
    if misses < TRANSIENT_MISSES_BEFORE_ERROR:
        logger.warning(
            "poll_open_prs could not reach the network for card %s "
            "(%d in a row, reporting at %d): %r",
            card_id,
            misses,
            TRANSIENT_MISSES_BEFORE_ERROR,
            exc,
        )
        return
    logger.exception(
        "poll_open_prs has been unable to reach the network for card %s "
        "for %d ticks in a row",
        card_id,
        misses,
    )


async def _note_card_poll_crashed(
    chat: ChatService, card_id: uuid.UUID, exc: BaseException
) -> None:
    """Leave the crash on the card, in its own transaction.

    The rollback above throws away everything the failed tick wrote — which is
    right for the state machine and wrong for the reader: the card keeps showing
    whatever it said before, usually 「等 CI」, while every tick dies the same
    way. A person watching a green PR that never merges has no way to tell that
    apart from slow checks. So the explanation is written by a SEPARATE session
    that the rollback cannot take with it.

    Best-effort by construction: if even this write fails, the log line above is
    still there and the poll loop keeps going.
    """
    from app.domain.review.services import AcceptService

    try:
        async with chat.session_factory() as session:
            await AcceptService(session).note_poll_crashed(card_id, exc)
            await session.commit()
    except Exception:  # noqa: BLE001 — never let the explanation kill the loop
        logger.exception("could not record poll failure on card %s", card_id)


async def open_draft_prs(chat: ChatService) -> dict:
    """有东西就有 PR (#718 拍板①): give every batch with commits a draft PR,
    without waiting for anyone to file a card.

    The observation and every reason it is an observation rather than a hook
    live in `pr_publish.sweep_draft_prs`; this is only the clock.
    """
    from app.domain.review import pr_publish

    result = dict(await pr_publish.sweep_draft_prs(chat.session_factory))
    await deliver_dependency_notices(chat)
    return result


async def deliver_dependency_notices(chat: ChatService) -> None:
    """Dispatch committed task-parent intent through the delivery ledger."""
    from app.api.deps import get_work_runner
    from app.domain.delivery.agent import dispatch_pending

    await dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())


async def forge_repository_changed(
    chat: ChatService, kind: str, repo: str, project_id: str | None = None
) -> None:
    """One repository moved — from a relayed forge event, or from a reconnect."""
    from app.domain.project.models import ProjectForge
    from app.domain.review.pr_publish import sweep_draft_prs

    sessions = chat.session_factory
    query = select(ProjectForge.project_id).where(
        ProjectForge.kind == kind, ProjectForge.repo == repo
    )
    if project_id is not None:
        query = query.where(ProjectForge.project_id == uuid.UUID(project_id))
    async with sessions() as session:
        projects = list(await session.scalars(query))
    for changed_project_id in projects:
        await poll_open_prs(chat, changed_project_id)
        await sweep_draft_prs(sessions, changed_project_id)
    await deliver_dependency_notices(chat)
