"""Filling in who merged a card that landed before the platform recorded it.

The poller writes `AcceptCard.merged_by` when it watches a PR merge. Cards
merged before that existed have no merger on them, and the project overview's
「最近进展」 shows those accepts without a name. This is the backfill: one
GitHub read per card, only for the window that overview still shows, and only
until each card has an answer (`{}` counts as one — see `merge_actor`). It
runs once per process at takeover (`pr_poll.backfill_missing_merge_actors`),
not on a clock.
"""

from __future__ import annotations

import uuid

from app.domain.project.forge import background_quota, quota_serves_background
from app.domain.review import services as pkg
from app.domain.review.github_pr_status import merge_actor
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.services._shared import logger
from app.domain.topic.models import Topic


async def fill_merged_by(self: pkg.AcceptService, card_id: uuid.UUID) -> bool:
    """Read who merged this accepted card's PR and record it.

    False when there is nothing left to do (already answered, decided on the
    platform, never merged, no PR) or when the read could not be made — the
    next process start asks again, and a GitHub hiccup is not an error the
    progress line should turn into one. One card per transaction, as in the
    open-card poll: the forge read must not be made under another card's row.
    """
    card = await self._session.get(AcceptCard, card_id, populate_existing=True)
    if (
        card is None
        or card.status != AcceptStatus.accepted
        or card.decided_by is not None
        or card.merged_by is not None
        or card.pr_number is None
    ):
        return False
    topic = await self._session.get(Topic, card.topic_id)
    if topic is None:
        return False
    budget = await background_quota(topic.project_id, self._session)
    if not await quota_serves_background(budget):
        return False
    try:
        owner, repo = await self._pr_repo_of(card, topic)
    except Exception as exc:  # no binding, repo gone, renamed away
        logger.warning("card %s: cannot resolve its PR repository: %s", card.id, exc)
        return False
    creds, reason = await self._pr_poll_credentials(card, topic)
    if creds is None:
        logger.warning("card %s: no GitHub credentials (%s)", card.id, reason)
        return False
    client = await self._status_client(topic.project_id)
    if client is None:
        return False
    status = await client.pull_request_status(
        owner=owner, repo=repo, number=card.pr_number, token=creds.read
    )
    if not status.merged:
        return False
    card.merged_by = merge_actor(status)
    await self._session.flush()
    return True
