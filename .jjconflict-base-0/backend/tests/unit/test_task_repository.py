"""Unit tests for app.domain.task.repositories.

Covers TaskRepository, TaskMembershipRepository, TaskSubmissionRepository,
TaskSubmissionEntryRepository, TaskSubmissionReviewRepository,
TaskAIAdviceRepository, TaskAIAdviceContextRepository,
AIConversationRepository, AIMessageRepository, TopicRepository,
TaskSubmissionSchemaRepository.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.domain.task.models import Task
from app.domain.task.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    TaskAIAdviceContextRepository,
    TaskAIAdviceRepository,
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
    TaskSubmissionSchemaRepository,
    TopicRepository,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides):
    defaults = {
        "id": 1,
        "name": "Task One",
        "intro": "An intro",
        "description": "Description",
        "creator_id": 10,
        "space_id": 100,
        "category_id": 5,
        "submitter_type": 0,
        "approved": 2,
        "deadline": None,
        "registration_start_at": None,
        "participant_limit": None,
        "default_deadline": 7,
        "resubmittable": False,
        "editable": False,
        "rank": None,
        "require_real_name": False,
        "min_team_size": None,
        "max_team_size": None,
        "team_locking_policy": "NONE",
        "video_url": None,
        "reject_reason": "",
        "published_at": None,
        "ended_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _membership(**overrides):
    defaults = {
        "id": 1,
        "task_id": 1,
        "member_id": 10,
        "is_team": False,
        "approved": 0,
        "completion_status": "NOT_SUBMITTED",
        "deadline": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _submission(**overrides):
    defaults = {
        "id": 1,
        "membership_id": 1,
        "submitter_id": 10,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _review(**overrides):
    defaults = {
        "id": 1,
        "submission_id": 1,
        "accepted": True,
        "score": 85,
        "comment": "Good",
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


def _mock_scalar_one(val):
    m = MagicMock()
    m.scalar_one.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


def _mock_rowcount(count):
    m = MagicMock()
    m.rowcount = count
    return m


# ---------------------------------------------------------------------------
# TaskRepository
# ---------------------------------------------------------------------------


class TestTaskRepository:
    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        t = _task()
        session.execute.return_value = _mock_scalar(t)
        repo = TaskRepository(session)

        result = await repo.get_by_id(1)
        assert result is t

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TaskRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_list_tasks_basic(self):
        session = _mock_session()
        t = _task()
        session.execute.return_value = _mock_scalars([t])
        repo = TaskRepository(session)

        result = await repo.list_tasks(space_id=100, limit=10, offset=0)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_list_tasks_with_filters(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100,
            category_id=5,
            approved=0,
            owner_id=10,
            keywords="test",
            limit=10,
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_tasks_sort_createdAt(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100, limit=10, sort_by="createdAt", sort_order="asc"
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_tasks_sort_deadline(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100, limit=10, sort_by="deadline", sort_order="desc"
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_tasks_sort_published_at(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100, limit=10, sort_by="publishedAt", sort_order="desc"
        )

        assert list(result) == []
        sql = str(session.execute.await_args.args[0])
        assert "published_at" in sql

    @pytest.mark.anyio
    async def test_list_tasks_with_topics_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(space_id=100, topics=[1, 2], limit=10)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_tasks_joined_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100, joined=True, current_user_id=10, limit=10
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_tasks_not_joined_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskRepository(session)

        result = await repo.list_tasks(
            space_id=100, joined=False, current_user_id=10, limit=10
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_count_tasks_basic(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = TaskRepository(session)

        result = await repo.count_tasks(space_id=100)
        assert result == 5

    @pytest.mark.anyio
    async def test_count_tasks_with_filters(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(2)
        repo = TaskRepository(session)

        result = await repo.count_tasks(
            space_id=100,
            category_id=5,
            approved=0,
            owner_id=10,
            keywords="test",
            topics=[1],
        )
        assert result == 2

    @pytest.mark.anyio
    async def test_count_tasks_joined_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(1)
        repo = TaskRepository(session)

        result = await repo.count_tasks(space_id=100, joined=True, current_user_id=10)
        assert result == 1

    @pytest.mark.anyio
    async def test_count_tasks_not_joined_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = TaskRepository(session)

        result = await repo.count_tasks(space_id=100, joined=False, current_user_id=10)
        assert result == 3

    def test_apply_space_task_visibility_unlimited(self):
        session = _mock_session()
        repo = TaskRepository(session)

        stmt = repo._apply_space_task_visibility(
            select(Task),
            space_id=100,
            visible_task_limit=None,
        )
        sql = str(stmt)

        assert "ended_at IS NOT NULL" in sql
        assert "approved" in sql

    def test_apply_space_task_visibility_zero(self):
        session = _mock_session()
        repo = TaskRepository(session)

        stmt = repo._apply_space_task_visibility(
            select(Task),
            space_id=100,
            visible_task_limit=0,
        )

        assert "ended_at IS NOT NULL" in str(stmt)

    def test_apply_space_task_visibility_limited_uses_row_number(self):
        session = _mock_session()
        repo = TaskRepository(session)

        stmt = repo._apply_space_task_visibility(
            select(Task),
            space_id=100,
            visible_task_limit=1,
        )
        sql = str(stmt).lower()

        assert "row_number" in sql
        assert "partition by task.creator_id" in sql
        assert (
            "order by coalesce(task.published_at, task.created_at) asc, task.id asc"
            in sql
        )

    @pytest.mark.anyio
    async def test_is_task_visible_for_space_limit_hides_later_task(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(1)
        repo = TaskRepository(session)
        task = _task(
            id=2,
            creator_id=100,
            approved=0,
            published_at=datetime(2025, 1, 2, tzinfo=UTC),
        )

        result = await repo.is_task_visible_for_space_limit(
            task=task,
            visible_task_limit=1,
        )

        assert result is False
        sql = str(session.execute.await_args.args[0])
        assert "coalesce(task.published_at, task.created_at) <" in sql

    @pytest.mark.anyio
    async def test_create_task(self):
        session = _mock_session()
        repo = TaskRepository(session)

        result = await repo.create_task(
            name="New Task",
            intro="Intro",
            description="Desc",
            creator_id=10,
            space_id=100,
            category_id=5,
            submitter_type=0,
            deadline=None,
            registration_start_at=None,
            participant_limit=None,
            default_deadline=7,
            resubmittable=False,
            editable=False,
            rank=None,
            require_real_name=False,
            min_team_size=None,
            max_team_size=None,
            team_locking_policy="NONE",
        )
        assert result.name == "New Task"
        assert result.approved == 2
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_task_with_deadline(self):
        session = _mock_session()
        repo = TaskRepository(session)
        deadline = datetime(2025, 12, 31, tzinfo=UTC)

        result = await repo.create_task(
            name="Task With Deadline",
            intro="Intro",
            description="Desc",
            creator_id=10,
            space_id=100,
            category_id=5,
            submitter_type=1,
            deadline=deadline,
            registration_start_at=deadline,
            participant_limit=50,
            default_deadline=14,
            resubmittable=True,
            editable=True,
            rank=1,
            require_real_name=True,
            min_team_size=2,
            max_team_size=5,
            team_locking_policy="ALWAYS",
            video_url="https://example.com/video",
        )
        assert result.name == "Task With Deadline"
        assert result.deadline is not None
        assert result.registration_start_at is not None

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        t = _task()
        repo = TaskRepository(session)

        result = await repo.save(t)
        assert result is t
        session.add.assert_called_once_with(t)


# ---------------------------------------------------------------------------
# TaskMembershipRepository
# ---------------------------------------------------------------------------


class TestTaskMembershipRepository:
    @pytest.mark.anyio
    async def test_list_memberships_for_task(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalars([m])
        repo = TaskMembershipRepository(session)

        result = await repo.list_memberships_for_task(1)
        assert list(result) == [m]

    @pytest.mark.anyio
    async def test_list_memberships_for_task_with_approved(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskMembershipRepository(session)

        result = await repo.list_memberships_for_task(1, approved=0)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_get_user_membership(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalar(m)
        repo = TaskMembershipRepository(session)

        result = await repo.get_user_membership(1, 10)
        assert result is m

    @pytest.mark.anyio
    async def test_list_team_memberships_for_user(self):
        session = _mock_session()
        m = _membership(is_team=True, member_id=100)
        session.execute.return_value = _mock_scalars([m])
        repo = TaskMembershipRepository(session)

        result = await repo.list_team_memberships_for_user(1, 10)
        assert list(result) == [m]

    @pytest.mark.anyio
    async def test_count_approved_for_task(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = TaskMembershipRepository(session)

        assert await repo.count_approved_for_task(1) == 3

    @pytest.mark.anyio
    async def test_count_memberships_for_task(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = TaskMembershipRepository(session)

        assert await repo.count_memberships_for_task(1) == 5

    @pytest.mark.anyio
    async def test_count_team_members(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(4)
        repo = TaskMembershipRepository(session)

        assert await repo.count_team_members(100) == 4

    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalar(m)
        repo = TaskMembershipRepository(session)

        result = await repo.get_by_id(1)
        assert result is m

    @pytest.mark.anyio
    async def test_list_memberships_for_space(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalars([m])
        repo = TaskMembershipRepository(session)

        result = await repo.list_memberships_for_space(100)
        assert list(result) == [m]

    @pytest.mark.anyio
    async def test_get_by_task_and_member(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalar(m)
        repo = TaskMembershipRepository(session)

        result = await repo.get_by_task_and_member(1, 10)
        assert result is m

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        m = _membership()
        repo = TaskMembershipRepository(session)

        result = await repo.save(m)
        assert result is m
        session.add.assert_called_once_with(m)

    @pytest.mark.anyio
    async def test_find_active_locked_memberships(self):
        session = _mock_session()
        m = _membership(is_team=True)
        session.execute.return_value = _mock_scalars([m])
        repo = TaskMembershipRepository(session)

        result = await repo.find_active_locked_memberships(100, ["ALWAYS"])
        assert list(result) == [m]


# ---------------------------------------------------------------------------
# TaskSubmissionRepository
# ---------------------------------------------------------------------------


class TestTaskSubmissionRepository:
    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        s = _submission()
        session.execute.return_value = _mock_scalar(s)
        repo = TaskSubmissionRepository(session)

        result = await repo.get_by_id(1)
        assert result is s

    @pytest.mark.anyio
    async def test_create_submission(self):
        session = _mock_session()
        repo = TaskSubmissionRepository(session)

        result = await repo.create_submission(
            membership_id=1, submitter_id=10, version=1
        )
        assert result.membership_id == 1
        assert result.version == 1
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_latest_version_for_membership(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = TaskSubmissionRepository(session)

        assert await repo.get_latest_version_for_membership(1) == 3

    @pytest.mark.anyio
    async def test_get_latest_version_for_membership_none(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(None)
        repo = TaskSubmissionRepository(session)

        assert await repo.get_latest_version_for_membership(1) == 0

    @pytest.mark.anyio
    async def test_get_by_membership_and_version(self):
        session = _mock_session()
        s = _submission()
        session.execute.return_value = _mock_scalar(s)
        repo = TaskSubmissionRepository(session)

        result = await repo.get_by_membership_and_version(1, 1)
        assert result is s

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        s = _submission()
        repo = TaskSubmissionRepository(session)

        result = await repo.save(s)
        assert result is s

    @pytest.mark.anyio
    async def test_list_submissions(self):
        session = _mock_session()
        s = _submission()
        session.execute.return_value = _mock_scalars([s])
        repo = TaskSubmissionRepository(session)

        result = await repo.list_submissions(task_id=1, limit=10)
        assert list(result) == [s]

    @pytest.mark.anyio
    async def test_list_submissions_all_versions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskSubmissionRepository(session)

        result = await repo.list_submissions(task_id=1, all_versions=True, limit=10)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_submissions_with_participant_and_reviewed(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskSubmissionRepository(session)

        result = await repo.list_submissions(
            task_id=1, participant_id=1, reviewed=True, limit=10, sort_by="createdAt"
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_submissions_not_reviewed(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskSubmissionRepository(session)

        result = await repo.list_submissions(
            task_id=1, reviewed=False, limit=10, sort_order="asc"
        )
        assert list(result) == []

    @pytest.mark.anyio
    async def test_count_submissions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = TaskSubmissionRepository(session)

        assert await repo.count_submissions(task_id=1) == 5

    @pytest.mark.anyio
    async def test_count_submissions_with_filters(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(2)
        repo = TaskSubmissionRepository(session)

        result = await repo.count_submissions(
            task_id=1, participant_id=1, all_versions=True, reviewed=True
        )
        assert result == 2

    @pytest.mark.anyio
    async def test_count_submissions_not_reviewed(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = TaskSubmissionRepository(session)

        result = await repo.count_submissions(task_id=1, reviewed=False)
        assert result == 3


# ---------------------------------------------------------------------------
# TaskSubmissionEntryRepository
# ---------------------------------------------------------------------------


class TestTaskSubmissionEntryRepository:
    @pytest.mark.anyio
    async def test_list_by_submission_id(self):
        session = _mock_session()
        entry = SimpleNamespace(id=1, task_submission_id=1, index=0)
        session.execute.return_value = _mock_scalars([entry])
        repo = TaskSubmissionEntryRepository(session)

        result = await repo.list_by_submission_id(1)
        assert list(result) == [entry]

    @pytest.mark.anyio
    async def test_create_entries(self):
        session = _mock_session()
        repo = TaskSubmissionEntryRepository(session)

        await repo.create_entries(
            submission_id=1,
            entries=[(0, "text content", None), (1, None, 100)],
        )
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_soft_delete_by_membership_and_version(self):
        session = _mock_session()
        entry = SimpleNamespace(id=1, deleted_at=None)
        session.execute.return_value = _mock_scalars([entry])
        repo = TaskSubmissionEntryRepository(session)

        await repo.soft_delete_by_membership_and_version(1, 1)
        assert entry.deleted_at is not None

    @pytest.mark.anyio
    async def test_soft_delete_by_membership_and_version_no_entries(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskSubmissionEntryRepository(session)

        await repo.soft_delete_by_membership_and_version(1, 1)
        session.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# TaskSubmissionReviewRepository
# ---------------------------------------------------------------------------


class TestTaskSubmissionReviewRepository:
    @pytest.mark.anyio
    async def test_get_by_submission_id(self):
        session = _mock_session()
        r = _review()
        session.execute.return_value = _mock_scalar(r)
        repo = TaskSubmissionReviewRepository(session)

        result = await repo.get_by_submission_id(1)
        assert result is r

    @pytest.mark.anyio
    async def test_exists_by_submission_id_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = TaskSubmissionReviewRepository(session)

        assert await repo.exists_by_submission_id(1) is True

    @pytest.mark.anyio
    async def test_exists_by_submission_id_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TaskSubmissionReviewRepository(session)

        assert await repo.exists_by_submission_id(1) is False

    @pytest.mark.anyio
    async def test_create_review(self):
        session = _mock_session()
        repo = TaskSubmissionReviewRepository(session)

        result = await repo.create_review(
            submission_id=1, accepted=True, score=90, comment="Excellent"
        )
        assert result.submission_id == 1
        assert result.accepted is True
        assert result.score == 90
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        r = _review()
        repo = TaskSubmissionReviewRepository(session)

        result = await repo.save(r)
        assert result is r

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        r = _review()
        repo = TaskSubmissionReviewRepository(session)

        await repo.soft_delete(r)
        assert r.deleted_at is not None


# ---------------------------------------------------------------------------
# TaskAIAdviceRepository
# ---------------------------------------------------------------------------


class TestTaskAIAdviceRepository:
    @pytest.mark.anyio
    async def test_list_by_task(self):
        session = _mock_session()
        advice = SimpleNamespace(id=1, task_id=1)
        session.execute.return_value = _mock_scalars([advice])
        repo = TaskAIAdviceRepository(session)

        result = await repo.list_by_task(1)
        assert result == [advice]

    @pytest.mark.anyio
    async def test_get_latest(self):
        session = _mock_session()
        advice = SimpleNamespace(id=1, task_id=1)
        session.execute.return_value = _mock_scalar(advice)
        repo = TaskAIAdviceRepository(session)

        result = await repo.get_latest(1)
        assert result is advice

    @pytest.mark.anyio
    async def test_find_by_model_hash(self):
        session = _mock_session()
        advice = SimpleNamespace(id=1, model_hash="abc123")
        session.execute.return_value = _mock_scalar(advice)
        repo = TaskAIAdviceRepository(session)

        result = await repo.find_by_model_hash(1, "abc123")
        assert result is advice

    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = TaskAIAdviceRepository(session)

        result = await repo.create(task_id=1, model_hash="abc123", status="pending")
        assert result.task_id == 1
        assert result.model_hash == "abc123"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        advice = SimpleNamespace(id=1)
        repo = TaskAIAdviceRepository(session)

        result = await repo.save(advice)
        assert result is advice


# ---------------------------------------------------------------------------
# TaskAIAdviceContextRepository
# ---------------------------------------------------------------------------


class TestTaskAIAdviceContextRepository:
    @pytest.mark.anyio
    async def test_get_or_create_existing(self):
        session = _mock_session()
        ctx = SimpleNamespace(id=1, task_id=1, section="intro", section_index=0)
        session.execute.return_value = _mock_scalar(ctx)
        repo = TaskAIAdviceContextRepository(session)

        result = await repo.get_or_create(task_id=1, section="intro", section_index=0)
        assert result is ctx

    @pytest.mark.anyio
    async def test_get_or_create_new(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TaskAIAdviceContextRepository(session)

        result = await repo.get_or_create(task_id=1, section=None, section_index=None)
        assert result.task_id == 1
        session.add.assert_called_once()


# ---------------------------------------------------------------------------
# AIConversationRepository
# ---------------------------------------------------------------------------


class TestAIConversationRepository:
    @pytest.mark.anyio
    async def test_list_for_task(self):
        session = _mock_session()
        convo = SimpleNamespace(id=1, context_id=1)
        session.execute.return_value = _mock_scalars([convo])
        repo = AIConversationRepository(session)

        result = await repo.list_for_task(1)
        assert result == [convo]

    @pytest.mark.anyio
    async def test_get_by_conversation_id(self):
        session = _mock_session()
        convo = SimpleNamespace(conversation_id="abc")
        session.execute.return_value = _mock_scalar(convo)
        repo = AIConversationRepository(session)

        result = await repo.get_by_conversation_id("abc")
        assert result is convo

    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = AIConversationRepository(session)

        result = await repo.create(
            conversation_id="abc", task_id=1, owner_id=10, title="Chat"
        )
        assert result.conversation_id == "abc"
        assert result.title == "Chat"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        convo = SimpleNamespace(deleted_at=None)
        repo = AIConversationRepository(session)

        await repo.soft_delete(convo)
        assert convo.deleted_at is not None


# ---------------------------------------------------------------------------
# AIMessageRepository
# ---------------------------------------------------------------------------


class TestAIMessageRepository:
    @pytest.mark.anyio
    async def test_list_for_conversation(self):
        session = _mock_session()
        msg = SimpleNamespace(id=1, conversation_id=1)
        session.execute.return_value = _mock_scalars([msg])
        repo = AIMessageRepository(session)

        result = await repo.list_for_conversation(1)
        assert result == [msg]

    @pytest.mark.anyio
    async def test_create_message(self):
        session = _mock_session()
        repo = AIMessageRepository(session)

        result = await repo.create_message(
            conversation_id=1, role="user", content="Hello"
        )
        assert result.role == "user"
        assert result.content == "Hello"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        msg = SimpleNamespace(deleted_at=None, updated_at=None)
        repo = AIMessageRepository(session)

        await repo.soft_delete(msg)
        assert msg.deleted_at is not None
        assert msg.updated_at is not None


# ---------------------------------------------------------------------------
# TopicRepository
# ---------------------------------------------------------------------------


class TestTopicRepository:
    @pytest.mark.anyio
    async def test_list_by_task_id(self):
        session = _mock_session()
        topic = SimpleNamespace(id=1, name="Python")
        session.execute.return_value = _mock_scalars([topic])
        repo = TopicRepository(session)

        result = await repo.list_by_task_id(1)
        assert list(result) == [topic]


# ---------------------------------------------------------------------------
# TaskSubmissionSchemaRepository
# ---------------------------------------------------------------------------


class TestTaskSubmissionSchemaRepository:
    @pytest.mark.anyio
    async def test_list_by_task_id(self):
        session = _mock_session()
        entry = SimpleNamespace(id=1, task_id=1, index=0)
        session.execute.return_value = _mock_scalars([entry])
        repo = TaskSubmissionSchemaRepository(session)

        result = await repo.list_by_task_id(1)
        assert list(result) == [entry]

    @pytest.mark.anyio
    async def test_replace_schema(self):
        session = _mock_session()
        session.execute.return_value = MagicMock()  # delete result
        repo = TaskSubmissionSchemaRepository(session)

        entries = [
            {"prompt": "Write your answer", "type": "TEXT"},
            {"prompt": "Upload file", "type": "FILE"},
        ]
        result = await repo.replace_schema(1, entries)
        assert len(result) == 2
        assert result[0].description == "Write your answer"
        assert result[0].type == 0  # TEXT
        assert result[1].type == 1  # FILE
