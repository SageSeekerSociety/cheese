from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.oauth.services import (
    GitHubProvider,
    GoogleProvider,
    OAuthProviderConfig,
    OAuthService,
    OAuthUserInfo,
)

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _github_config():
    return OAuthProviderConfig(
        id="github",
        name="GitHub",
        client_id="gh-client-id",
        client_secret="gh-secret",
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        redirect_url="https://example.com/callback/github",
        scope=["read:user", "user:email"],
    )


def _google_config():
    return OAuthProviderConfig(
        id="google",
        name="Google",
        client_id="goog-client-id",
        client_secret="goog-secret",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        redirect_url="https://example.com/callback/google",
        scope=["openid", "email", "profile"],
    )


def _connection(**overrides):
    defaults = {
        "id": 1,
        "user_id": 42,
        "provider_id": "github",
        "provider_user_id": "gh-123",
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo=None, redis=None) -> tuple[OAuthService, AsyncMock]:
    repo = repo or AsyncMock()
    svc = OAuthService(repo=repo, redis=redis)
    return svc, repo


# ---------------------------------------------------------------------------
# OAuthProviderConfig dataclass
# ---------------------------------------------------------------------------


class TestOAuthProviderConfig:
    def test_defaults(self):
        cfg = OAuthProviderConfig(
            id="test",
            name="Test",
            client_id="cid",
            client_secret="csec",
            authorization_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_url="https://redirect.example.com",
            scope=["read"],
        )
        assert cfg.user_info_url is None

    def test_with_user_info_url(self):
        cfg = OAuthProviderConfig(
            id="test",
            name="Test",
            client_id="cid",
            client_secret="csec",
            authorization_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_url="https://redirect.example.com",
            scope=["read"],
            user_info_url="https://info.example.com",
        )
        assert cfg.user_info_url == "https://info.example.com"


# ---------------------------------------------------------------------------
# OAuthUserInfo dataclass
# ---------------------------------------------------------------------------


class TestOAuthUserInfo:
    def test_defaults(self):
        info = OAuthUserInfo(id="123")
        assert info.id == "123"
        assert info.email is None
        assert info.name is None
        assert info.username is None
        assert info.preferred_username is None

    def test_full(self):
        info = OAuthUserInfo(
            id="456",
            email="test@example.com",
            name="Test User",
            username="testuser",
            preferred_username="testpref",
        )
        assert info.email == "test@example.com"
        assert info.name == "Test User"
        assert info.username == "testuser"
        assert info.preferred_username == "testpref"


# ---------------------------------------------------------------------------
# GitHubProvider.get_authorization_url (inherited from OAuthProvider)
# ---------------------------------------------------------------------------


class TestGitHubProviderAuthUrl:
    def test_without_state(self):
        provider = GitHubProvider(_github_config())
        url = provider.get_authorization_url()
        assert "client_id=gh-client-id" in url
        assert "redirect_uri=" in url
        assert "scope=read%3Auser+user%3Aemail" in url
        assert "response_type=code" in url
        assert "state=" not in url

    def test_with_state(self):
        provider = GitHubProvider(_github_config())
        url = provider.get_authorization_url(state="my-state")
        assert "state=my-state" in url


# ---------------------------------------------------------------------------
# GoogleProvider.get_authorization_url (overridden)
# ---------------------------------------------------------------------------


class TestGoogleProviderAuthUrl:
    def test_without_state(self):
        provider = GoogleProvider(_google_config())
        url = provider.get_authorization_url()
        assert "client_id=goog-client-id" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url
        assert "response_type=code" in url
        assert "state=" not in url

    def test_with_state(self):
        provider = GoogleProvider(_google_config())
        url = provider.get_authorization_url(state="goog-state")
        assert "state=goog-state" in url


# ---------------------------------------------------------------------------
# GitHubProvider.exchange_code
# ---------------------------------------------------------------------------


class TestGitHubProviderExchangeCode:
    @pytest.mark.anyio
    async def test_exchange_code(self):
        provider = GitHubProvider(_github_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok123"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            result = await provider.exchange_code("my-code")

        assert result == {"access_token": "tok123"}
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["data"]["code"] == "my-code"
        assert call_kwargs[1]["data"]["client_id"] == "gh-client-id"


# ---------------------------------------------------------------------------
# GitHubProvider.get_user_info
# ---------------------------------------------------------------------------


class TestGitHubProviderGetUserInfo:
    @pytest.mark.anyio
    async def test_with_email_in_profile(self):
        provider = GitHubProvider(_github_config())

        user_response = MagicMock()
        user_response.json.return_value = {
            "id": 12345,
            "email": "user@github.com",
            "name": "GitHub User",
            "login": "ghuser",
        }
        user_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = user_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("token123")

        assert info.id == "12345"
        assert info.email == "user@github.com"
        assert info.name == "GitHub User"
        assert info.username == "ghuser"
        assert info.preferred_username == "ghuser"

    @pytest.mark.anyio
    async def test_without_email_fetches_from_emails_endpoint(self):
        provider = GitHubProvider(_github_config())

        user_response = MagicMock()
        user_response.json.return_value = {
            "id": 12345,
            "email": None,
            "name": "GitHub User",
            "login": "ghuser",
        }
        user_response.raise_for_status = MagicMock()

        emails_response = MagicMock()
        emails_response.status_code = 200
        emails_response.json.return_value = [
            {"email": "secondary@github.com", "primary": False},
            {"email": "primary@github.com", "primary": True},
        ]

        mock_client = AsyncMock()
        mock_client.get.side_effect = [user_response, emails_response]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("token123")

        assert info.email == "primary@github.com"

    @pytest.mark.anyio
    async def test_without_email_and_emails_endpoint_fails(self):
        provider = GitHubProvider(_github_config())

        user_response = MagicMock()
        user_response.json.return_value = {
            "id": 12345,
            "email": None,
            "name": "GitHub User",
            "login": "ghuser",
        }
        user_response.raise_for_status = MagicMock()

        emails_response = MagicMock()
        emails_response.status_code = 403

        mock_client = AsyncMock()
        mock_client.get.side_effect = [user_response, emails_response]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("token123")

        assert info.email is None

    @pytest.mark.anyio
    async def test_without_email_no_primary_in_list(self):
        provider = GitHubProvider(_github_config())

        user_response = MagicMock()
        user_response.json.return_value = {
            "id": 12345,
            "email": None,
            "name": "GitHub User",
            "login": "ghuser",
        }
        user_response.raise_for_status = MagicMock()

        emails_response = MagicMock()
        emails_response.status_code = 200
        emails_response.json.return_value = [
            {"email": "other@github.com", "primary": False},
        ]

        mock_client = AsyncMock()
        mock_client.get.side_effect = [user_response, emails_response]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("token123")

        assert info.email is None


# ---------------------------------------------------------------------------
# GoogleProvider.exchange_code
# ---------------------------------------------------------------------------


class TestGoogleProviderExchangeCode:
    @pytest.mark.anyio
    async def test_exchange_code(self):
        provider = GoogleProvider(_google_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "goog-tok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            result = await provider.exchange_code("goog-code")

        assert result == {"access_token": "goog-tok"}
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["data"]["grant_type"] == "authorization_code"
        assert call_kwargs[1]["data"]["code"] == "goog-code"


# ---------------------------------------------------------------------------
# GoogleProvider.get_user_info
# ---------------------------------------------------------------------------


class TestGoogleProviderGetUserInfo:
    @pytest.mark.anyio
    async def test_with_email(self):
        provider = GoogleProvider(_google_config())

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "goog-id-1",
            "email": "user@gmail.com",
            "name": "Google User",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("goog-token")

        assert info.id == "goog-id-1"
        assert info.email == "user@gmail.com"
        assert info.name == "Google User"
        assert info.username == "user"

    @pytest.mark.anyio
    async def test_without_email(self):
        provider = GoogleProvider(_google_config())

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "goog-id-2",
            "name": "No Email User",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.domain.oauth.services.httpx.AsyncClient", return_value=mock_client):
            info = await provider.get_user_info("goog-token")

        assert info.email is None
        assert info.username is None


# ---------------------------------------------------------------------------
# OAuthService._initialize / _get_provider_config
# ---------------------------------------------------------------------------


class TestOAuthServiceInitialize:
    def test_initialize_with_no_enabled_providers(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = ""
            svc._initialize()
        assert svc._initialized is True
        assert svc._providers == {}

    def test_initialize_with_missing_attribute(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            # Simulate settings missing the oauth_enabled_providers attribute
            del mock_settings.oauth_enabled_providers
            svc._initialize()
        assert svc._initialized is True
        assert svc._providers == {}

    def test_initialize_with_github_provider(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = "github"
            mock_settings.oauth_github_client_id = "gh-cid"
            mock_settings.oauth_github_client_secret = "gh-sec"
            mock_settings.oauth_github_redirect_url = "https://example.com/callback/github"
            svc._initialize()
        assert "github" in svc._providers
        assert isinstance(svc._providers["github"], GitHubProvider)

    def test_initialize_with_google_provider(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = "google"
            mock_settings.oauth_google_client_id = "goog-cid"
            mock_settings.oauth_google_client_secret = "goog-sec"
            mock_settings.oauth_google_redirect_url = "https://example.com/callback/google"
            svc._initialize()
        assert "google" in svc._providers
        assert isinstance(svc._providers["google"], GoogleProvider)

    def test_initialize_with_multiple_providers(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = "github, google"
            mock_settings.oauth_github_client_id = "gh-cid"
            mock_settings.oauth_github_client_secret = "gh-sec"
            mock_settings.oauth_github_redirect_url = "https://example.com/callback/github"
            mock_settings.oauth_google_client_id = "goog-cid"
            mock_settings.oauth_google_client_secret = "goog-sec"
            mock_settings.oauth_google_redirect_url = "https://example.com/callback/google"
            svc._initialize()
        assert len(svc._providers) == 2

    def test_initialize_skips_incomplete_config(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = "github"
            mock_settings.oauth_github_client_id = "gh-cid"
            # Missing client_secret and redirect_url
            del mock_settings.oauth_github_client_secret
            del mock_settings.oauth_github_redirect_url
            svc._initialize()
        assert svc._providers == {}

    def test_initialize_skips_unknown_provider(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = "gitlab"
            mock_settings.oauth_gitlab_client_id = "gl-cid"
            mock_settings.oauth_gitlab_client_secret = "gl-sec"
            mock_settings.oauth_gitlab_redirect_url = "https://example.com/callback/gitlab"
            svc._initialize()
        # gitlab config returns None from _get_provider_config for unknown providers
        assert svc._providers == {}

    def test_initialize_called_twice_is_idempotent(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_enabled_providers = ""
            svc._initialize()
            # Second call should return early
            svc._initialized = True
            svc._providers["fake"] = "should not be cleared"
            svc._initialize()
        assert "fake" in svc._providers


# ---------------------------------------------------------------------------
# OAuthService._get_provider_config
# ---------------------------------------------------------------------------


class TestGetProviderConfig:
    def test_github_config(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_github_client_id = "cid"
            mock_settings.oauth_github_client_secret = "csec"
            mock_settings.oauth_github_redirect_url = "https://redirect.com"
            config = svc._get_provider_config("github")
        assert config is not None
        assert config.id == "github"
        assert config.name == "GitHub"
        assert config.client_id == "cid"
        assert "read:user" in config.scope

    def test_google_config(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_google_client_id = "cid"
            mock_settings.oauth_google_client_secret = "csec"
            mock_settings.oauth_google_redirect_url = "https://redirect.com"
            config = svc._get_provider_config("google")
        assert config is not None
        assert config.id == "google"
        assert config.name == "Google"
        assert "openid" in config.scope

    def test_unknown_provider_returns_none(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_gitlab_client_id = "cid"
            mock_settings.oauth_gitlab_client_secret = "csec"
            mock_settings.oauth_gitlab_redirect_url = "https://redirect.com"
            config = svc._get_provider_config("gitlab")
        assert config is None

    def test_missing_client_id_returns_none(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            del mock_settings.oauth_github_client_id
            mock_settings.oauth_github_client_secret = "csec"
            mock_settings.oauth_github_redirect_url = "https://redirect.com"
            config = svc._get_provider_config("github")
        assert config is None

    def test_missing_client_secret_returns_none(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_github_client_id = "cid"
            del mock_settings.oauth_github_client_secret
            mock_settings.oauth_github_redirect_url = "https://redirect.com"
            config = svc._get_provider_config("github")
        assert config is None

    def test_missing_redirect_url_returns_none(self):
        svc, _repo = _make_service()
        with patch("app.domain.oauth.services.settings") as mock_settings:
            mock_settings.oauth_github_client_id = "cid"
            mock_settings.oauth_github_client_secret = "csec"
            del mock_settings.oauth_github_redirect_url
            config = svc._get_provider_config("github")
        assert config is None


# ---------------------------------------------------------------------------
# OAuthService.get_providers_config
# ---------------------------------------------------------------------------


class TestGetProvidersConfig:
    def test_returns_provider_list(self):
        svc, _repo = _make_service()
        # Manually set up providers to avoid hitting real settings
        svc._initialized = True
        svc._providers = {
            "github": GitHubProvider(_github_config()),
            "google": GoogleProvider(_google_config()),
        }

        result = svc.get_providers_config()

        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"github", "google"}
        for r in result:
            assert "name" in r
            assert "scope" in r

    def test_empty_when_no_providers(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {}

        result = svc.get_providers_config()

        assert result == []


# ---------------------------------------------------------------------------
# OAuthService.get_provider
# ---------------------------------------------------------------------------


class TestGetProvider:
    def test_success(self):
        svc, _repo = _make_service()
        svc._initialized = True
        gh_provider = GitHubProvider(_github_config())
        svc._providers = {"github": gh_provider}

        result = svc.get_provider("github")

        assert result is gh_provider

    def test_not_found(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {}

        with pytest.raises(NotFoundError, match="OAuth provider 'gitlab' not found"):
            svc.get_provider("gitlab")


# ---------------------------------------------------------------------------
# OAuthService.generate_authorization_url
# ---------------------------------------------------------------------------


class TestGenerateAuthorizationUrl:
    def test_with_provided_state(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {"github": GitHubProvider(_github_config())}

        url = svc.generate_authorization_url("github", state="custom-state")

        assert "state=custom-state" in url

    def test_auto_generates_state_when_none(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {"github": GitHubProvider(_github_config())}

        url = svc.generate_authorization_url("github")

        assert "state=" in url

    def test_auto_generates_state_when_empty_string(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {"github": GitHubProvider(_github_config())}

        url = svc.generate_authorization_url("github", state="")

        # Empty string is falsy, so auto-generated state is used
        assert "state=" in url

    def test_provider_not_found(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {}

        with pytest.raises(NotFoundError):
            svc.generate_authorization_url("unknown")


# ---------------------------------------------------------------------------
# OAuthService.handle_callback
# ---------------------------------------------------------------------------


class TestHandleCallback:
    @pytest.mark.anyio
    async def test_success(self):
        svc, _repo = _make_service()
        svc._initialized = True

        mock_provider = AsyncMock()
        mock_provider.exchange_code.return_value = {"access_token": "tok123"}
        mock_provider.get_user_info.return_value = OAuthUserInfo(
            id="uid-1", email="user@test.com"
        )
        svc._providers = {"github": mock_provider}

        access_token, user_info = await svc.handle_callback("github", "auth-code")

        mock_provider.exchange_code.assert_awaited_once_with("auth-code")
        mock_provider.get_user_info.assert_awaited_once_with("tok123")
        assert access_token == "tok123"
        assert user_info.id == "uid-1"
        assert user_info.email == "user@test.com"

    @pytest.mark.anyio
    async def test_missing_access_token(self):
        svc, _repo = _make_service()
        svc._initialized = True

        mock_provider = AsyncMock()
        mock_provider.exchange_code.return_value = {"error": "bad_code"}
        svc._providers = {"github": mock_provider}

        with pytest.raises(BadRequestError, match="Failed to get access token"):
            await svc.handle_callback("github", "bad-code")

    @pytest.mark.anyio
    async def test_provider_not_found(self):
        svc, _repo = _make_service()
        svc._initialized = True
        svc._providers = {}

        with pytest.raises(NotFoundError):
            await svc.handle_callback("unknown", "code")


# ---------------------------------------------------------------------------
# OAuthService.get_connection_by_provider
# ---------------------------------------------------------------------------


class TestGetConnectionByProvider:
    @pytest.mark.anyio
    async def test_found(self):
        svc, repo = _make_service()
        conn = _connection(id=10, user_id=42, provider_id="github", provider_user_id="gh-123")
        repo.get_by_provider.return_value = conn

        result = await svc.get_connection_by_provider("github", "gh-123")

        repo.get_by_provider.assert_awaited_once_with("github", "gh-123")
        assert result is not None
        assert result["id"] == 10
        assert result["userId"] == 42
        assert result["providerId"] == "github"
        assert result["providerUserId"] == "gh-123"

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.get_by_provider.return_value = None

        result = await svc.get_connection_by_provider("github", "nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# OAuthService.create_connection
# ---------------------------------------------------------------------------


class TestCreateConnection:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        conn = _connection(id=99, user_id=5, provider_id="google", provider_user_id="goog-42")
        repo.create.return_value = conn

        result = await svc.create_connection(
            user_id=5,
            provider_id="google",
            provider_user_id="goog-42",
            raw_profile={"name": "User"},
            refresh_token="rt-token",
            token_expires=NOW,
        )

        repo.create.assert_awaited_once_with(
            user_id=5,
            provider_id="google",
            provider_user_id="goog-42",
            raw_profile={"name": "User"},
            refresh_token="rt-token",
            token_expires=NOW,
        )
        assert result["id"] == 99
        assert result["userId"] == 5

    @pytest.mark.anyio
    async def test_minimal_params(self):
        svc, repo = _make_service()
        conn = _connection(id=1)
        repo.create.return_value = conn

        result = await svc.create_connection(
            user_id=42,
            provider_id="github",
            provider_user_id="gh-123",
        )

        repo.create.assert_awaited_once_with(
            user_id=42,
            provider_id="github",
            provider_user_id="gh-123",
            raw_profile=None,
            refresh_token=None,
            token_expires=None,
        )
        assert result["id"] == 1


# ---------------------------------------------------------------------------
# OAuthService._connection_to_dict
# ---------------------------------------------------------------------------


class TestConnectionToDict:
    def test_with_created_at(self):
        svc, _repo = _make_service()
        conn = _connection(id=3, user_id=7, provider_id="github", provider_user_id="gh-99")

        result = svc._connection_to_dict(conn)

        assert result["id"] == 3
        assert result["userId"] == 7
        assert result["providerId"] == "github"
        assert result["providerUserId"] == "gh-99"
        assert result["createdAt"] == NOW.isoformat()

    def test_with_none_created_at(self):
        svc, _repo = _make_service()
        conn = _connection(created_at=None)

        result = svc._connection_to_dict(conn)

        assert result["createdAt"] is None


# ---------------------------------------------------------------------------
# OAuthService.list_user_connections
# ---------------------------------------------------------------------------


class TestListUserConnections:
    @pytest.mark.anyio
    async def test_returns_formatted_list(self):
        svc, repo = _make_service()
        conns = [
            _connection(id=1, provider_id="github", provider_user_id="gh-1"),
            _connection(id=2, provider_id="google", provider_user_id="goog-1"),
        ]
        repo.list_by_user.return_value = conns

        result = await svc.list_user_connections(42)

        repo.list_by_user.assert_awaited_once_with(42)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["providerId"] == "github"
        assert result[0]["providerName"] == "GitHub"
        assert result[0]["providerUserId"] == "gh-1"
        assert result[0]["connectedAt"] == NOW.isoformat()
        assert result[1]["providerName"] == "Google"

    @pytest.mark.anyio
    async def test_empty_list(self):
        svc, repo = _make_service()
        repo.list_by_user.return_value = []

        result = await svc.list_user_connections(42)

        assert result == []

    @pytest.mark.anyio
    async def test_unknown_provider_uses_id_as_name(self):
        svc, repo = _make_service()
        conn = _connection(id=3, provider_id="gitlab", provider_user_id="gl-1")
        repo.list_by_user.return_value = [conn]

        result = await svc.list_user_connections(42)

        assert result[0]["providerName"] == "gitlab"

    @pytest.mark.anyio
    async def test_none_created_at(self):
        svc, repo = _make_service()
        conn = _connection(id=1, created_at=None)
        repo.list_by_user.return_value = [conn]

        result = await svc.list_user_connections(42)

        assert result[0]["connectedAt"] is None


# ---------------------------------------------------------------------------
# OAuthService.delete_connection
# ---------------------------------------------------------------------------


class TestDeleteConnection:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.delete_by_id.return_value = True

        result = await svc.delete_connection(connection_id=1, user_id=42)

        repo.delete_by_id.assert_awaited_once_with(1, 42)
        assert result is True

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.delete_by_id.return_value = False

        result = await svc.delete_connection(connection_id=999, user_id=42)

        assert result is False


# ---------------------------------------------------------------------------
# OAuthService.store_oauth_state
# ---------------------------------------------------------------------------


class TestStoreOAuthState:
    @pytest.mark.anyio
    async def test_with_redis(self):
        redis = AsyncMock()
        svc, _repo = _make_service(redis=redis)

        await svc.store_oauth_state("state-token", {"provider": "github"})

        redis.set.assert_awaited_once()
        call_args = redis.set.call_args
        assert call_args[0][0] == "oauth_state:state-token"
        assert '"provider"' in call_args[0][1]
        assert call_args[1]["ex"] == 600

    @pytest.mark.anyio
    async def test_without_redis_is_noop(self):
        svc, _repo = _make_service(redis=None)

        # Should not raise
        await svc.store_oauth_state("state-token", {"provider": "github"})


# ---------------------------------------------------------------------------
# OAuthService.get_oauth_state
# ---------------------------------------------------------------------------


class TestGetOAuthState:
    @pytest.mark.anyio
    async def test_with_redis_found(self):
        redis = AsyncMock()
        redis.get.return_value = '{"provider": "github"}'
        svc, _repo = _make_service(redis=redis)

        result = await svc.get_oauth_state("state-token")

        redis.get.assert_awaited_once_with("oauth_state:state-token")
        redis.delete.assert_awaited_once_with("oauth_state:state-token")
        assert result == {"provider": "github"}

    @pytest.mark.anyio
    async def test_with_redis_not_found(self):
        redis = AsyncMock()
        redis.get.return_value = None
        svc, _repo = _make_service(redis=redis)

        result = await svc.get_oauth_state("missing-token")

        redis.get.assert_awaited_once()
        redis.delete.assert_not_awaited()
        assert result is None

    @pytest.mark.anyio
    async def test_without_redis_returns_none(self):
        svc, _repo = _make_service(redis=None)

        result = await svc.get_oauth_state("state-token")

        assert result is None
