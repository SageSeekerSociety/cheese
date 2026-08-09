"""Open a real GitHub PR for a green accept card (PR-based accept, #188 §5.1).

Dispatched fire-and-forget when a card turns `pending` (born pending on
projects without a gate, or promoted by a green gate). Pushes the topic branch
to the upstream and opens (or finds) the PR, then records pr_number/pr_url on
the card. Everything is best-effort: any failure leaves the card PR-less and
the accept path falls back to the local merge — GitHub being down must never
block acceptance.

Same task-reference pattern as review/gate.py.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.domain.agent.github_app import github_app_tokens
from app.domain.review.github_pr import GitHubPRClient, parse_github_repo
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.pr_publish")

_TASKS: set[asyncio.Task] = set()


def enabled() -> bool:
    """Cheap pre-check: the flag is on and the App is configured. The
    per-project eligibility (upstream is a GitHub https remote) is checked in
    the task itself — it needs a subprocess."""
    return bool(settings.accept_via_pr) and github_app_tokens() is not None


def dispatch(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Start the PR publication in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    try:
        pr = await _publish(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    except Exception:  # noqa: BLE001 — best-effort: card stays PR-less, accept falls back
        logger.exception("PR publication failed for card %s", card_id)
        return
    if pr is None:
        return
    await _record(session_factory, card_id=card_id, pr=pr)


async def _publish(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    tokens = github_app_tokens()
    if tokens is None:
        return None
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return None  # not a GitHub https upstream — PR path not applicable
    owner, repo_name = parsed

    token, _ = await tokens.write_token()
    branch = await asyncio.to_thread(ws.push_topic_branch, project_id, topic_id, token)
    base = (
        await asyncio.to_thread(
            lambda: ws.upstream_default_branch(ws.ensure_repo(project_id))
        )
        or ws.DEFAULT_BRANCH
    )

    title, body = await _pr_text(
        session_factory, card_id=card_id, topic_id=topic_id, branch=branch
    )
    client = GitHubPRClient(owner, repo_name, tokens)
    pr = await client.open_pr(head=branch, base=base, title=title, body=body)
    logger.info(
        "PR #%s ready for card %s (%s)", pr.get("number"), card_id, pr.get("html_url")
    )
    return pr


async def _pr_text(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    branch: str,
) -> tuple[str, str]:
    """PR title/body from the topic and card. Falls back to the branch name."""
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.topic.repositories import TopicRepository

    title = branch
    lines: list[str] = []
    async with session_factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        if topic is not None and topic.title:
            title = topic.title
        card = await AcceptCardRepository(session).get(card_id)
        if card is not None:
            lines.append(f"验收人：{card.reviewer_handle}")
            if card.routing_reason:
                lines.append(f"路由理由：{card.routing_reason}")
    lines.append(f"话题分支 `{branch}`，由平台递验收卡时自动创建（#188 采纳 PR 化）。")
    lines.append("采纳这张验收卡即合并本 PR。")
    return title, "\n\n".join(lines)


async def _record(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, pr: dict
) -> None:
    from app.domain.review.repositories import AcceptCardRepository

    async with session_factory() as session:
        card = await AcceptCardRepository(session).get(card_id)
        if card is None:
            logger.error("PR recorded nowhere: card %s vanished", card_id)
            return
        card.pr_number = int(pr["number"])
        card.pr_url = str(pr.get("html_url") or "")[:255] or None
        await session.commit()
