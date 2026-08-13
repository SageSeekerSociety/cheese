"""知是 tag services.

The error text below still says "Topic", on purpose and by the same rule as the
JSON keys: 知是 shows these strings to people, who see 话题 in the UI. #370 renamed
the code and the address, not the product's vocabulary.
"""

from app.core.errors import ConflictError, NotFoundError
from app.domain.tag.models import Tag
from app.domain.tag.repositories import TagRepository


def _tag_to_dto(tag: Tag) -> dict:
    created_at_ms = int(tag.created_at.timestamp() * 1000) if tag.created_at else 0
    return {
        "id": tag.id,
        "name": tag.name,
        "createdById": tag.created_by_id,
        "createdAt": created_at_ms,
    }


class TagService:
    def __init__(self, repo: TagRepository) -> None:
        self._repo = repo

    async def list_tags(
        self,
        *,
        keyword: str | None = None,
        page_start: int | None,
        page_size: int,
    ) -> tuple[list[dict], dict]:
        if not keyword or not keyword.strip():
            page = {
                "pageSize": 0,
                "pageStart": 0,
                "hasPrev": False,
                "prevStart": 0,
                "hasMore": False,
                "nextStart": 0,
            }
            return [], page

        tags, prev_id, has_more, next_id = await self._repo.list_tags_cursor(
            keyword=keyword, page_start=page_start, page_size=page_size
        )
        items = [_tag_to_dto(t) for t in tags]
        returned = len(items)

        first_id = tags[0].id if tags else 0
        has_prev = prev_id is not None

        page = {
            "pageSize": returned,
            "pageStart": first_id,
            "hasPrev": has_prev,
            "prevStart": prev_id if prev_id else 0,
            "hasMore": has_more,
            "nextStart": next_id if next_id else 0,
        }
        return items, page

    async def get_tag(self, tag_id: int) -> dict:
        tag = await self._repo.get_by_id(tag_id)
        if tag is None:
            raise NotFoundError("Topic not found", data={"id": tag_id})
        return _tag_to_dto(tag)

    async def create_tag(self, *, name: str, created_by_id: int) -> dict:
        existing = await self._repo.get_by_name(name)
        if existing is not None:
            raise ConflictError("Topic already exists", data={"name": name})
        tag = await self._repo.create(name=name, created_by_id=created_by_id)
        return {"id": tag.id}
