"""A 退回's comments, copied onto the task's PR.

The comments live on the platform and reach 芝士 with the 退回; the copy on the
PR is so whoever reads the PR (on GitHub, or the platform's own forge) sees the
same remarks next to the lines. It is a copy: posting it is best-effort, and a
PR that is gone or a forge that refuses only leaves a log line.

The forge posts as the platform's own account (the GitHub App, the project's
Forgejo account), so each comment names who wrote it. Every copy ends with
`PLATFORM_REVIEW_MARK`, which is how the PR poller knows not to hand 芝士 these
again as a reviewer speaking.
"""

import asyncio
import functools
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.forge_http import forge_client
from app.core.sentences import say
from app.domain.project.forge import proposal_client
from app.domain.review.comment_models import ReviewComment
from app.domain.review.github_pr import GitHubPRClient, GitHubPRError
from app.domain.review.models import AcceptCard
from app.domain.review.pr_signals import PLATFORM_REVIEW_MARK
from app.domain.topic.models import Topic

logger = logging.getLogger(__name__)

_TASKS: set[asyncio.Task] = set()


def comment_text(comment: ReviewComment) -> str:
    """One comment as the PR shows it: who, what, and the suggested lines."""
    parts = [f"**{comment.author_handle}**：{comment.body}".rstrip("：")]
    if comment.suggestion is not None:
        parts.append(f"```suggestion\n{comment.suggestion}\n```")
    parts.append(PLATFORM_REVIEW_MARK)
    return "\n\n".join(parts)


async def _post_github_review(
    client: GitHubPRClient,
    number: int,
    *,
    commit_id: str | None,
    body: str,
    comments: list[dict],
) -> None:
    """One review of comments on lines, or, when GitHub refuses a line that is
    not in the PR's diff, the same review with every comment written into its
    body."""
    token, _ = await client.tokens.write_token()
    inline = [
        {
            "path": c["path"],
            "line": c["line"],
            "side": "RIGHT",
            "body": c["body"],
            **(
                {"start_line": c["start_line"], "start_side": "RIGHT"}
                if c.get("start_line") and c["start_line"] < c["line"]
                else {}
            ),
        }
        for c in comments
    ]
    payload: dict = {"event": "COMMENT", "body": body, "comments": inline}
    if commit_id:
        payload["commit_id"] = commit_id
    url = client._url(f"/pulls/{number}/reviews")
    async with forge_client(transport=client._transport, timeout=30.0) as http:
        resp = await http.post(url, json=payload, headers=client._headers(token))
        if resp.status_code == 422 and inline:
            folded = "\n\n".join(
                [body, *(f"`{c['path']}:{c['line']}`\n{c['body']}" for c in comments)]
            )
            resp = await http.post(
                url,
                json={"event": "COMMENT", "body": folded},
                headers=client._headers(token),
            )
    if resp.status_code not in (200, 201):
        raise GitHubPRError(
            say(
                "githubReviewPostFailed", status=resp.status_code, reply=resp.text[:300]
            )
        )


def dispatch(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, reason: str
) -> None:
    """Copy the comments sent back with this card onto its PR, in the background."""
    task = asyncio.create_task(_run(session_factory, card_id=card_id, reason=reason))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, reason: str
) -> None:
    try:
        async with session_factory() as session:
            card = await session.get(AcceptCard, card_id)
            if card is None or card.pr_number is None:
                return
            rows = list(
                await session.scalars(
                    select(ReviewComment)
                    .where(ReviewComment.card_id == card_id)
                    .order_by(ReviewComment.path, ReviewComment.line_start)
                )
            )
            if not rows:
                return
            topic = await session.get(Topic, card.topic_id)
            if topic is None:
                return
            client = await proposal_client(topic.project_id, session)
            if client is None:
                return
            body = "\n\n".join(
                part
                for part in (
                    f"**{card.decided_by}** 退回了这次交付。",
                    reason,
                    PLATFORM_REVIEW_MARK,
                )
                if part
            )
            post = (
                functools.partial(_post_github_review, client)
                if isinstance(client, GitHubPRClient)
                else client.post_review
            )
            await post(
                card.pr_number,
                commit_id=card.pr_head_sha,
                body=body,
                comments=[
                    {
                        "path": row.path,
                        "line": row.line_end,
                        "start_line": row.line_start,
                        "body": comment_text(row),
                    }
                    for row in rows
                ],
            )
    except Exception:  # noqa: BLE001 — a copy that failed changes nothing the platform holds
        logger.exception("copying the comments of card %s onto its PR failed", card_id)
