from app.core.errors import ConflictError, NotFoundError
from app.domain.topics.models import Topic
from app.domain.topics.repositories import TopicRepository


def _topic_to_dto(topic: Topic) -> dict:
    created_at_ms = int(topic.created_at.timestamp() * 1000) if topic.created_at else 0
    return {
        "id": topic.id,
        "name": topic.name,
        "createdById": topic.created_by_id,
        "createdAt": created_at_ms,
    }


class TopicService:
    def __init__(self, repo: TopicRepository) -> None:
        self._repo = repo

    async def list_topics(
        self,
        *,
        keyword: str | None = None,
        page_start: int | None,
        page_size: int,
    ) -> tuple[list[dict], dict]:
        if not keyword or not keyword.strip():
            page = {
                "page_size": 0,
                "page_start": 0,
                "has_prev": False,
                "prev_start": 0,
                "has_more": False,
                "next_start": 0,
            }
            return [], page

        topics, prev_id, has_more, next_id = await self._repo.list_topics_cursor(
            keyword=keyword, page_start=page_start, page_size=page_size
        )
        items = [_topic_to_dto(t) for t in topics]
        returned = len(items)

        first_id = topics[0].id if topics else 0
        has_prev = prev_id is not None

        page = {
            "page_size": returned,
            "page_start": first_id,
            "has_prev": has_prev,
            "prev_start": prev_id if prev_id else 0,
            "has_more": has_more,
            "next_start": next_id if next_id else 0,
        }
        return items, page

    async def get_topic(self, topic_id: int) -> dict:
        topic = await self._repo.get_by_id(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found", data={"id": topic_id})
        return _topic_to_dto(topic)

    async def create_topic(self, *, name: str, created_by_id: int) -> dict:
        existing = await self._repo.get_by_name(name)
        if existing is not None:
            raise ConflictError("Topic already exists", data={"name": name})
        topic = await self._repo.create(name=name, created_by_id=created_by_id)
        return {"id": topic.id}
