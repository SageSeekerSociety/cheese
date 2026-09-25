import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest

from app.common.auth import (
    create_access_token,
    get_current_user_id,
    verify_access_token,
)
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError


class TestAccessToken:
    def test_a_token_names_its_user_handle_and_session(self) -> None:
        sid = uuid.uuid4()
        claims = verify_access_token(create_access_token(123, "alice", sid=sid))

        assert claims is not None
        assert (claims.user_id, claims.handle, claims.sid) == (123, "alice", sid)

    def test_a_token_can_name_a_handle_alone(self) -> None:
        claims = verify_access_token(create_access_token(None, handle="alice"))

        assert claims is not None
        assert (claims.user_id, claims.handle) == (None, "alice")

    @pytest.mark.anyio
    async def test_a_handle_alone_does_not_pass_as_a_user(self) -> None:
        token = create_access_token(None, handle="alice")

        with pytest.raises(AuthenticationRequiredError):
            await get_current_user_id(authorization=f"Bearer {token}")

    def test_garbage_is_not_a_token(self) -> None:
        assert verify_access_token("invalid.token.here") is None
        assert verify_access_token("") is None

    def test_an_expired_token_is_refused(self) -> None:
        expired = jwt.encode(
            {
                "sub": "123",
                "type": "access",
                "exp": datetime.now(UTC) - timedelta(hours=1),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert verify_access_token(expired) is None

    def test_a_token_signed_elsewhere_is_refused(self) -> None:
        forged = jwt.encode(
            {"sub": "123", "type": "access"}, "not-our-secret", algorithm="HS256"
        )

        assert verify_access_token(forged) is None

    def test_a_token_of_another_type_is_not_an_access_token(self) -> None:
        other = jwt.encode(
            {"sub": "123", "type": "2fa_pending"},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert verify_access_token(other) is None


class TestUserAuthService:
    @pytest.fixture
    def mock_repos(self):
        from app.domain.user.repositories import (
            UserProfileRepository,
            UserRepository,
            UserStatisticsRepository,
        )

        return {
            "user_repo": AsyncMock(spec=UserRepository),
            "profile_repo": AsyncMock(spec=UserProfileRepository),
            "stats_repo": AsyncMock(spec=UserStatisticsRepository),
        }

    @pytest.fixture
    def auth_service(self, mock_repos):
        from app.domain.user.services import UserAuthService

        return UserAuthService(**mock_repos)

    @pytest.mark.anyio
    async def test_authenticate_valid_credentials(
        self, auth_service, mock_repos
    ) -> None:
        import bcrypt

        from app.domain.user.models import User, UserProfile

        password = "test123"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        mock_user = AsyncMock(spec=User)
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.hashed_password = hashed

        mock_profile = AsyncMock(spec=UserProfile)
        mock_profile.user_id = 1
        mock_profile.nickname = "Test User"

        mock_repos["user_repo"].get_by_username.return_value = mock_user
        mock_repos["profile_repo"].get_profile_by_user_id.return_value = mock_profile

        result = await auth_service.authenticate("testuser", password)
        assert result is not None
        user, profile = result
        assert user.id == 1
        assert profile.nickname == "Test User"

    @pytest.mark.anyio
    async def test_authenticate_invalid_password(
        self, auth_service, mock_repos
    ) -> None:
        import bcrypt

        from app.domain.user.models import User

        password = "correct_password"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        mock_user = AsyncMock(spec=User)
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.hashed_password = hashed

        mock_repos["user_repo"].get_by_username.return_value = mock_user

        result = await auth_service.authenticate("testuser", "wrong_password")
        assert result is None

    @pytest.mark.anyio
    async def test_authenticate_user_not_found(self, auth_service, mock_repos) -> None:
        mock_repos["user_repo"].get_by_username.return_value = None

        result = await auth_service.authenticate("nonexistent", "password")
        assert result is None

    @pytest.mark.anyio
    async def test_get_user_with_profile(self, auth_service, mock_repos) -> None:
        from app.domain.user.models import User, UserProfile

        mock_user = AsyncMock(spec=User)
        mock_user.id = 1

        mock_profile = AsyncMock(spec=UserProfile)
        mock_profile.user_id = 1

        mock_repos["user_repo"].get_by_id.return_value = mock_user
        mock_repos["profile_repo"].get_profile_by_user_id.return_value = mock_profile

        user, profile = await auth_service.get_user_with_profile(1)
        assert user.id == 1
        assert profile.user_id == 1

    @pytest.mark.anyio
    async def test_get_user_with_profile_not_found(
        self, auth_service, mock_repos
    ) -> None:
        mock_repos["user_repo"].get_by_id.return_value = None

        with pytest.raises(ValueError, match="User not found"):
            await auth_service.get_user_with_profile(999)

    @pytest.mark.anyio
    async def test_is_username_taken(self, auth_service, mock_repos) -> None:
        mock_repos["user_repo"].is_username_taken.return_value = True
        assert await auth_service.is_username_taken("taken") is True

        mock_repos["user_repo"].is_username_taken.return_value = False
        assert await auth_service.is_username_taken("available") is False

    @pytest.mark.anyio
    async def test_is_email_taken(self, auth_service, mock_repos) -> None:
        mock_repos["user_repo"].is_email_taken.return_value = True
        assert await auth_service.is_email_taken("taken@example.com") is True

        mock_repos["user_repo"].is_email_taken.return_value = False
        assert await auth_service.is_email_taken("available@example.com") is False
