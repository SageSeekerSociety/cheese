"""A task's review comments as one reader sees them.

`review_comment_rows` is the one function that builds them: `GET
/topics/{task}/review-comments` returns them, and the `review_comments` of a
task's room snapshot (`app.api.room_snapshot`) embeds the same ones, so the
批注 panel drawn from a subscription and the one the route answers with cannot
disagree about where a comment's lines are.

It lives under `app.api`, not in `routes/review_comments.py`, because the
snapshot asks for it too and is not a route: left beside the route, it would
make `room_snapshot` reach a route — C4 (`routes-independent`).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.comments import ReviewCommentService, describe


async def review_comment_rows(db: AsyncSession, task, viewer: str) -> dict:
    """A task's review comments as `viewer` reads them; also the
    `review_comments` of a task's room snapshot. The caller has authorised the
    reader."""
    svc = ReviewCommentService(db)
    rows = await svc.comments(task.id, viewer)
    anchor = await svc.anchors(task, rows)
    return {"comments": [describe(row, anchor) for row in rows]}
