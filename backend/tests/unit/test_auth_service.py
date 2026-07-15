from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.common.auth import create_access_token, create_refresh_token, decode_token
from app.core.errors import AuthenticationRequiredError


class TestJWTService:
    def test_create_access_token(self) -> None:
        user_id = 123
        token = create_access_token(user_id)
        assert token is not None
        assert len(token) > 0

    def test_create_refresh_token(self) -> None:
        user_id = 123
        token = create_refresh_token(user_id)
        assert token is not None
        assert len(token) > 0

    def test_decode_access_token(self) -> None:
        user_id = 123
        token = create_access_token(user_id)
        payload = decode_token(token)

        assert payload is not None
        assert payload.get("sub") == str(user_id)
        assert payload.get("type") == "access"

    def test_decode_refresh_token(self) -> None:
        user_id = 123
        token = create_refresh_token(user_id)
        payload = decode_token(token)

        assert payload is not None
        assert payload.get("sub") == str(user_id)
        assert payload.get("type") == "refresh"

    def test_invalid_token_raises_error(self) -> None:
        with pytest.raises(AuthenticationRequiredError):
            decode_token("invalid.token.here")

    def test_expired_token_raises_error(self) -> None:
        with patch("app.common.auth.settings") as mock_settings:
            mock_settings.jwt_secret = "test-secret"
            mock_settings.access_token_expires_seconds = -1

            import jwt

            from app.core.config import settings

            payload = {
                "sub": "123",
                "type": "access",
                "exp": datetime.now(UTC) - timedelta(hours=1),
            }
            expired_token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

            with pytest.raises(AuthenticationRequiredError):
                decode_token(expired_token)

    def test_token_contains_user_id(self) -> None:
        user_id = 456
        token = create_access_token(user_id)
        payload = decode_token(token)

        assert int(payload.get("sub")) == user_id

    def test_different_users_get_different_tokens(self) -> None:
        token1 = create_access_token(123)
        token2 = create_access_token(456)
        assert token1 != token2


class TestUserAuthService:
    @pytest.fixture
    def mock_repos(self):
        from app.domain.user.repositories import (
            UserFollowingRepository,
            UserProfileRepository,
            UserRepository,
            UserStatisticsRepository,
        )

        return {
            "user_repo": AsyncMock(spec=UserRepository),
            "profile_repo": AsyncMock(spec=UserProfileRepository),
            "follow_repo": AsyncMock(spec=UserFollowingRepository),
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
