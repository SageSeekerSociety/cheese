from typing import TYPE_CHECKING

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.comments.models import Comment
from app.domain.comments.repositories import CommentRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository

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


_EMPTY_ATTITUDES: dict = {
    "positive_count": 0,
    "negative_count": 0,
    "difference": 0,
    "user_attitude": "UNDEFINED",
}


class CommentService:
    def __init__(
        self,
        repo: CommentRepository,
        user_repo: UserRepository | None = None,
        profile_repo: UserProfileRepository | None = None,
    ) -> None:
        self._repo = repo
        self._user_repo = user_repo
        self._profile_repo = profile_repo

    async def list_comments(
        self,
        *,
        commentable_type: str,
        commentable_id: int,
        page_start: int | None,
        page_size: int,
        viewer_id: int | None = None,
    ) -> tuple[list[dict], dict]:
        offset = page_start or 0
        comments, total = await self._repo.list_comments(
            commentable_type=commentable_type,
            commentable_id=commentable_id,
            limit=page_size,
            offset=offset,
        )
        items = [_comment_to_dto(c) for c in comments]
        items = await self._enrich_comment_dtos(
            items, viewer_id=viewer_id, with_sub_comments=True
        )
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

    async def get_comment(self, comment_id: int, viewer_id: int | None = None) -> dict:
        comment = await self._repo.get_by_id(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found", data={"id": comment_id})
        dto = _comment_to_dto(comment)
        enriched = await self._enrich_comment_dtos(
            [dto], viewer_id=viewer_id, with_sub_comments=False
        )
        return enriched[0]

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
        dto = _comment_to_dto(comment)
        enriched = await self._enrich_comment_dtos(
            [dto], viewer_id=user_id, with_sub_comments=False
        )
        return enriched[0]

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

    # ------------------------------------------------------------------ #
    # Enrichment helpers — populate `user`, `attitudes`, `sub_comments`  #
    # so the response matches the frontend `Comment` type contract.      #
    # ------------------------------------------------------------------ #

    async def _enrich_comment_dtos(
        self,
        items: list[dict],
        *,
        viewer_id: int | None,
        with_sub_comments: bool,
    ) -> list[dict]:
        if not items:
            return items

        comment_ids = [it["id"] for it in items]

        # 1. attitudes (counts + viewer's own attitude).
        votes_map = await self._repo.bulk_count_votes(comment_ids)
        user_votes_map: dict[int, str] = {}
        if viewer_id is not None and viewer_id > 0:
            user_votes_map = await self._repo.bulk_get_user_votes(comment_ids, viewer_id)

        # 2. user (author) profiles.
        from app.domain.user.models import User, UserProfile

        author_ids = list({it["created_by_id"] for it in items})
        users_map: dict[int, User] = {}
        profiles_map: dict[int, UserProfile] = {}
        if self._user_repo is not None and author_ids:
            users_map = await self._user_repo.get_by_ids(author_ids)
        if self._profile_repo is not None and author_ids:
            profiles_map = await self._profile_repo.get_profiles_by_user_ids(author_ids)

        # 3. sub-comments (one-level deep, IDs only). Frontend's CommentBox
        #    iterates over `comment.sub_comments` for nested rendering.
        sub_map: dict[int, list[dict]] = {}
        if with_sub_comments:
            sub_rows = await self._repo.list_sub_comments(comment_ids)
            sub_dtos_flat: list[dict] = []
            for parent_id, rows in sub_rows.items():
                child_dtos = [_comment_to_dto(r) for r in rows]
                sub_map[parent_id] = child_dtos
                sub_dtos_flat.extend(child_dtos)
            # Recurse one level so nested user/attitudes are also populated
            # (sub-comments themselves don't fetch grand-children to avoid loops).
            if sub_dtos_flat:
                await self._enrich_comment_dtos(
                    sub_dtos_flat, viewer_id=viewer_id, with_sub_comments=False
                )

        for item in items:
            cid = item["id"]
            counts = votes_map.get(cid, {"POSITIVE": 0, "NEGATIVE": 0})
            user_vote = user_votes_map.get(cid)
            item["attitudes"] = {
                "positive_count": counts.get("POSITIVE", 0),
                "negative_count": counts.get("NEGATIVE", 0),
                "difference": counts.get("POSITIVE", 0) - counts.get("NEGATIVE", 0),
                "user_attitude": user_vote or "UNDEFINED",
            }

            author_id = item["created_by_id"]
            user_obj = users_map.get(author_id)
            profile = profiles_map.get(author_id)
            item["user"] = _build_user_dto(user_obj, profile, fallback_id=author_id)

            if with_sub_comments:
                item["sub_comments"] = sub_map.get(cid, [])

        return items


def _build_user_dto(user_obj, profile, *, fallback_id: int) -> dict:
    """Produce a User-shaped dict with the fields the frontend type expects."""
    if user_obj is not None:
        nickname = (
            profile.nickname if profile and getattr(profile, "nickname", None) else user_obj.username
        )
        avatar_id = profile.avatar_id if profile else None
        intro = profile.intro if profile else ""
        return {
            "id": user_obj.id,
            "username": user_obj.username,
            "nickname": nickname,
            "avatarId": avatar_id,
            "intro": intro,
            "follow_count": 0,
            "fans_count": 0,
            "question_count": 0,
            "answer_count": 0,
        }
    # Author no longer exists (deleted user). Frontend tolerates missing
    # nested fields via optional chaining for most paths; the placeholder keeps
    # the shape so direct accesses don't crash.
    return {
        "id": fallback_id,
        "username": "",
        "nickname": "",
        "avatarId": None,
        "intro": "",
        "follow_count": 0,
        "fans_count": 0,
        "question_count": 0,
        "answer_count": 0,
    }
