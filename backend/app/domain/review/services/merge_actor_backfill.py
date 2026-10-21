"""Reaching back for the merge actor on cards that settled without a click.

A card merged directly on GitHub settles with no `decided_by`: nobody accepted
it in the platform. New merges record GitHub's `merged_by` as they settle
(`services/accept._settle_external_merge`); this sweep reaches the cards that
were already settled, once each, so the recent-progress feed can name who
handled a delivery instead of leaving the row blank. What is written here is
GitHub's own (account id, login) — turning that into a platform name belongs
to the feed, which sits above both domains that know the two halves.

Small on purpose: one GitHub read per card, a handful per tick. The feed only
covers two weeks, so the backlog is bounded by that window and drains in a few
minutes. An empty answer is written back as "" so a card is asked only once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.project.forge import (
    background_quota,
    binding_for_project,
    quota_serves_background,
)
from app.domain.review import services as pkg
from app.domain.review.models import AcceptCard
from app.domain.review.services._shared import logger

#: 最近进展往回看两周（`project_progress/feed.py::WINDOW`）；补录也只补这一段。
WINDOW = timedelta(days=14)

#: 一拍补几张。每张一次 GitHub 读，和 PR 轮询共用同一份额度，所以取得很小。
BATCH = 5


async def backfill_merge_actors(
    self: pkg.AcceptService,
    *,
    limit: int = BATCH,
    project_id: uuid.UUID | None = None,
) -> int:
    """Give up to `limit` recently settled external-merge cards a merge actor.

    Returns how many cards were answered (a name, or "" for "nobody to name").
    Never raises for one card: a forge hiccup leaves that card for the next
    tick and costs the others nothing.
    """
    since = datetime.now(UTC) - WINDOW
    card_ids = await self._repo.recent_accepted_without_merge_actor(
        since=since, limit=limit, project_id=project_id
    )
    self._commits_before_remote = True
    answered = 0
    try:
        for card_id in card_ids:
            card = await self._session.get(AcceptCard, card_id, populate_existing=True)
            if card is None or card.pr_number is None:
                continue
            topic = await self._topic_or_404(card.topic_id)
            try:
                binding = await binding_for_project(topic.project_id, self._session)
            except Exception:
                logger.warning(
                    "merge actor backfill: card %s has no readable forge binding",
                    card_id,
                    exc_info=True,
                )
                continue
            if binding is None or binding.kind != "github_app":
                # Nothing on another forge reports a GitHub account; record the
                # checked answer so this card stops coming back.
                card.pr_merged_by_login = ""
                await self._session.commit()
                answered += 1
                continue
            try:
                owner, repo = await self._pr_repo_of(card, topic)
                creds, _why = await self._pr_poll_credentials(card, topic)
                if creds is None:
                    continue
                quota = await background_quota(topic.project_id, self._session)
                if not await quota_serves_background(quota):
                    continue
                client = await self._status_client(topic.project_id)
                status = await client.pull_request_status(
                    owner=owner,
                    repo=repo,
                    number=card.pr_number,
                    token=creds.read,
                )
            except Exception:
                logger.warning(
                    "merge actor backfill: card %s: forge read failed",
                    card_id,
                    exc_info=True,
                )
                continue
            card.pr_merged_by_id = status.merged_by_id
            card.pr_merged_by_login = status.merged_by_login or ""
            await self._session.flush()
            await self._session.commit()
            answered += 1
    finally:
        self._commits_before_remote = False
    return answered
