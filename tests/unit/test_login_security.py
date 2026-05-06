from unittest.mock import AsyncMock

import pytest


class TestLoginRateLimiter:
    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def rate_limiter(self, mock_redis):
        from app.domain.user.login_security import LoginRateLimiter

        return LoginRateLimiter(mock_redis)

    @pytest.mark.anyio
    async def test_not_locked_initially(self, rate_limiter, mock_redis) -> None:
        mock_redis.exists.return_value = 0
        assert await rate_limiter.is_locked_out("testuser") is False

    @pytest.mark.anyio
    async def test_locked_after_exists(self, rate_limiter, mock_redis) -> None:
        mock_redis.exists.return_value = 1
        assert await rate_limiter.is_locked_out("testuser") is True

    @pytest.mark.anyio
    async def test_record_failed_attempt_increments(self, rate_limiter, mock_redis) -> None:
        mock_redis.incr.return_value = 1
        attempts = await rate_limiter.record_failed_attempt("testuser")
        assert attempts == 1
        mock_redis.expire.assert_called_once()

    @pytest.mark.anyio
    async def test_lockout_after_max_attempts(self, rate_limiter, mock_redis) -> None:
        mock_redis.incr.return_value = 5
        await rate_limiter.record_failed_attempt("testuser")
        mock_redis.setex.assert_called_once()

    @pytest.mark.anyio
    async def test_clear_attempts(self, rate_limiter, mock_redis) -> None:
        await rate_limiter.clear_attempts("testuser")
        mock_redis.delete.assert_called_once()

    @pytest.mark.anyio
    async def test_get_attempt_count(self, rate_limiter, mock_redis) -> None:
        mock_redis.get.return_value = b"3"
        count = await rate_limiter.get_attempt_count("testuser")
        assert count == 3

    @pytest.mark.anyio
    async def test_get_attempt_count_none(self, rate_limiter, mock_redis) -> None:
        mock_redis.get.return_value = None
        count = await rate_limiter.get_attempt_count("testuser")
        assert count == 0


class TestTOTPService:
    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def totp_service(self, mock_redis):
        from app.domain.user.login_security import TOTPService

        return TOTPService(mock_redis)

    def test_generate_secret(self, totp_service) -> None:
        secret = totp_service.generate_secret()
        assert len(secret) == 32

    def test_get_provisioning_uri(self, totp_service) -> None:
        from urllib.parse import unquote

        secret = "JBSWY3DPEHPK3PXP"
        uri = totp_service.get_provisioning_uri(secret, "test@example.com")
        assert "otpauth://totp/" in uri
        assert "test@example.com" in unquote(uri)
        assert "Cheese" in uri

    def test_verify_code_valid(self, totp_service) -> None:
        import pyotp

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert totp_service.verify_code(secret, code) is True

    def test_verify_code_invalid(self, totp_service) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        assert totp_service.verify_code(secret, "000000") is False

    @pytest.mark.anyio
    async def test_start_2fa_setup(self, totp_service, mock_redis) -> None:
        result = await totp_service.start_2fa_setup(123, "test@example.com")
        assert "secret" in result
        assert "provisioningUri" in result
        mock_redis.setex.assert_called_once()

    @pytest.mark.anyio
    async def test_is_2fa_enabled_true(self, totp_service, mock_redis) -> None:
        mock_redis.exists.return_value = 1
        assert await totp_service.is_2fa_enabled(123) is True

    @pytest.mark.anyio
    async def test_is_2fa_enabled_false(self, totp_service, mock_redis) -> None:
        mock_redis.exists.return_value = 0
        assert await totp_service.is_2fa_enabled(123) is False

    @pytest.mark.anyio
    async def test_disable_2fa(self, totp_service, mock_redis) -> None:
        mock_redis.delete.return_value = 1
        result = await totp_service.disable_2fa(123)
        assert result is True


class TestSessionManager:
    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def session_manager(self, mock_redis):
        from app.domain.user.login_security import SessionManager

        return SessionManager(mock_redis)

    @pytest.mark.anyio
    async def test_create_session(self, session_manager, mock_redis) -> None:
        session_id = await session_manager.create_session(
            user_id=123,
            device_info="Chrome",
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0",
        )
        assert session_id is not None
        assert len(session_id) == 36
        mock_redis.hset.assert_called()
        mock_redis.sadd.assert_called()

    @pytest.mark.anyio
    async def test_get_session(self, session_manager, mock_redis) -> None:
        mock_redis.hgetall.return_value = {
            b"session_id": b"test-id",
            b"user_id": b"123",
        }
        session = await session_manager.get_session("test-id")
        assert session is not None
        assert session["session_id"] == "test-id"

    @pytest.mark.anyio
    async def test_get_session_not_found(self, session_manager, mock_redis) -> None:
        mock_redis.hgetall.return_value = {}
        session = await session_manager.get_session("nonexistent")
        assert session is None

    @pytest.mark.anyio
    async def test_list_user_sessions(self, session_manager, mock_redis) -> None:
        mock_redis.smembers.return_value = {b"session-1", b"session-2"}
        mock_redis.hgetall.side_effect = [
            {b"session_id": b"session-1", b"last_active_at": b"2024-01-01T00:00:00"},
            {b"session_id": b"session-2", b"last_active_at": b"2024-01-02T00:00:00"},
        ]
        sessions = await session_manager.list_user_sessions(123)
        assert len(sessions) == 2

    @pytest.mark.anyio
    async def test_revoke_session_success(self, session_manager, mock_redis) -> None:
        mock_redis.hgetall.return_value = {
            b"session_id": b"test-id",
            b"user_id": b"123",
        }
        result = await session_manager.revoke_session("test-id", 123)
        assert result is True
        mock_redis.delete.assert_called()

    @pytest.mark.anyio
    async def test_revoke_session_not_found(self, session_manager, mock_redis) -> None:
        mock_redis.hgetall.return_value = {}
        result = await session_manager.revoke_session("nonexistent", 123)
        assert result is False

    @pytest.mark.anyio
    async def test_revoke_session_wrong_user(self, session_manager, mock_redis) -> None:
        from app.core.errors import ForbiddenError

        mock_redis.hgetall.return_value = {
            b"session_id": b"test-id",
            b"user_id": b"456",
        }
        with pytest.raises(ForbiddenError):
            await session_manager.revoke_session("test-id", 123)


class TestPasswordResetService:
    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def reset_service(self, mock_redis):
        from app.domain.user.login_security import PasswordResetService

        return PasswordResetService(mock_redis)

    def test_generate_reset_token(self, reset_service) -> None:
        token = reset_service.generate_reset_token(username="testuser")
        assert len(token) > 32

    @pytest.mark.anyio
    async def test_create_reset_token(self, reset_service, mock_redis) -> None:
        token = await reset_service.create_reset_token(123, "test@example.com", username="testuser")
        assert token is not None
        mock_redis.hset.assert_called()
        mock_redis.expire.assert_called()

    @pytest.mark.anyio
    async def test_validate_reset_token_valid(self, reset_service, mock_redis) -> None:
        mock_redis.hgetall.return_value = {
            b"user_id": b"123",
            b"email": b"test@example.com",
        }
        data = await reset_service.validate_reset_token("valid-token")
        assert data is not None
        assert data["user_id"] == "123"

    @pytest.mark.anyio
    async def test_validate_reset_token_invalid(self, reset_service, mock_redis) -> None:
        mock_redis.hgetall.return_value = {}
        data = await reset_service.validate_reset_token("invalid-token")
        assert data is None

    @pytest.mark.anyio
    async def test_consume_reset_token(self, reset_service, mock_redis) -> None:
        mock_redis.hgetall.return_value = {
            b"user_id": b"123",
            b"email": b"test@example.com",
        }
        data = await reset_service.consume_reset_token("valid-token")
        assert data is not None
        mock_redis.delete.assert_called()
