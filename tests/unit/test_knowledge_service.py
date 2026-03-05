"""Unit tests for KnowledgeService."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.knowledge.services import KnowledgeService

NOW = datetime(2025, 6, 1, 12, 0, 0)
NOW_MS = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity(**overrides):
    defaults = {
        "id": 1,
        "name": "Knowledge Item",
        "description": "A description",
        "type": "TEXT",
        "content": {"body": "some content"},
        "team_id": 10,
        "project_id": None,
        "discussion_id": None,
        "material_id": None,
        "created_by": 99,
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_service(repo=None, team_repo=None):
    repo = repo or AsyncMock()
    team_repo = team_repo or AsyncMock()
    team_repo.is_team_member.return_value = True
    return KnowledgeService(repo=repo, team_repo=team_repo)


def _stub_dto_deps(repo, entity, labels=None, upvote_count=0, has_upvote=False):
    """Stub the repository calls needed by _build_dto."""
    repo.get_labels_map.return_value = {entity.id: labels or []}
    repo.get_upvote_count.return_value = upvote_count
    repo.has_upvote.return_value = has_upvote


def _stub_dtos_deps(repo, entities, label_map=None, count_map=None, user_upvotes=None):
    """Stub the repository calls needed by _build_dtos."""
    repo.get_labels_map.return_value = label_map or {}
    repo.get_upvote_counts.return_value = count_map or {}
    repo.list_user_upvotes.return_value = user_upvotes or set()


# ===========================================================================
# create
# ===========================================================================


class TestCreate:
    @pytest.mark.anyio
    async def test_create_success(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = True

        entity = _entity(id=1)
        repo.create.return_value = entity
        _stub_dto_deps(repo, entity, labels=["python"], upvote_count=0)

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        result = await svc.create(
            name="Knowledge Item",
            type_="TEXT",
            content={"body": "some content"},
            description="A description",
            team_id=10,
            created_by=99,
            labels=["python"],
            material_id=None,
            project_id=None,
            discussion_id=None,
        )

        assert result["id"] == 1
        assert result["name"] == "Knowledge Item"
        assert result["labels"] == ["python"]
        assert result["teamId"] == 10
        assert result["createdBy"] == 99
        team_repo.is_team_member.assert_awaited_once_with(10, 99)
        repo.create.assert_awaited_once()

    @pytest.mark.anyio
    async def test_create_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.create(
                name="Item",
                type_="TEXT",
                content={},
                description=None,
                team_id=10,
                created_by=99,
                labels=[],
                material_id=None,
                project_id=None,
                discussion_id=None,
            )


# ===========================================================================
# find_all
# ===========================================================================


class TestFindAll:
    @pytest.mark.anyio
    async def test_find_all_success(self):
        repo = AsyncMock()
        entities = [_entity(id=1), _entity(id=2, name="Other")]
        repo.find_all.return_value = (entities, 2)
        _stub_dtos_deps(
            repo,
            entities,
            label_map={1: ["a"], 2: ["b"]},
            count_map={1: 3, 2: 0},
            user_upvotes={1},
        )

        svc = _build_service(repo=repo)

        dtos, total = await svc.find_all(
            team_id=10,
            user_id=99,
            project_id=None,
            type_=None,
            labels=None,
            query=None,
            limit=20,
            offset=0,
            sort_by="createdAt",
            sort_order="desc",
        )

        assert total == 2
        assert len(dtos) == 2
        assert dtos[0]["labels"] == ["a"]
        assert dtos[0]["upvoteCount"] == 3
        assert dtos[0]["isUpvoted"] is True
        assert dtos[1]["upvoteCount"] == 0
        assert dtos[1]["isUpvoted"] is False

    @pytest.mark.anyio
    async def test_find_all_empty_result(self):
        repo = AsyncMock()
        repo.find_all.return_value = ([], 0)
        _stub_dtos_deps(repo, [])

        svc = _build_service(repo=repo)

        dtos, total = await svc.find_all(
            team_id=10,
            user_id=99,
            project_id=None,
            type_=None,
            labels=None,
            query=None,
            limit=20,
            offset=0,
            sort_by="createdAt",
            sort_order="desc",
        )

        assert total == 0
        assert dtos == []

    @pytest.mark.anyio
    async def test_find_all_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.find_all(
                team_id=10,
                user_id=99,
                project_id=None,
                type_=None,
                labels=None,
                query=None,
                limit=20,
                offset=0,
                sort_by="createdAt",
                sort_order="desc",
            )


# ===========================================================================
# get
# ===========================================================================


class TestGet:
    @pytest.mark.anyio
    async def test_get_success(self):
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        _stub_dto_deps(repo, entity, labels=["tag1"], upvote_count=2, has_upvote=True)

        svc = _build_service(repo=repo)

        result = await svc.get(knowledge_id=5, user_id=99)

        assert result["id"] == 5
        assert result["labels"] == ["tag1"]
        assert result["upvoteCount"] == 2
        assert result["isUpvoted"] is True
        repo.get_by_id.assert_awaited_once_with(5)

    @pytest.mark.anyio
    async def test_get_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.get(knowledge_id=999, user_id=99)

    @pytest.mark.anyio
    async def test_get_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.get(knowledge_id=5, user_id=99)


# ===========================================================================
# delete
# ===========================================================================


class TestDelete:
    @pytest.mark.anyio
    async def test_delete_success(self):
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        repo.soft_delete.return_value = True

        svc = _build_service(repo=repo)

        await svc.delete(knowledge_id=5, user_id=99)

        repo.soft_delete.assert_awaited_once_with(5)

    @pytest.mark.anyio
    async def test_delete_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.delete(knowledge_id=999, user_id=99)

    @pytest.mark.anyio
    async def test_delete_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.delete(knowledge_id=5, user_id=99)

    @pytest.mark.anyio
    async def test_delete_soft_delete_returns_false(self):
        """Race condition: entity fetched but soft_delete fails."""
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        repo.soft_delete.return_value = False

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.delete(knowledge_id=5, user_id=99)


# ===========================================================================
# update
# ===========================================================================


class TestUpdate:
    @pytest.mark.anyio
    async def test_update_all_fields(self):
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        updated = _entity(id=5, name="Updated", description="New desc", content={"text": "hi"})
        repo.update_entity.return_value = updated
        _stub_dto_deps(repo, updated, labels=["new-label"])

        svc = _build_service(repo=repo)

        result = await svc.update(
            knowledge_id=5,
            user_id=99,
            name="Updated",
            description="New desc",
            content={"text": "hi"},
            labels=["new-label"],
        )

        assert result["name"] == "Updated"
        assert result["description"] == "New desc"
        repo.update_entity.assert_awaited_once_with(
            entity=entity,
            name="Updated",
            description="New desc",
            content={"text": "hi"},
        )
        repo.update_labels.assert_awaited_once_with(5, ["new-label"])

    @pytest.mark.anyio
    async def test_update_without_labels(self):
        """When labels is None, update_labels should not be called."""
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        updated = _entity(id=5, name="Updated")
        repo.update_entity.return_value = updated
        _stub_dto_deps(repo, updated)

        svc = _build_service(repo=repo)

        await svc.update(
            knowledge_id=5,
            user_id=99,
            name="Updated",
            labels=None,
        )

        repo.update_labels.assert_not_awaited()

    @pytest.mark.anyio
    async def test_update_with_empty_labels_list(self):
        """When labels is an empty list, update_labels should still be called."""
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        updated = _entity(id=5)
        repo.update_entity.return_value = updated
        _stub_dto_deps(repo, updated)

        svc = _build_service(repo=repo)

        await svc.update(
            knowledge_id=5,
            user_id=99,
            labels=[],
        )

        repo.update_labels.assert_awaited_once_with(5, [])

    @pytest.mark.anyio
    async def test_update_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.update(knowledge_id=999, user_id=99, name="X")

    @pytest.mark.anyio
    async def test_update_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.update(knowledge_id=5, user_id=99, name="X")

    @pytest.mark.anyio
    async def test_update_no_optional_fields(self):
        """Update with only required params, all optional fields are None."""
        repo = AsyncMock()
        entity = _entity(id=5, team_id=10)
        repo.get_by_id.return_value = entity
        repo.update_entity.return_value = entity
        _stub_dto_deps(repo, entity)

        svc = _build_service(repo=repo)

        result = await svc.update(knowledge_id=5, user_id=99)

        repo.update_entity.assert_awaited_once_with(
            entity=entity,
            name=None,
            description=None,
            content=None,
        )
        assert result["id"] == 5


# ===========================================================================
# upvote
# ===========================================================================


class TestUpvote:
    @pytest.mark.anyio
    async def test_upvote_success(self):
        repo = AsyncMock()
        entity = _entity(id=42, team_id=10)
        repo.get_by_id.return_value = entity
        repo.add_upvote.return_value = None
        _stub_dto_deps(repo, entity, upvote_count=1, has_upvote=True)

        svc = _build_service(repo=repo)

        result = await svc.upvote(knowledge_id=42, user_id=7)

        repo.add_upvote.assert_awaited_once_with(42, 7)
        assert result["upvoteCount"] == 1
        assert result["isUpvoted"] is True

    @pytest.mark.anyio
    async def test_upvote_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.upvote(knowledge_id=999, user_id=7)

    @pytest.mark.anyio
    async def test_upvote_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        entity = _entity(id=42, team_id=10)
        repo.get_by_id.return_value = entity

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.upvote(knowledge_id=42, user_id=7)


# ===========================================================================
# remove_upvote
# ===========================================================================


class TestRemoveUpvote:
    @pytest.mark.anyio
    async def test_remove_upvote_success(self):
        repo = AsyncMock()
        entity = _entity(id=42, team_id=10)
        repo.get_by_id.return_value = entity
        repo.remove_upvote.return_value = None
        _stub_dto_deps(repo, entity, upvote_count=0, has_upvote=False)

        svc = _build_service(repo=repo)

        result = await svc.remove_upvote(knowledge_id=42, user_id=7)

        repo.remove_upvote.assert_awaited_once_with(42, 7)
        assert result["upvoteCount"] == 0
        assert result["isUpvoted"] is False

    @pytest.mark.anyio
    async def test_remove_upvote_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource knowledge not found"):
            await svc.remove_upvote(knowledge_id=999, user_id=7)

    @pytest.mark.anyio
    async def test_remove_upvote_non_member_raises_forbidden(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False

        entity = _entity(id=42, team_id=10)
        repo.get_by_id.return_value = entity

        svc = KnowledgeService(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc.remove_upvote(knowledge_id=42, user_id=7)


# ===========================================================================
# _build_dto
# ===========================================================================


class TestBuildDto:
    @pytest.mark.anyio
    async def test_build_dto_with_user(self):
        repo = AsyncMock()
        entity = _entity(id=1)
        repo.get_labels_map.return_value = {1: ["tag1", "tag2"]}
        repo.get_upvote_count.return_value = 5
        repo.has_upvote.return_value = True

        svc = _build_service(repo=repo)

        result = await svc._build_dto(entity, current_user_id=99)

        assert result["labels"] == ["tag1", "tag2"]
        assert result["upvoteCount"] == 5
        assert result["isUpvoted"] is True
        repo.has_upvote.assert_awaited_once_with(1, 99)

    @pytest.mark.anyio
    async def test_build_dto_without_user(self):
        repo = AsyncMock()
        entity = _entity(id=1)
        repo.get_labels_map.return_value = {1: []}
        repo.get_upvote_count.return_value = 3

        svc = _build_service(repo=repo)

        result = await svc._build_dto(entity, current_user_id=None)

        assert result["isUpvoted"] is False
        repo.has_upvote.assert_not_awaited()

    @pytest.mark.anyio
    async def test_build_dto_no_labels_in_map(self):
        repo = AsyncMock()
        entity = _entity(id=1)
        repo.get_labels_map.return_value = {}
        repo.get_upvote_count.return_value = 0
        repo.has_upvote.return_value = False

        svc = _build_service(repo=repo)

        result = await svc._build_dto(entity, current_user_id=99)

        assert result["labels"] == []


# ===========================================================================
# _build_dtos
# ===========================================================================


class TestBuildDtos:
    @pytest.mark.anyio
    async def test_build_dtos_with_user(self):
        repo = AsyncMock()
        e1 = _entity(id=1)
        e2 = _entity(id=2, name="Other")
        entities = [e1, e2]

        repo.get_labels_map.return_value = {1: ["a"], 2: ["b", "c"]}
        repo.get_upvote_counts.return_value = {1: 10, 2: 5}
        repo.list_user_upvotes.return_value = {2}

        svc = _build_service(repo=repo)

        result = await svc._build_dtos(entities, current_user_id=99)

        assert len(result) == 2
        assert result[0]["labels"] == ["a"]
        assert result[0]["upvoteCount"] == 10
        assert result[0]["isUpvoted"] is False
        assert result[1]["labels"] == ["b", "c"]
        assert result[1]["upvoteCount"] == 5
        assert result[1]["isUpvoted"] is True
        repo.list_user_upvotes.assert_awaited_once_with([1, 2], 99)

    @pytest.mark.anyio
    async def test_build_dtos_without_user(self):
        repo = AsyncMock()
        e1 = _entity(id=1)
        entities = [e1]

        repo.get_labels_map.return_value = {1: []}
        repo.get_upvote_counts.return_value = {1: 2}

        svc = _build_service(repo=repo)

        result = await svc._build_dtos(entities, current_user_id=None)

        assert len(result) == 1
        assert result[0]["isUpvoted"] is False
        repo.list_user_upvotes.assert_not_awaited()

    @pytest.mark.anyio
    async def test_build_dtos_missing_counts_default_zero(self):
        repo = AsyncMock()
        e1 = _entity(id=1)
        entities = [e1]

        repo.get_labels_map.return_value = {}
        repo.get_upvote_counts.return_value = {}
        repo.list_user_upvotes.return_value = set()

        svc = _build_service(repo=repo)

        result = await svc._build_dtos(entities, current_user_id=99)

        assert result[0]["upvoteCount"] == 0
        assert result[0]["labels"] == []
        assert result[0]["isUpvoted"] is False

    @pytest.mark.anyio
    async def test_build_dtos_entity_with_none_id_excluded_from_ids(self):
        """Entities with id=None should be excluded from the IDs list."""
        repo = AsyncMock()
        e1 = _entity(id=1)
        e_none = _entity(id=None)
        entities = [e1, e_none]

        repo.get_labels_map.return_value = {1: []}
        repo.get_upvote_counts.return_value = {}
        repo.list_user_upvotes.return_value = set()

        svc = _build_service(repo=repo)

        result = await svc._build_dtos(entities, current_user_id=99)

        assert len(result) == 2
        # Only id=1 should be in the ids list passed to get_labels_map
        repo.get_labels_map.assert_awaited_once_with([1])

    @pytest.mark.anyio
    async def test_build_dtos_empty_list(self):
        repo = AsyncMock()
        repo.get_labels_map.return_value = {}
        repo.get_upvote_counts.return_value = {}

        svc = _build_service(repo=repo)

        result = await svc._build_dtos([], current_user_id=99)

        assert result == []
        repo.get_labels_map.assert_awaited_once_with([])


# ===========================================================================
# _to_dto
# ===========================================================================


class TestToDto:
    def test_to_dto_all_fields(self):
        svc = _build_service()
        entity = _entity(
            id=1,
            name="Item",
            description="Desc",
            type="TEXT",
            content={"body": "data"},
            team_id=10,
            project_id=20,
            discussion_id=30,
            material_id=40,
            created_by=99,
            created_at=NOW,
            updated_at=NOW,
        )

        result = svc._to_dto(entity, ["label1"], 5, True)

        assert result["id"] == 1
        assert result["name"] == "Item"
        assert result["type"] == "TEXT"
        assert result["content"] == {"body": "data"}
        assert result["description"] == "Desc"
        assert result["teamId"] == 10
        assert result["projectId"] == 20
        assert result["discussionId"] == 30
        assert result["materialId"] == 40
        assert result["labels"] == ["label1"]
        assert result["createdBy"] == 99
        assert result["upvoteCount"] == 5
        assert result["isUpvoted"] is True
        assert isinstance(result["createdAt"], int)
        assert isinstance(result["updatedAt"], int)

    def test_to_dto_none_timestamps(self):
        svc = _build_service()
        entity = _entity(id=1, created_at=None, updated_at=None)

        result = svc._to_dto(entity, [], 0, False)

        assert result["createdAt"] == 0
        assert result["updatedAt"] == 0

    def test_to_dto_no_upvote(self):
        svc = _build_service()
        entity = _entity(id=1)

        result = svc._to_dto(entity, [], 0, False)

        assert result["upvoteCount"] == 0
        assert result["isUpvoted"] is False


# ===========================================================================
# _ensure_team_member
# ===========================================================================


class TestEnsureTeamMember:
    @pytest.mark.anyio
    async def test_ensure_team_member_passes(self):
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = True
        svc = _build_service(team_repo=team_repo)

        # Should not raise
        await svc._ensure_team_member(10, 99)

        team_repo.is_team_member.assert_awaited_once_with(10, 99)

    @pytest.mark.anyio
    async def test_ensure_team_member_fails(self):
        team_repo = AsyncMock()
        team_repo.is_team_member.return_value = False
        svc = KnowledgeService(repo=AsyncMock(), team_repo=team_repo)

        with pytest.raises(ForbiddenError, match="User is not a member of the team"):
            await svc._ensure_team_member(10, 99)
