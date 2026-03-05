from typing import TYPE_CHECKING

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.comments.models import Comment
from app.domain.comments.repositories import CommentRepository

if TYPE_CHECKING:
    pass


def _comment_to_dto(comment: Comment) -> dict:
    created_at_ms = int(comment.created_at.timestamp() * 1000) if comment.created_at else 0
    updated_at_ms = int(comment.updated_at.timestamp() * 1000) if comment.updated_at else 0
    return {
        "id": comment.id,
        "commentable_type": comment.commentable_type,
        "commentable_id": comment.commentable_id,
        "content": comment.content,
        "created_by_id": comment.created_by_id,
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
    }


class CommentService:
    def __init__(self, repo: CommentRepository) -> None:
        self._repo = repo

    async def list_comments(
        self,
        *,
        commentable_type: str,
        commentable_id: int,
        page_start: int | None,
        page_size: int,
    ) -> tuple[list[dict], dict]:
        offset = page_start or 0
        comments, total = await self._repo.list_comments(
            commentable_type=commentable_type,
            commentable_id=commentable_id,
            limit=page_size,
            offset=offset,
        )
        items = [_comment_to_dto(c) for c in comments]
        returned = len(items)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return items, page

    async def get_comment(self, comment_id: int) -> dict:
        comment = await self._repo.get_by_id(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found", data={"id": comment_id})
        return _comment_to_dto(comment)

    async def create_comment(
        self,
        *,
        commentable_type: str,
        commentable_id: int,
        content: str,
        created_by_id: int,
    ) -> dict:
        comment = await self._repo.create(
            commentable_type=commentable_type,
            commentable_id=commentable_id,
            content=content,
            created_by_id=created_by_id,
        )
        return {"id": comment.id}

    async def update_comment(
        self,
        *,
        comment_id: int,
        user_id: int,
        content: str,
    ) -> dict:
        comment = await self._repo.get_by_id(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found", data={"id": comment_id})
        if comment.created_by_id != user_id:
            raise ForbiddenError("Only the author can update the comment")
        await self._repo.update(comment, content=content)
        return _comment_to_dto(comment)

    async def delete_comment(self, *, comment_id: int, user_id: int) -> None:
        comment = await self._repo.get_by_id(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found", data={"id": comment_id})
        if comment.created_by_id != user_id:
            raise ForbiddenError("Only the author can delete the comment")
        await self._repo.soft_delete(comment)

    async def _ensure_comment_exists(self, comment_id: int) -> Comment:
        comment = await self._repo.get_by_id(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found", data={"id": comment_id})
        return comment

    async def vote_comment(self, *, comment_id: int, user_id: int, vote_type: str) -> dict:
        from app.core.errors import BadRequestError

        await self._ensure_comment_exists(comment_id)
        if vote_type not in ("POSITIVE", "NEGATIVE"):
            raise BadRequestError("Invalid vote type", data={"vote_type": vote_type})
        await self._repo.vote(comment_id=comment_id, user_id=user_id, vote_type=vote_type)
        counts = await self._repo.count_votes(comment_id)
        return {
            "upvotes": counts.get("POSITIVE", 0),
            "downvotes": counts.get("NEGATIVE", 0),
            "userVote": vote_type,
        }

    async def remove_comment_vote(self, *, comment_id: int, user_id: int) -> dict:
        await self._ensure_comment_exists(comment_id)
        await self._repo.remove_vote(comment_id=comment_id, user_id=user_id)
        counts = await self._repo.count_votes(comment_id)
        return {
            "upvotes": counts.get("POSITIVE", 0),
            "downvotes": counts.get("NEGATIVE", 0),
            "userVote": None,
        }

    async def get_comment_votes(self, *, comment_id: int, user_id: int | None) -> dict:
        await self._ensure_comment_exists(comment_id)
        counts = await self._repo.count_votes(comment_id)
        user_vote = None
        if user_id:
            user_vote = await self._repo.get_user_vote(comment_id, user_id)
        return {
            "upvotes": counts.get("POSITIVE", 0),
            "downvotes": counts.get("NEGATIVE", 0),
            "userVote": user_vote,
        }
