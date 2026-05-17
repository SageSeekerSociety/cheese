"""Unit tests for task access control (domain-group-based visibility).

Covers:
- CreateTaskRequest / PatchTaskRequest validation
- _task_to_api_model includes accessControlEnabled
- _create_task_entity domain group resolution logic
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.tasks import CreateTaskRequest, PatchTaskRequest, _task_to_api_model

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    defaults = {
        "id": 1,
        "name": "Test Task",
        "intro": "intro",
        "description": "desc",
        "creator_id": 10,
        "space_id": 100,
        "category_id": 5,
        "submitter_type": 0,
        "approved": 2,
        "participant_limit": None,
        "deadline": None,
        "registration_start_at": None,
        "default_deadline": 30,
        "resubmittable": False,
        "editable": True,
        "rank": None,
        "require_real_name": False,
        "min_team_size": None,
        "max_team_size": None,
        "reject_reason": "",
        "team_locking_policy": "NO_LOCK",
        "access_control_enabled": False,
        "video_url": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _create_payload(**overrides):
    defaults = {
        "name": "Test Task",
        "submitterType": "USER",
        "resubmittable": False,
        "editable": True,
        "intro": "intro text",
        "description": "desc text",
        "space": 100,
        "defaultDeadline": 30,
        "deadline": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
        "rank": 1,
        "categoryId": 5,
        "topics": [],
    }
    defaults.update(overrides)
    return defaults


def _mock_space_repo(session=None):
    async def get_by_id(space_id):
        return SimpleNamespace(id=space_id, default_category_id=5)

    return SimpleNamespace(get_by_id=get_by_id)


def _mock_category_repo(session=None):
    async def get_default_category(space_id):
        return SimpleNamespace(
            id=5,
            space_id=space_id,
            archived_at=None,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=None,
        )

    async def get_by_id(category_id):
        return SimpleNamespace(id=category_id, space_id=100, archived_at=None, deleted_at=None)

    async def get_by_id_and_space(category_id, space_id):
        return SimpleNamespace(id=category_id, space_id=space_id, archived_at=None, deleted_at=None)

    return SimpleNamespace(
        get_default_category=get_default_category,
        get_by_id=get_by_id,
        get_by_id_and_space=get_by_id_and_space,
    )


def _mock_task_repo(**kwargs):
    async def create_task(**ckwargs):
        return _make_task(
            id=42,
            access_control_enabled=ckwargs.get("access_control_enabled", False),
        )

    return SimpleNamespace(create_task=create_task, **kwargs)


# ---------------------------------------------------------------------------
# CreateTaskRequest validation
# ---------------------------------------------------------------------------


class TestCreateTaskRequestValidation:
    def test_create_request_accepts_access_control_enabled(self):
        payload = _create_payload(accessControlEnabled=True)
        req = CreateTaskRequest.model_validate(payload)
        assert req.access_control_enabled is True

    def test_create_request_access_control_defaults_to_false(self):
        payload = _create_payload()
        req = CreateTaskRequest.model_validate(payload)
        assert req.access_control_enabled is False
        assert req.access_domain_group_ids == []

    def test_create_request_accepts_access_domain_group_ids(self):
        payload = _create_payload(accessControlEnabled=True, accessDomainGroupIds=[1, 2, 3])
        req = CreateTaskRequest.model_validate(payload)
        assert req.access_control_enabled is True
        assert req.access_domain_group_ids == [1, 2, 3]

    def test_create_request_empty_domain_group_ids_by_default(self):
        payload = _create_payload(accessControlEnabled=True)
        req = CreateTaskRequest.model_validate(payload)
        assert req.access_domain_group_ids == []

    def test_create_request_rejects_invalid_access_control_type(self):
        from pydantic import ValidationError

        payload = _create_payload(accessControlEnabled=42)
        with pytest.raises(ValidationError):
            CreateTaskRequest.model_validate(payload)

    def test_create_request_rejects_non_int_group_ids(self):
        from pydantic import ValidationError

        payload = _create_payload(accessDomainGroupIds=["abc"])
        with pytest.raises(ValidationError):
            CreateTaskRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# PatchTaskRequest validation
# ---------------------------------------------------------------------------


class TestPatchTaskRequestValidation:
    def test_patch_request_accepts_access_control_enabled(self):
        req = PatchTaskRequest.model_validate({"accessControlEnabled": True})
        assert req.access_control_enabled is True

    def test_patch_request_accepts_access_domain_group_ids(self):
        req = PatchTaskRequest.model_validate({"accessDomainGroupIds": [1, 2]})
        assert req.access_domain_group_ids == [1, 2]

    def test_patch_request_access_control_none_when_absent(self):
        req = PatchTaskRequest.model_validate({})
        assert req.access_control_enabled is None
        assert req.access_domain_group_ids is None

    def test_patch_request_access_control_false(self):
        req = PatchTaskRequest.model_validate({"accessControlEnabled": False})
        assert req.access_control_enabled is False


# ---------------------------------------------------------------------------
# _task_to_api_model
# ---------------------------------------------------------------------------


class TestTaskToApiModel:
    def test_access_control_enabled_in_api_model_when_true(self):
        task = _make_task(access_control_enabled=True)
        result = _task_to_api_model(task)
        assert result["accessControlEnabled"] is True

    def test_access_control_enabled_in_api_model_when_false(self):
        task = _make_task(access_control_enabled=False)
        result = _task_to_api_model(task)
        assert result["accessControlEnabled"] is False

    def test_api_model_contains_required_fields(self):
        task = _make_task()
        result = _task_to_api_model(task)
        required_fields = [
            "id",
            "name",
            "intro",
            "description",
            "deadline",
            "registrationStartAt",
            "defaultDeadline",
            "resubmittable",
            "editable",
            "approved",
            "rank",
            "submitterType",
            "requireRealName",
            "participantLimit",
            "minTeamSize",
            "maxTeamSize",
            "teamLockingPolicy",
            "accessControlEnabled",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# _create_task_entity — domain group resolution
# ---------------------------------------------------------------------------


class TestCreateTaskEntityDomainResolution:
    @pytest.mark.anyio
    async def test_access_control_enabled_resolves_domains_from_groups(self):
        """When accessControlEnabled=True with group IDs, domains are resolved
        from SpaceDomainGroupDomainRepository and persisted via
        TaskAccessDomainRepository."""
        from app.api.routes.tasks import _create_task_entity

        payload = CreateTaskRequest.model_validate(
            _create_payload(
                accessControlEnabled=True,
                accessDomainGroupIds=[1, 2],
            )
        )

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        # Track TaskAccessDomain replace_domains call
        replace_calls: list[dict] = []

        async def _replace_domains(*, task_id, domains):
            replace_calls.append({"task_id": task_id, "domains": domains})

        async def _list_domains_for_groups(group_ids):
            return {1: ["cs.edu.cn", "ai.edu.cn"], 2: ["math.edu.cn"]}

        with (
            patch(
                "app.api.routes.tasks.SpaceRepository",
                return_value=_mock_space_repo(),
            ),
            patch(
                "app.api.routes.tasks.SpaceCategoryRepository",
                return_value=_mock_category_repo(),
            ),
            patch(
                "app.api.routes.tasks.TaskRepository",
                return_value=_mock_task_repo(),
            ),
            patch(
                "app.domain.space.repositories.SpaceDomainGroupDomainRepository",
                return_value=SimpleNamespace(
                    list_domains_for_groups=_list_domains_for_groups,
                ),
            ),
            patch(
                "app.domain.task.repositories.TaskAccessDomainRepository",
                return_value=SimpleNamespace(
                    replace_domains=_replace_domains,
                ),
            ),
        ):
            task = await _create_task_entity(
                payload=payload,
                db=mock_db,
                creator_user_id=10,
            )

        assert task.id == 42
        assert task.access_control_enabled is True
        assert len(replace_calls) == 1
        resolved = replace_calls[0]["domains"]
        assert "cs.edu.cn" in resolved
        assert "ai.edu.cn" in resolved
        assert "math.edu.cn" in resolved

    @pytest.mark.anyio
    async def test_access_control_disabled_skips_domain_resolution(self):
        """When accessControlEnabled=False, no TaskAccessDomain records are created."""
        from app.api.routes.tasks import _create_task_entity

        payload = CreateTaskRequest.model_validate(
            _create_payload(
                accessControlEnabled=False,
                accessDomainGroupIds=[1],
            )
        )

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        replace_calls: list[dict] = []

        async def _replace_domains(*, task_id, domains):
            replace_calls.append({"task_id": task_id, "domains": domains})

        with (
            patch(
                "app.api.routes.tasks.SpaceRepository",
                return_value=_mock_space_repo(),
            ),
            patch(
                "app.api.routes.tasks.SpaceCategoryRepository",
                return_value=_mock_category_repo(),
            ),
            patch(
                "app.api.routes.tasks.TaskRepository",
                return_value=_mock_task_repo(),
            ),
            patch(
                "app.domain.task.repositories.TaskAccessDomainRepository",
                return_value=SimpleNamespace(replace_domains=_replace_domains),
            ),
        ):
            task = await _create_task_entity(
                payload=payload,
                db=mock_db,
                creator_user_id=10,
            )

        assert task.access_control_enabled is False
        assert len(replace_calls) == 0
