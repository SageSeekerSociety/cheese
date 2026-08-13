"""Unit tests for app.domain.user.repositories.

Covers UserRepository, UserProfileRepository, UserFollowingRepository,
UserRealNameRepository, UserStatisticsRepository.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
    UserStatisticsRepository,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(**overrides):
    defaults = {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "hashed_password": "hashed",
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _profile(**overrides):
    defaults = {
        "id": 10,
        "user_id": 1,
        "nickname": "Alice",
        "intro": "Hello",
        "avatar_id": 5,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _identity(**overrides):
    defaults = {
        "id": 100,
        "user_id": 1,
        "encrypted": False,
        "real_name": "Zhang San",
        "student_id": "2024001",
        "grade": "2024",
        "major": "CS",
        "class_name": "Class-1",
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


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------


class TestUserRepository:
    @pytest.mark.anyio
    async def test_get_by_username_found(self):
        session = _mock_session()
        u = _user()
        session.execute.return_value = _mock_scalar(u)
        repo = UserRepository(session)

        result = await repo.get_by_username("alice")
        assert result is u

    @pytest.mark.anyio
    async def test_get_by_username_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRepository(session)

        result = await repo.get_by_username("nobody")
        assert result is None

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        u = _user()
        session.execute.return_value = _mock_scalar(u)
        repo = UserRepository(session)

        result = await repo.get_by_id(1)
        assert result is u

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRepository(session)

        result = await repo.get_by_id(999)
        assert result is None

    @pytest.mark.anyio
    async def test_is_username_taken_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = UserRepository(session)

        assert await repo.is_username_taken("alice") is True

    @pytest.mark.anyio
    async def test_is_username_taken_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRepository(session)

        assert await repo.is_username_taken("nobody") is False

    @pytest.mark.anyio
    async def test_is_email_taken_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = UserRepository(session)

        assert await repo.is_email_taken("alice@example.com") is True

    @pytest.mark.anyio
    async def test_is_email_taken_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRepository(session)

        assert await repo.is_email_taken("nobody@example.com") is False

    @pytest.mark.anyio
    async def test_get_by_email(self):
        session = _mock_session()
        u = _user()
        session.execute.return_value = _mock_scalar(u)
        repo = UserRepository(session)

        result = await repo.get_by_email("alice@example.com")
        assert result is u

    @pytest.mark.anyio
    async def test_create_user(self):
        session = _mock_session()
        repo = UserRepository(session)

        result = await repo.create_user(
            username="bob", email="bob@example.com", hashed_password="hashed_pw"
        )
        assert result.username == "bob"
        assert result.email == "bob@example.com"
        assert result.hashed_password == "hashed_pw"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_password_user_found(self):
        session = _mock_session()
        u = _user()
        session.execute.return_value = _mock_scalar(u)
        repo = UserRepository(session)

        await repo.update_password(1, "new_hashed")
        assert u.hashed_password == "new_hashed"
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_password_user_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRepository(session)

        await repo.update_password(999, "new_hashed")
        # No flush called because user not found
        session.flush.assert_not_awaited()

    @pytest.mark.anyio
    async def test_get_by_ids_empty(self):
        session = _mock_session()
        repo = UserRepository(session)

        result = await repo.get_by_ids([])
        assert result == {}

    @pytest.mark.anyio
    async def test_get_by_ids(self):
        session = _mock_session()
        u1 = _user(id=1)
        u2 = _user(id=2, username="bob")
        session.execute.return_value = _mock_scalars([u1, u2])
        repo = UserRepository(session)

        result = await repo.get_by_ids([1, 2])
        assert result == {1: u1, 2: u2}


# ---------------------------------------------------------------------------
# UserProfileRepository
# ---------------------------------------------------------------------------


class TestUserProfileRepository:
    @pytest.mark.anyio
    async def test_get_profiles_by_user_ids_empty(self):
        session = _mock_session()
        repo = UserProfileRepository(session)

        result = await repo.get_profiles_by_user_ids([])
        assert result == {}

    @pytest.mark.anyio
    async def test_get_profiles_by_user_ids(self):
        session = _mock_session()
        p1 = _profile(user_id=1)
        p2 = _profile(user_id=2, nickname="Bob")
        session.execute.return_value = _mock_scalars([p1, p2])
        repo = UserProfileRepository(session)

        result = await repo.get_profiles_by_user_ids([1, 2])
        assert result == {1: p1, 2: p2}

    @pytest.mark.anyio
    async def test_get_profile_by_user_id(self):
        session = _mock_session()
        p = _profile()
        session.execute.return_value = _mock_scalar(p)
        repo = UserProfileRepository(session)

        result = await repo.get_profile_by_user_id(1)
        assert result is p

    @pytest.mark.anyio
    async def test_create_profile(self):
        session = _mock_session()
        repo = UserProfileRepository(session)

        result = await repo.create_profile(
            user_id=1, nickname="Alice", intro="Hi", avatar_id=5
        )
        assert result.user_id == 1
        assert result.nickname == "Alice"
        assert result.intro == "Hi"
        assert result.avatar_id == 5
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_list_profiles(self):
        session = _mock_session()
        p = _profile()
        session.execute.return_value = _mock_scalars([p])
        repo = UserProfileRepository(session)

        result = await repo.list_profiles(limit=10, offset=0)
        assert result == [p]

    @pytest.mark.anyio
    async def test_update_profile_all_fields(self):
        session = _mock_session()
        p = _profile()
        repo = UserProfileRepository(session)

        result = await repo.update_profile(
            p, nickname="NewName", intro="NewIntro", avatar_id=10
        )
        assert result.nickname == "NewName"
        assert result.intro == "NewIntro"
        assert result.avatar_id == 10

    @pytest.mark.anyio
    async def test_update_profile_partial(self):
        session = _mock_session()
        p = _profile()
        repo = UserProfileRepository(session)

        result = await repo.update_profile(p, nickname="NewName")
        assert result.nickname == "NewName"
        assert result.intro == "Hello"
        assert result.avatar_id == 5


# ---------------------------------------------------------------------------
# UserFollowingRepository
# ---------------------------------------------------------------------------


class TestUserFollowingRepository:
    @pytest.mark.anyio
    async def test_count_followers(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = UserFollowingRepository(session)

        assert await repo.count_followers(1) == 5

    @pytest.mark.anyio
    async def test_count_following(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = UserFollowingRepository(session)

        assert await repo.count_following(1) == 3

    @pytest.mark.anyio
    async def test_is_following_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = UserFollowingRepository(session)

        assert await repo.is_following(1, 2) is True

    @pytest.mark.anyio
    async def test_is_following_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserFollowingRepository(session)

        assert await repo.is_following(1, 2) is False

    @pytest.mark.anyio
    async def test_add_follow(self):
        session = _mock_session()
        repo = UserFollowingRepository(session)

        await repo.add_follow(1, 2)
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_soft_delete_follow_success(self):
        session = _mock_session()
        rel = SimpleNamespace(follower_id=1, followee_id=2, deleted_at=None)
        session.execute.return_value = _mock_scalar(rel)
        repo = UserFollowingRepository(session)

        result = await repo.soft_delete_follow(1, 2)
        assert result is True
        assert rel.deleted_at is not None

    @pytest.mark.anyio
    async def test_soft_delete_follow_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserFollowingRepository(session)

        result = await repo.soft_delete_follow(1, 2)
        assert result is False


# ---------------------------------------------------------------------------
# UserRealNameRepository
# ---------------------------------------------------------------------------


class TestUserRealNameRepository:
    @pytest.mark.anyio
    async def test_has_identity_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = UserRealNameRepository(session)

        assert await repo.has_identity(1) is True

    @pytest.mark.anyio
    async def test_has_identity_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = UserRealNameRepository(session)

        assert await repo.has_identity(1) is False

    @pytest.mark.anyio
    async def test_get_identity(self):
        session = _mock_session()
        identity = _identity()
        session.execute.return_value = _mock_scalar(identity)
        repo = UserRealNameRepository(session)

        result = await repo.get_identity(1)
        assert result is identity

    @pytest.mark.anyio
    async def test_upsert_identity_create_new(self):
        session = _mock_session()
        # get_identity returns None
        session.execute.return_value = _mock_scalar(None)
        repo = UserRealNameRepository(session)

        result = await repo.upsert_identity(
            1,
            real_name="Zhang San",
            student_id="2024001",
            grade="2024",
            major="CS",
            class_name="Class-1",
        )
        assert result.real_name == "Zhang San"
        assert result.student_id == "2024001"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_upsert_identity_update_existing(self):
        session = _mock_session()
        existing = _identity()
        session.execute.return_value = _mock_scalar(existing)
        repo = UserRealNameRepository(session)

        result = await repo.upsert_identity(
            1,
            real_name="Li Si",
            student_id="2024002",
            grade="2025",
            major="Math",
            class_name="Class-2",
        )
        assert result is existing
        assert result.real_name == "Li Si"
        assert result.student_id == "2024002"
        assert result.major == "Math"

    @pytest.mark.anyio
    async def test_create_access_log(self):
        session = _mock_session()
        repo = UserRealNameRepository(session)

        result = await repo.create_access_log(
            accessor_id=2,
            target_id=1,
            access_reason="grading",
            ip_address="127.0.0.1",
            access_type="view",
            module_type="task",
            module_entity_id=50,
        )
        assert result.accessor_id == 2
        assert result.target_id == 1
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_list_access_logs(self):
        session = _mock_session()
        log = SimpleNamespace(id=1, target_id=1, accessor_id=2)
        session.execute.side_effect = [
            _mock_scalars([log]),
            _mock_scalar_one(1),
        ]
        repo = UserRealNameRepository(session)

        rows, total = await repo.list_access_logs(target_id=1, limit=10, offset=0)
        assert rows == [log]
        assert total == 1


# ---------------------------------------------------------------------------
# UserStatisticsRepository
# ---------------------------------------------------------------------------


class TestUserStatisticsRepository:
    @pytest.mark.anyio
    async def test_count_teams(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = UserStatisticsRepository(session)

        assert await repo.count_teams(1) == 3

    @pytest.mark.anyio
    async def test_count_task_memberships(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = UserStatisticsRepository(session)

        assert await repo.count_task_memberships(1) == 5

    @pytest.mark.anyio
    async def test_count_knowledge_entries(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(10)
        repo = UserStatisticsRepository(session)

        assert await repo.count_knowledge_entries(1) == 10

    @pytest.mark.anyio
    async def test_count_submissions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(7)
        repo = UserStatisticsRepository(session)

        assert await repo.count_submissions(1) == 7

    @pytest.mark.anyio
    async def test_count_questions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(4)
        repo = UserStatisticsRepository(session)

        assert await repo.count_questions(1) == 4

    @pytest.mark.anyio
    async def test_count_answers(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(6)
        repo = UserStatisticsRepository(session)

        assert await repo.count_answers(1) == 6

    @pytest.mark.anyio
    async def test_aggregate(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_scalar_one(3),  # teams
            _mock_scalar_one(5),  # tasks
            _mock_scalar_one(10),  # knowledge
            _mock_scalar_one(7),  # submissions
            _mock_scalar_one(4),  # questions
            _mock_scalar_one(6),  # answers
        ]
        repo = UserStatisticsRepository(session)

        result = await repo.aggregate(1)
        assert result == {
            "teamCount": 3,
            "taskParticipationCount": 5,
            "knowledgeCount": 10,
            "submissionCount": 7,
            "questionCount": 4,
            "answerCount": 6,
        }
