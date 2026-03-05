from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.knowledge.services import KnowledgeService


def make_entity(**overrides):
    defaults = {
        "id": 1,
        "team_id": 10,
        "name": "Knowledge",
        "description": "Desc",
        "type": "TEXT",
        "content": {},
        "project_id": None,
        "discussion_id": None,
        "material_id": None,
        "created_by": 99,
        "created_at": __import__("datetime").datetime.utcnow(),
        "updated_at": __import__("datetime").datetime.utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.anyio
async def test_upvote_adds_record_and_returns_dto():
    repo = AsyncMock()
    team_repo = AsyncMock()
    team_repo.is_team_member.return_value = True

    entity = make_entity(id=42)
    repo.get_by_id.return_value = entity
    repo.add_upvote.return_value = None
    repo.get_upvote_count.return_value = 1
    repo.get_labels_map.return_value = {42: ["label"]}
    repo.get_upvote_count.return_value = 1
    repo.has_upvote.return_value = True
    repo.list_user_upvotes.return_value = {42}

    service = KnowledgeService(repo=repo, team_repo=team_repo)

    dto = await service.upvote(knowledge_id=42, user_id=7)

    repo.add_upvote.assert_awaited_once_with(42, 7)
    assert dto["upvoteCount"] == 1
    assert dto["isUpvoted"] is True


@pytest.mark.anyio
async def test_update_changes_fields():
    repo = AsyncMock()
    team_repo = AsyncMock()
    team_repo.is_team_member.return_value = True

    entity = make_entity(id=5)
    repo.get_by_id.return_value = entity
    updated = make_entity(id=5, name="Updated", description="New", content={"text": "hi"})
    repo.update_entity.return_value = updated
    repo.get_labels_map.return_value = {5: []}
    repo.get_upvote_count.return_value = 0
    repo.has_upvote.return_value = False

    service = KnowledgeService(repo=repo, team_repo=team_repo)

    dto = await service.update(
        knowledge_id=5,
        user_id=9,
        name="Updated",
        description="New",
        content={"text": "hi"},
    )

    repo.update_entity.assert_awaited_once()
    assert dto["name"] == "Updated"
    assert dto["description"] == "New"
