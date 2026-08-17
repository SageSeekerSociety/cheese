"""Comprehensive unit tests for user domain services.

Covers:
  - app.domain.user.services        (UserService, UserProfileService, UserAuthService)
  - app.domain.user.realname_services (UserRealNameService)
  - app.domain.user.verification_service
    (generate_verification_code, EmailVerificationService)
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import BadRequestError, NotFoundError, UnprocessableEntityError
from app.domain.user.services import UserAuthService, UserProfileService, UserService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "hashed_password": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _profile(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 10,
        "user_id": 1,
        "nickname": "Alice",
        "intro": "Hello",
        "avatar_id": 5,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _identity(**overrides) -> SimpleNamespace:
    defaults = {
        "encrypted": False,
        "real_name": "Zhang San",
        "student_id": "2024001",
        "grade": "2024",
        "major": "CS",
        "class_name": "Class-1",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _access_log(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 100,
        "accessor_id": 2,
        "target_id": 1,
        "module_type": "task",
        "module_entity_id": 50,
        "access_reason": "grading",
        "ip_address": "127.0.0.1",
        "access_type": "view",
        "created_at": datetime(2025, 6, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _stats_dict(**overrides) -> dict:
    defaults = {
        "questionCount": 3,
        "answerCount": 7,
        "teamCount": 2,
        "taskParticipationCount": 4,
        "knowledgeCount": 1,
        "submissionCount": 5,
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# UserService
# ===========================================================================


class TestUserService:
    @pytest.fixture
    def profile_repo(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def service(self, profile_repo) -> UserService:
        return UserService(repo=profile_repo)

    @pytest.mark.anyio
    async def test_get_users_by_ids_delegates_to_repo(
        self, service, profile_repo
    ) -> None:
        p1 = _profile(user_id=1)
        p2 = _profile(user_id=2, nickname="Bob")
        profile_repo.get_profiles_by_user_ids.return_value = {1: p1, 2: p2}

        result = await service.get_users_by_ids([1, 2])

        profile_repo.get_profiles_by_user_ids.assert_awaited_once_with([1, 2])
        assert result == {1: p1, 2: p2}

    @pytest.mark.anyio
    async def test_get_users_by_ids_empty_list(self, service, profile_repo) -> None:
        profile_repo.get_profiles_by_user_ids.return_value = {}
        result = await service.get_users_by_ids([])
        assert result == {}


# ===========================================================================
# UserProfileService
# ===========================================================================


class TestUserProfileService:
    @pytest.fixture
    def profile_repo(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def service(self, profile_repo) -> UserProfileService:
        return UserProfileService(profile_repo=profile_repo)

    @pytest.mark.anyio
    async def test_update_profile_success(self, service, profile_repo) -> None:
        existing = _profile()
        profile_repo.get_profile_by_user_id.return_value = existing

        await service.update_profile(user_id=1, nickname="New", intro="Hi", avatar_id=9)

        profile_repo.get_profile_by_user_id.assert_awaited_once_with(1)
        profile_repo.update_profile.assert_awaited_once_with(
            existing, nickname="New", intro="Hi", avatar_id=9
        )

    @pytest.mark.anyio
    async def test_update_profile_partial_fields(self, service, profile_repo) -> None:
        existing = _profile()
        profile_repo.get_profile_by_user_id.return_value = existing

        await service.update_profile(user_id=1, nickname="Only Name")

        profile_repo.update_profile.assert_awaited_once_with(
            existing, nickname="Only Name", intro=None, avatar_id=None
        )

    @pytest.mark.anyio
    async def test_update_profile_not_found_raises(self, service, profile_repo) -> None:
        profile_repo.get_profile_by_user_id.return_value = None

        with pytest.raises(NotFoundError, match="User profile not found"):
            await service.update_profile(user_id=999, nickname="X")

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "nickname", ["李", "A", "小明", "a b", "李Lee_2026", "123456", "7"]
    )
    async def test_update_profile_accepts_short_nicknames(
        self, service, profile_repo, nickname
    ) -> None:
        existing = _profile()
        profile_repo.get_profile_by_user_id.return_value = existing

        await service.update_profile(user_id=1, nickname=nickname)

        profile_repo.update_profile.assert_awaited_once_with(
            existing, nickname=nickname, intro=None, avatar_id=None
        )

    @pytest.mark.anyio
    async def test_update_profile_trims_surrounding_whitespace(
        self, service, profile_repo
    ) -> None:
        existing = _profile()
        profile_repo.get_profile_by_user_id.return_value = existing

        await service.update_profile(user_id=1, nickname="  小明  ")

        profile_repo.update_profile.assert_awaited_once_with(
            existing, nickname="小明", intro=None, avatar_id=None
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("nickname", "message"),
        [
            ("", "must not be empty"),
            ("   ", "must not be empty"),
            ("!!!???", "at least one letter"),
            ("---", "at least one letter"),
            ("😀😀", "at least one letter"),
            ("A" * 51, "at most 50 characters"),
        ],
    )
    async def test_update_profile_rejects_unusable_nicknames(
        self, service, profile_repo, nickname, message
    ) -> None:
        profile_repo.get_profile_by_user_id.return_value = _profile()

        with pytest.raises(UnprocessableEntityError, match=message):
            await service.update_profile(user_id=1, nickname=nickname)

        profile_repo.update_profile.assert_not_awaited()

    @pytest.mark.anyio
    async def test_update_profile_without_nickname_skips_validation(
        self, service, profile_repo
    ) -> None:
        existing = _profile()
        profile_repo.get_profile_by_user_id.return_value = existing

        await service.update_profile(user_id=1, intro="Hi")

        profile_repo.update_profile.assert_awaited_once_with(
            existing, nickname=None, intro="Hi", avatar_id=None
        )


# ===========================================================================
# UserAuthService
# ===========================================================================


class TestUserAuthService:
    @pytest.fixture
    def repos(self) -> dict:
        return {
            "user_repo": AsyncMock(),
            "profile_repo": AsyncMock(),
            "follow_repo": AsyncMock(),
            "stats_repo": AsyncMock(),
        }

    @pytest.fixture
    def service(self, repos) -> UserAuthService:
        return UserAuthService(**repos)

    # --- authenticate ---

    @pytest.mark.anyio
    async def test_authenticate_success(self, service, repos) -> None:
        import bcrypt

        pw = "secret123"
        hashed = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = _user(hashed_password=hashed)
        profile = _profile()

        repos["user_repo"].get_by_username.return_value = user
        repos["profile_repo"].get_profile_by_user_id.return_value = profile

        result = await service.authenticate("alice", pw)

        assert result is not None
        assert result[0] is user
        assert result[1] is profile

    @pytest.mark.anyio
    async def test_authenticate_user_not_found(self, service, repos) -> None:
        repos["user_repo"].get_by_username.return_value = None
        assert await service.authenticate("nobody", "pw") is None

    @pytest.mark.anyio
    async def test_authenticate_no_hashed_password(self, service, repos) -> None:
        user = _user(hashed_password=None)
        repos["user_repo"].get_by_username.return_value = user
        assert await service.authenticate("alice", "pw") is None

    @pytest.mark.anyio
    async def test_authenticate_empty_hashed_password(self, service, repos) -> None:
        user = _user(hashed_password="")
        repos["user_repo"].get_by_username.return_value = user
        assert await service.authenticate("alice", "pw") is None

    @pytest.mark.anyio
    async def test_authenticate_wrong_password(self, service, repos) -> None:
        import bcrypt

        hashed = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode("utf-8")
        user = _user(hashed_password=hashed)
        repos["user_repo"].get_by_username.return_value = user

        assert await service.authenticate("alice", "wrong") is None

    @pytest.mark.anyio
    async def test_authenticate_missing_profile(self, service, repos) -> None:
        import bcrypt

        pw = "pass"
        hashed = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = _user(hashed_password=hashed)
        repos["user_repo"].get_by_username.return_value = user
        repos["profile_repo"].get_profile_by_user_id.return_value = None

        assert await service.authenticate("alice", pw) is None

    # --- get_user_with_profile ---

    @pytest.mark.anyio
    async def test_get_user_with_profile_success(self, service, repos) -> None:
        user = _user()
        profile = _profile()
        repos["user_repo"].get_by_id.return_value = user
        repos["profile_repo"].get_profile_by_user_id.return_value = profile

        u, p = await service.get_user_with_profile(1)
        assert u is user
        assert p is profile

    @pytest.mark.anyio
    async def test_get_user_with_profile_user_missing(self, service, repos) -> None:
        repos["user_repo"].get_by_id.return_value = None
        with pytest.raises(ValueError, match="User not found"):
            await service.get_user_with_profile(999)

    @pytest.mark.anyio
    async def test_get_user_with_profile_profile_missing(self, service, repos) -> None:
        repos["user_repo"].get_by_id.return_value = _user()
        repos["profile_repo"].get_profile_by_user_id.return_value = None
        with pytest.raises(ValueError, match="User profile not found"):
            await service.get_user_with_profile(1)

    # --- get_user_by_email ---

    @pytest.mark.anyio
    async def test_get_user_by_email_found(self, service, repos) -> None:
        user = _user(email="bob@example.com")
        repos["user_repo"].get_by_email.return_value = user
        assert await service.get_user_by_email("bob@example.com") is user

    @pytest.mark.anyio
    async def test_get_user_by_email_not_found(self, service, repos) -> None:
        repos["user_repo"].get_by_email.return_value = None
        assert await service.get_user_by_email("missing@example.com") is None

    # --- update_password ---

    @pytest.mark.anyio
    async def test_update_password(self, service, repos) -> None:
        await service.update_password(1, "newpass")

        repos["user_repo"].update_password.assert_awaited_once()
        call_args = repos["user_repo"].update_password.call_args
        assert call_args[0][0] == 1
        # The stored hash should be a bcrypt hash, not the plaintext password
        stored_hash = call_args[0][1]
        assert stored_hash.startswith("$2")

    # --- is_username_taken / is_email_taken ---

    @pytest.mark.anyio
    async def test_is_username_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = True
        assert await service.is_username_taken("taken") is True

    @pytest.mark.anyio
    async def test_is_username_not_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        assert await service.is_username_taken("free") is False

    @pytest.mark.anyio
    async def test_is_email_taken(self, service, repos) -> None:
        repos["user_repo"].is_email_taken.return_value = True
        assert await service.is_email_taken("x@x.com") is True

    @pytest.mark.anyio
    async def test_is_email_not_taken(self, service, repos) -> None:
        repos["user_repo"].is_email_taken.return_value = False
        assert await service.is_email_taken("y@y.com") is False

    # --- register_with_password ---

    @pytest.mark.anyio
    async def test_register_with_password_success(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = False
        new_user = _user(id=42)
        new_profile = _profile(user_id=42)
        repos["user_repo"].create_user.return_value = new_user
        repos["profile_repo"].create_profile.return_value = new_profile

        user, profile = await service.register_with_password(
            username="bob",
            nickname="Bobby",
            email="bob@example.com",
            password="pw123",
        )

        assert user is new_user
        assert profile is new_profile
        repos["user_repo"].create_user.assert_awaited_once()
        create_kw = repos["user_repo"].create_user.call_args.kwargs
        assert create_kw["username"] == "bob"
        assert create_kw["email"] == "bob@example.com"
        assert create_kw["hashed_password"].startswith("$2")
        repos["profile_repo"].create_profile.assert_awaited_once_with(
            user_id=42, nickname="Bobby", intro="", avatar_id=1
        )

    @pytest.mark.anyio
    async def test_register_with_password_custom_avatar(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = False
        repos["user_repo"].create_user.return_value = _user(id=50)
        repos["profile_repo"].create_profile.return_value = _profile(user_id=50)

        await service.register_with_password(
            username="carol",
            nickname="Carol",
            email="carol@example.com",
            password="pass",
            default_avatar_id=7,
        )
        repos["profile_repo"].create_profile.assert_awaited_once_with(
            user_id=50, nickname="Carol", intro="", avatar_id=7
        )

    @pytest.mark.anyio
    async def test_register_with_password_username_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = True
        with pytest.raises(ValueError, match="USERNAME_TAKEN"):
            await service.register_with_password(
                username="taken", nickname="N", email="e@e.com", password="pw"
            )

    @pytest.mark.anyio
    async def test_register_with_password_email_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = True
        with pytest.raises(ValueError, match="EMAIL_TAKEN"):
            await service.register_with_password(
                username="new", nickname="N", email="taken@e.com", password="pw"
            )

    # --- register_with_srp ---

    @pytest.mark.anyio
    async def test_register_with_srp_success(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = False
        new_user = _user(id=60)
        new_profile = _profile(user_id=60)
        repos["user_repo"].create_user.return_value = new_user
        repos["profile_repo"].create_profile.return_value = new_profile

        user, profile = await service.register_with_srp(
            username="dave",
            nickname="Dave",
            email="dave@example.com",
            srp_salt="salt123",
            srp_verifier="verifier456",
        )

        assert user is new_user
        assert profile is new_profile
        create_kw = repos["user_repo"].create_user.call_args.kwargs
        assert create_kw["hashed_password"] == "SRP:salt123:verifier456"
        repos["profile_repo"].create_profile.assert_awaited_once_with(
            user_id=60, nickname="Dave", intro="", avatar_id=1
        )

    @pytest.mark.anyio
    async def test_register_with_srp_custom_avatar(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = False
        repos["user_repo"].create_user.return_value = _user(id=61)
        repos["profile_repo"].create_profile.return_value = _profile(user_id=61)

        await service.register_with_srp(
            username="eve",
            nickname="Eve",
            email="eve@example.com",
            srp_salt="s",
            srp_verifier="v",
            default_avatar_id=3,
        )
        repos["profile_repo"].create_profile.assert_awaited_once_with(
            user_id=61, nickname="Eve", intro="", avatar_id=3
        )

    @pytest.mark.anyio
    async def test_register_with_srp_username_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = True
        with pytest.raises(ValueError, match="USERNAME_TAKEN"):
            await service.register_with_srp(
                username="taken",
                nickname="N",
                email="e@e.com",
                srp_salt="s",
                srp_verifier="v",
            )

    @pytest.mark.anyio
    async def test_register_with_srp_email_taken(self, service, repos) -> None:
        repos["user_repo"].is_username_taken.return_value = False
        repos["user_repo"].is_email_taken.return_value = True
        with pytest.raises(ValueError, match="EMAIL_TAKEN"):
            await service.register_with_srp(
                username="new",
                nickname="N",
                email="taken@e.com",
                srp_salt="s",
                srp_verifier="v",
            )

    # --- _base_user_dto (static method) ---

    def test_base_user_dto(self) -> None:
        user = _user(id=7, username="frank")
        profile = _profile(nickname="Frankie", avatar_id=3, intro="hi")
        result = UserAuthService._base_user_dto(user, profile)
        assert result == {
            "id": 7,
            "username": "frank",
            "nickname": "Frankie",
            "avatarId": 3,
            "intro": "hi",
        }

    # --- build_user_dto ---

    @pytest.mark.anyio
    async def test_build_user_dto_no_viewer(self, service, repos) -> None:
        user = _user(id=5, username="zara")
        profile = _profile(nickname="Zara", avatar_id=2, intro="hey")
        repos["follow_repo"].count_followers.return_value = 10
        repos["follow_repo"].count_following.return_value = 3
        repos["stats_repo"].aggregate.return_value = _stats_dict()

        dto = await service.build_user_dto(user, profile)

        assert dto["id"] == 5
        assert dto["nickname"] == "Zara"
        assert dto["fans_count"] == 10
        assert dto["follow_count"] == 3
        assert dto["question_count"] == 3
        assert dto["answer_count"] == 7
        assert dto["is_follow"] is False
        # is_following should NOT have been called when viewer_id is None
        repos["follow_repo"].is_following.assert_not_awaited()

    @pytest.mark.anyio
    async def test_build_user_dto_viewer_is_self(self, service, repos) -> None:
        user = _user(id=5)
        profile = _profile()
        repos["follow_repo"].count_followers.return_value = 0
        repos["follow_repo"].count_following.return_value = 0
        repos["stats_repo"].aggregate.return_value = _stats_dict()

        dto = await service.build_user_dto(user, profile, viewer_id=5)

        # Same user viewing themselves -> is_follow stays False, no repo call
        assert dto["is_follow"] is False
        repos["follow_repo"].is_following.assert_not_awaited()

    @pytest.mark.anyio
    async def test_build_user_dto_viewer_follows(self, service, repos) -> None:
        user = _user(id=5)
        profile = _profile()
        repos["follow_repo"].count_followers.return_value = 1
        repos["follow_repo"].count_following.return_value = 0
        repos["follow_repo"].is_following.return_value = True
        repos["stats_repo"].aggregate.return_value = _stats_dict()

        dto = await service.build_user_dto(user, profile, viewer_id=99)

        assert dto["is_follow"] is True
        repos["follow_repo"].is_following.assert_awaited_once_with(
            follower_id=99, followee_id=5
        )

    @pytest.mark.anyio
    async def test_build_user_dto_viewer_does_not_follow(self, service, repos) -> None:
        user = _user(id=5)
        profile = _profile()
        repos["follow_repo"].count_followers.return_value = 0
        repos["follow_repo"].count_following.return_value = 0
        repos["follow_repo"].is_following.return_value = False
        repos["stats_repo"].aggregate.return_value = _stats_dict()

        dto = await service.build_user_dto(user, profile, viewer_id=99)

        assert dto["is_follow"] is False

    @pytest.mark.anyio
    async def test_build_user_dto_all_stats_fields(self, service, repos) -> None:
        user = _user(id=1)
        profile = _profile()
        repos["follow_repo"].count_followers.return_value = 0
        repos["follow_repo"].count_following.return_value = 0
        stats = _stats_dict(
            questionCount=10,
            answerCount=20,
            teamCount=30,
            taskParticipationCount=40,
            knowledgeCount=50,
            submissionCount=60,
        )
        repos["stats_repo"].aggregate.return_value = stats

        dto = await service.build_user_dto(user, profile)

        assert dto["question_count"] == 10
        assert dto["answer_count"] == 20
        assert dto["team_count"] == 30
        assert dto["task_participation_count"] == 40
        assert dto["knowledge_count"] == 50
        assert dto["submission_count"] == 60


# ===========================================================================
# UserRealNameService
# ===========================================================================


class TestUserRealNameService:
    @pytest.fixture
    def session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def user_repo(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def profile_repo(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def realname_repo(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def service(self, session, user_repo, profile_repo, realname_repo):
        from app.domain.user.realname_services import UserRealNameService

        return UserRealNameService(
            session=session,
            user_repo=user_repo,
            profile_repo=profile_repo,
            realname_repo=realname_repo,
        )

    # --- _ensure_user_exists ---

    @pytest.mark.anyio
    async def test_ensure_user_exists_found(self, service, user_repo) -> None:
        user = _user(id=1)
        user_repo.get_by_id.return_value = user
        result = await service._ensure_user_exists(1)
        assert result is user

    @pytest.mark.anyio
    async def test_ensure_user_exists_not_found(self, service, user_repo) -> None:
        user_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError, match="Resource user not found"):
            await service._ensure_user_exists(999)

    # --- _identity_dict ---

    def test_identity_dict_no_decrypt(self, service) -> None:
        ident = _identity(encrypted=False)
        result = service._identity_dict(ident, decrypt=False)
        assert result == {
            "realName": "Zhang San",
            "studentId": "2024001",
            "grade": "2024",
            "major": "CS",
            "className": "Class-1",
        }

    @patch(
        "app.domain.user.realname_services.decrypt_text",
        side_effect=lambda v: f"DEC({v})",
    )
    def test_identity_dict_decrypt_encrypted(self, mock_decrypt, service) -> None:
        ident = _identity(
            encrypted=True,
            real_name="enc_name",
            student_id="enc_sid",
            grade="enc_grade",
            major="enc_major",
            class_name="enc_class",
        )
        result = service._identity_dict(ident, decrypt=True)
        assert result == {
            "realName": "DEC(enc_name)",
            "studentId": "DEC(enc_sid)",
            "grade": "DEC(enc_grade)",
            "major": "DEC(enc_major)",
            "className": "DEC(enc_class)",
        }

    def test_identity_dict_decrypt_not_encrypted(self, service) -> None:
        """When decrypt=True but identity.encrypted=False, raw values are returned."""
        ident = _identity(encrypted=False)
        result = service._identity_dict(ident, decrypt=True)
        assert result["realName"] == "Zhang San"

    # --- _mask_name ---

    def test_mask_name_normal(self, service) -> None:
        assert service._mask_name("Zhang San") == "Z********"

    def test_mask_name_single_char(self, service) -> None:
        assert service._mask_name("A") == "A"

    def test_mask_name_two_chars(self, service) -> None:
        assert service._mask_name("AB") == "A*"

    def test_mask_name_empty(self, service) -> None:
        assert service._mask_name("") == ""

    # --- _mask_student_id ---

    def test_mask_student_id_normal(self, service) -> None:
        assert service._mask_student_id("2024001") == "2*****1"

    def test_mask_student_id_two_chars(self, service) -> None:
        assert service._mask_student_id("AB") == "AB"

    def test_mask_student_id_one_char(self, service) -> None:
        assert service._mask_student_id("X") == "X"

    def test_mask_student_id_three_chars(self, service) -> None:
        assert service._mask_student_id("ABC") == "A*C"

    # --- get_user_identity ---

    @pytest.mark.anyio
    async def test_get_user_identity_success(
        self, service, user_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        ident = _identity(encrypted=False)
        realname_repo.get_identity.return_value = ident

        result = await service.get_user_identity(1)

        assert result["realName"] == "Zhang San"
        assert result["studentId"] == "2024001"

    @pytest.mark.anyio
    async def test_get_user_identity_user_missing(self, service, user_repo) -> None:
        user_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            await service.get_user_identity(999)

    @pytest.mark.anyio
    async def test_get_user_identity_identity_missing(
        self, service, user_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        realname_repo.get_identity.return_value = None
        with pytest.raises(NotFoundError, match="user real name identity not found"):
            await service.get_user_identity(1)

    # --- get_fuzzy_user_identity ---

    @pytest.mark.anyio
    async def test_get_fuzzy_user_identity(
        self, service, user_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        ident = _identity(encrypted=False, real_name="Zhang San", student_id="2024001")
        realname_repo.get_identity.return_value = ident

        result = await service.get_fuzzy_user_identity(1)

        assert result["realName"] == "Z********"
        assert result["studentId"] == "2*****1"
        # Other fields should be unchanged
        assert result["grade"] == "2024"

    # --- create_or_update_user_identity ---

    @pytest.mark.anyio
    @patch(
        "app.domain.user.realname_services.encrypt_text",
        side_effect=lambda v: f"ENC({v})",
    )
    async def test_create_or_update_identity_success(
        self, mock_enc, service, user_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        returned_ident = _identity(
            encrypted=True,
            real_name="ENC(Zhang San)",
            student_id="ENC(2024001)",
            grade="ENC(2024)",
            major="ENC(CS)",
            class_name="ENC(Class-1)",
        )
        realname_repo.upsert_identity.return_value = returned_ident

        with patch(
            "app.domain.user.realname_services.decrypt_text",
            side_effect=lambda v: v.replace("ENC(", "").rstrip(")"),
        ):
            result = await service.create_or_update_user_identity(
                user_id=1,
                real_name="Zhang San",
                student_id="2024001",
                grade="2024",
                major="CS",
                class_name="Class-1",
            )

        realname_repo.upsert_identity.assert_awaited_once_with(
            user_id=1,
            real_name="ENC(Zhang San)",
            student_id="ENC(2024001)",
            grade="ENC(2024)",
            major="ENC(CS)",
            class_name="ENC(Class-1)",
            encrypted=True,
        )
        assert "realName" in result

    @pytest.mark.anyio
    async def test_create_or_update_identity_user_missing(
        self, service, user_repo
    ) -> None:
        user_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            await service.create_or_update_user_identity(
                user_id=999,
                real_name="A",
                student_id="B",
                grade="C",
                major="D",
                class_name="E",
            )

    @pytest.mark.anyio
    async def test_create_or_update_identity_missing_fields(
        self, service, user_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        with pytest.raises(BadRequestError, match="All real-name fields are required"):
            await service.create_or_update_user_identity(
                user_id=1,
                real_name="",
                student_id="2024001",
                grade="2024",
                major="CS",
                class_name="Class-1",
            )

    @pytest.mark.anyio
    async def test_create_or_update_identity_all_empty(
        self, service, user_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        with pytest.raises(BadRequestError):
            await service.create_or_update_user_identity(
                user_id=1,
                real_name="",
                student_id="",
                grade="",
                major="",
                class_name="",
            )

    # --- log_access ---

    @pytest.mark.anyio
    async def test_log_access_success(self, service, user_repo, realname_repo) -> None:
        user_repo.get_by_id.return_value = _user()
        expected_log = _access_log()
        realname_repo.create_access_log.return_value = expected_log

        result = await service.log_access(
            accessor_id=2,
            target_id=1,
            access_reason="grading",
            access_type="view",
            ip_address="127.0.0.1",
            module_type="task",
            module_entity_id=50,
        )

        assert result is expected_log
        realname_repo.create_access_log.assert_awaited_once_with(
            accessor_id=2,
            target_id=1,
            access_reason="grading",
            ip_address="127.0.0.1",
            access_type="view",
            module_type="task",
            module_entity_id=50,
        )

    @pytest.mark.anyio
    async def test_log_access_without_module(
        self, service, user_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user()
        realname_repo.create_access_log.return_value = _access_log()

        await service.log_access(
            accessor_id=2,
            target_id=1,
            access_reason="review",
            access_type="view",
            ip_address="10.0.0.1",
        )

        call_kw = realname_repo.create_access_log.call_args.kwargs
        assert call_kw["module_type"] is None
        assert call_kw["module_entity_id"] is None

    @pytest.mark.anyio
    async def test_log_access_accessor_missing(self, service, user_repo) -> None:
        user_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            await service.log_access(
                accessor_id=999,
                target_id=1,
                access_reason="r",
                access_type="view",
                ip_address="1.2.3.4",
            )

    @pytest.mark.anyio
    async def test_log_access_target_missing(self, service, user_repo) -> None:
        # First call (accessor) succeeds, second call (target) fails
        user_repo.get_by_id.side_effect = [_user(id=2), None]
        with pytest.raises(NotFoundError):
            await service.log_access(
                accessor_id=2,
                target_id=999,
                access_reason="r",
                access_type="view",
                ip_address="1.2.3.4",
            )

    # --- get_access_logs ---

    @pytest.mark.anyio
    async def test_get_access_logs_basic(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user(id=1)
        log1 = _access_log(
            accessor_id=2,
            module_type="task",
            module_entity_id=50,
            access_type="view",
            ip_address="1.2.3.4",
            access_reason="grading",
        )
        realname_repo.list_access_logs.return_value = ([log1], 1)
        profile_repo.get_profiles_by_user_ids.return_value = {
            2: _profile(user_id=2, nickname="Bob", avatar_id=3, intro="hi")
        }
        # get_by_id will be called for user_id=1 (_ensure_user_exists) then for accessor 2  # noqa: E501
        accessor_user = _user(id=2, username="bob")
        user_repo.get_by_id.side_effect = [_user(id=1), accessor_user]

        logs, page = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=None
        )

        assert len(logs) == 1
        assert logs[0]["accessor"]["id"] == 2
        assert logs[0]["accessor"]["username"] == "bob"
        assert logs[0]["accessModuleType"] == "task"
        assert logs[0]["accessEntityId"] == 50
        assert logs[0]["accessEntityName"] is None
        assert logs[0]["accessType"] == "view"
        assert logs[0]["ipAddress"] == "1.2.3.4"
        assert logs[0]["accessReason"] == "grading"
        assert isinstance(logs[0]["accessTime"], int)

        assert page["pageStart"] == 0
        assert page["pageSize"] == 1
        assert page["hasMore"] is False
        assert page["nextStart"] is None
        assert page["total"] == 1

    @pytest.mark.anyio
    async def test_get_access_logs_page_size_clamped(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        """page_size is clamped to [1, 100]."""
        user_repo.get_by_id.return_value = _user(id=1)
        realname_repo.list_access_logs.return_value = ([], 0)
        profile_repo.get_profiles_by_user_ids.return_value = {}

        await service.get_access_logs(target_user_id=1, page_size=200, page_start=0)
        call_kw = realname_repo.list_access_logs.call_args.kwargs
        assert call_kw["limit"] == 100

        await service.get_access_logs(target_user_id=1, page_size=-5, page_start=0)
        call_kw = realname_repo.list_access_logs.call_args.kwargs
        assert call_kw["limit"] == 1

    @pytest.mark.anyio
    async def test_get_access_logs_page_start_none_defaults_zero(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user(id=1)
        realname_repo.list_access_logs.return_value = ([], 0)
        profile_repo.get_profiles_by_user_ids.return_value = {}

        _, page = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=None
        )
        assert page["pageStart"] == 0

    @pytest.mark.anyio
    async def test_get_access_logs_has_more(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user(id=1)
        log = _access_log(accessor_id=2)
        realname_repo.list_access_logs.return_value = ([log], 5)
        accessor_user = _user(id=2, username="bob")
        user_repo.get_by_id.side_effect = [_user(id=1), accessor_user]
        profile_repo.get_profiles_by_user_ids.return_value = {
            2: _profile(user_id=2, nickname="Bob")
        }

        _, page = await service.get_access_logs(
            target_user_id=1, page_size=2, page_start=0
        )

        assert page["hasMore"] is True
        assert page["nextStart"] == 1

    @pytest.mark.anyio
    async def test_get_access_logs_skips_missing_user_or_profile(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        """Logs where accessor user or profile is missing are silently skipped."""
        user_repo.get_by_id.return_value = _user(id=1)
        log1 = _access_log(accessor_id=2)
        log2 = _access_log(accessor_id=3)
        realname_repo.list_access_logs.return_value = ([log1, log2], 2)
        # accessor 2 has no profile, accessor 3 has no user
        profile_repo.get_profiles_by_user_ids.return_value = {
            3: _profile(user_id=3, nickname="Charlie")
        }
        user_repo.get_by_id.side_effect = [
            _user(id=1),
            _user(id=2, username="bob"),
            None,
        ]

        logs, page = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=0
        )

        # log1 skipped (no profile), log2 skipped (no user)
        assert len(logs) == 0
        assert page["pageSize"] == 0

    @pytest.mark.anyio
    async def test_get_access_logs_deduplicates_accessor_lookups(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        """When multiple logs have the same accessor_id, get_by_id is only called once."""  # noqa: E501
        user_repo.get_by_id.return_value = _user(id=1)
        log1 = _access_log(accessor_id=2)
        log2 = _access_log(accessor_id=2)
        realname_repo.list_access_logs.return_value = ([log1, log2], 2)
        accessor = _user(id=2, username="bob")
        user_repo.get_by_id.side_effect = [_user(id=1), accessor]
        profile_repo.get_profiles_by_user_ids.return_value = {
            2: _profile(user_id=2, nickname="Bob", avatar_id=3, intro="hi")
        }

        logs, _ = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=0
        )

        assert len(logs) == 2
        # get_by_id called twice total: once for _ensure_user_exists, once for accessor 2  # noqa: E501
        assert user_repo.get_by_id.await_count == 2

    @pytest.mark.anyio
    async def test_get_access_logs_user_not_found(self, service, user_repo) -> None:
        user_repo.get_by_id.return_value = None
        with pytest.raises(NotFoundError):
            await service.get_access_logs(
                target_user_id=999, page_size=10, page_start=0
            )

    @pytest.mark.anyio
    async def test_get_access_logs_empty(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        user_repo.get_by_id.return_value = _user(id=1)
        realname_repo.list_access_logs.return_value = ([], 0)
        profile_repo.get_profiles_by_user_ids.return_value = {}

        logs, page = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=0
        )

        assert logs == []
        assert page["hasMore"] is False
        assert page["nextStart"] is None
        assert page["total"] == 0

    @pytest.mark.anyio
    async def test_get_access_logs_next_start_none_at_boundary(
        self, service, user_repo, profile_repo, realname_repo
    ) -> None:
        """When offset + returned == total, nextStart should be None."""
        user_repo.get_by_id.return_value = _user(id=1)
        log = _access_log(accessor_id=2)
        realname_repo.list_access_logs.return_value = ([log], 1)
        user_repo.get_by_id.side_effect = [_user(id=1), _user(id=2, username="bob")]
        profile_repo.get_profiles_by_user_ids.return_value = {
            2: _profile(user_id=2, nickname="Bob")
        }

        _, page = await service.get_access_logs(
            target_user_id=1, page_size=10, page_start=0
        )

        assert page["hasMore"] is False
        assert page["nextStart"] is None


# ===========================================================================
# EmailVerificationService & generate_verification_code
# ===========================================================================


class TestGenerateVerificationCode:
    def test_default_length_six(self) -> None:
        from app.domain.user.verification_service import generate_verification_code

        code = generate_verification_code()
        assert len(code) == 6
        assert code.isdigit()

    def test_custom_length(self) -> None:
        from app.domain.user.verification_service import generate_verification_code

        code = generate_verification_code(length=8)
        assert len(code) == 8
        assert code.isdigit()

    def test_length_one(self) -> None:
        from app.domain.user.verification_service import generate_verification_code

        code = generate_verification_code(length=1)
        assert len(code) == 1
        assert code.isdigit()


class TestEmailVerificationService:
    @pytest.fixture
    def redis(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def sender(self) -> MagicMock:
        mock = MagicMock()
        mock.send = AsyncMock(return_value=True)
        return mock

    @pytest.fixture
    def service(self, redis, sender):
        from app.domain.user.verification_service import EmailVerificationService

        svc = EmailVerificationService(redis)
        svc._sender = sender
        return svc

    # --- send_verification_code ---

    @pytest.mark.anyio
    async def test_send_code_no_existing(self, service, redis, sender) -> None:
        redis.get.return_value = None
        sender.send.return_value = True

        result = await service.send_verification_code("test@example.com")

        assert result is True
        redis.setex.assert_awaited_once()
        call_args = redis.setex.call_args[0]
        assert call_args[0] == "cheese:email_verification:test@example.com"
        assert call_args[1] == 600  # VERIFICATION_CODE_TTL
        assert len(call_args[2]) == 6
        sender.send.assert_called_once()

    @pytest.mark.anyio
    async def test_send_code_existing_but_enough_time_passed(
        self, service, redis, sender
    ) -> None:
        redis.get.return_value = b"123456"
        redis.ttl.return_value = 500  # 500 < 600 - 60 = 540 -> enough time passed
        sender.send.return_value = True

        result = await service.send_verification_code("test@example.com")

        assert result is True
        redis.setex.assert_awaited_once()

    @pytest.mark.anyio
    async def test_send_code_too_soon_raises(self, service, redis) -> None:
        redis.get.return_value = b"123456"
        redis.ttl.return_value = 580  # 580 > 600 - 60 = 540 -> too soon

        with pytest.raises(
            BadRequestError, match="Please wait before requesting a new code"
        ):
            await service.send_verification_code("test@example.com")

    @pytest.mark.anyio
    async def test_send_code_exactly_at_boundary(self, service, redis, sender) -> None:
        """TTL exactly at threshold (540) should NOT raise."""
        redis.get.return_value = b"123456"
        redis.ttl.return_value = 540  # 540 == 600 - 60 -> not greater, so no raise
        sender.send.return_value = True

        result = await service.send_verification_code("test@example.com")
        assert result is True

    @pytest.mark.anyio
    async def test_send_code_email_send_fails(self, service, redis, sender) -> None:
        redis.get.return_value = None
        sender.send.return_value = False

        result = await service.send_verification_code("test@example.com")

        # Even though sender.send returns False, the method returns True
        assert result is True

    @pytest.mark.anyio
    async def test_send_code_email_content(self, service, redis, sender) -> None:
        """Verify the email is sent with the right subject and recipient."""
        redis.get.return_value = None
        sender.send.return_value = True

        await service.send_verification_code("user@mail.com")

        call_kwargs = sender.send.call_args.kwargs
        assert call_kwargs["to"] == "user@mail.com"
        assert call_kwargs["subject"] == "[Cheese] Email Verification Code"
        assert "body_html" in call_kwargs
        assert "body_text" in call_kwargs

    # --- verify_code ---

    @pytest.mark.anyio
    async def test_verify_code_success(self, service, redis) -> None:
        redis.get.return_value = b"654321"

        result = await service.verify_code("test@example.com", "654321")

        assert result is True
        redis.delete.assert_awaited_once_with(
            "cheese:email_verification:test@example.com"
        )

    @pytest.mark.anyio
    async def test_verify_code_wrong_code(self, service, redis) -> None:
        redis.get.return_value = b"654321"

        result = await service.verify_code("test@example.com", "000000")

        assert result is False
        redis.delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_verify_code_expired_no_stored(self, service, redis) -> None:
        redis.get.return_value = None

        result = await service.verify_code("test@example.com", "123456")

        assert result is False
        redis.delete.assert_not_awaited()

    # --- check_code_exists ---

    @pytest.mark.anyio
    async def test_check_code_exists_true(self, service, redis) -> None:
        redis.exists.return_value = 1

        assert await service.check_code_exists("test@example.com") is True

    @pytest.mark.anyio
    async def test_check_code_exists_false(self, service, redis) -> None:
        redis.exists.return_value = 0

        assert await service.check_code_exists("test@example.com") is False

    @pytest.mark.anyio
    async def test_check_code_exists_key_format(self, service, redis) -> None:
        redis.exists.return_value = 0

        await service.check_code_exists("foo@bar.com")

        redis.exists.assert_awaited_once_with("cheese:email_verification:foo@bar.com")
