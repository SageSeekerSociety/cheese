from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.passkey.services import PasskeyService

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cred_entity(**overrides):
    defaults = {
        "id": 1,
        "user_id": 42,
        "credential_id": "Y3JlZC1pZA",
        "public_key": b"\x01\x02\x03",
        "counter": 0,
        "device_type": "single_device",
        "backed_up": False,
        "transports": "internal,usb",
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo=None) -> tuple[PasskeyService, AsyncMock]:
    repo = repo or AsyncMock()
    with patch("app.domain.passkey.services.settings") as mock_settings:
        mock_settings.webauthn_rp_id = "localhost"
        mock_settings.webauthn_rp_name = "Cheese Community"
        mock_settings.webauthn_origin = "http://localhost:5173"
        svc = PasskeyService(repo)
    return svc, repo


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestPasskeyServiceInit:
    def test_stores_repo_and_reads_settings(self):
        repo = AsyncMock()
        svc, _ = _make_service(repo)
        assert svc._repo is repo
        assert svc._rp_id == "localhost"
        assert svc._rp_name == "Cheese Community"
        assert svc._origin == "http://localhost:5173"


# ---------------------------------------------------------------------------
# generate_registration_options
# ---------------------------------------------------------------------------


class TestGenerateRegistrationOptions:
    @pytest.mark.anyio
    async def test_returns_dict_with_required_keys(self):
        svc, repo = _make_service()
        repo.list_by_user.return_value = []

        result = await svc.generate_registration_options(
            user_id=42, username="alice", display_name="Alice"
        )

        repo.list_by_user.assert_awaited_once_with(42)
        assert "challenge" in result
        assert result["rp"]["id"] == "localhost"
        assert result["rp"]["name"] == "Cheese Community"
        assert result["user"]["name"] == "alice"
        assert result["user"]["displayName"] == "Alice"
        assert "pubKeyCredParams" in result
        assert "excludeCredentials" in result
        assert "authenticatorSelection" in result

    @pytest.mark.anyio
    async def test_display_name_defaults_to_username(self):
        svc, repo = _make_service()
        repo.list_by_user.return_value = []

        result = await svc.generate_registration_options(
            user_id=42, username="bob"
        )

        assert result["user"]["displayName"] == "bob"

    @pytest.mark.anyio
    async def test_excludes_existing_credentials(self):
        svc, repo = _make_service()
        existing = _cred_entity(credential_id="Y3JlZC0x")
        repo.list_by_user.return_value = [existing]

        result = await svc.generate_registration_options(
            user_id=42, username="alice"
        )

        assert len(result["excludeCredentials"]) == 1


# ---------------------------------------------------------------------------
# verify_registration
# ---------------------------------------------------------------------------


class TestVerifyRegistration:
    @pytest.mark.anyio
    async def test_successful_verification(self):
        svc, repo = _make_service()
        fake_verification = SimpleNamespace(
            credential_id=b"\x01\x02",
            credential_public_key=b"\x03\x04",
            sign_count=1,
            credential_device_type="multi_device",
            credential_backed_up=True,
        )
        created_cred = _cred_entity(
            id=10,
            device_type="multi_device",
            backed_up=True,
        )
        repo.create.return_value = created_cred

        with patch("app.domain.passkey.services.verify_registration_response", return_value=fake_verification):
            result = await svc.verify_registration(
                user_id=42,
                challenge="Y2hhbGxlbmdl",
                credential={"response": {"transports": ["internal"]}},
            )

        assert result["id"] == 10
        assert result["deviceType"] == "multi_device"
        assert result["backedUp"] is True
        assert "credentialId" in result
        assert "createdAt" in result
        repo.create.assert_awaited_once()

    @pytest.mark.anyio
    async def test_verification_failure_raises_bad_request(self):
        svc, repo = _make_service()

        with (
            patch(
                "app.domain.passkey.services.verify_registration_response",
                side_effect=Exception("invalid signature"),
            ),
            pytest.raises(BadRequestError, match="Passkey verification failed"),
        ):
            await svc.verify_registration(
                user_id=42,
                challenge="Y2hhbGxlbmdl",
                credential={"response": {}},
            )

    @pytest.mark.anyio
    async def test_missing_transports_defaults_to_empty(self):
        svc, repo = _make_service()
        fake_verification = SimpleNamespace(
            credential_id=b"\x01",
            credential_public_key=b"\x02",
            sign_count=0,
        )
        repo.create.return_value = _cred_entity()

        with patch("app.domain.passkey.services.verify_registration_response", return_value=fake_verification):
            await svc.verify_registration(
                user_id=42,
                challenge="Y2hhbGxlbmdl",
                credential={},
            )

        call_kwargs = repo.create.call_args[1]
        assert call_kwargs["transports"] == []
        # getattr fallback for device_type and backed_up
        assert call_kwargs["device_type"] == "single_device"
        assert call_kwargs["backed_up"] is False


# ---------------------------------------------------------------------------
# generate_authentication_options
# ---------------------------------------------------------------------------


class TestGenerateAuthenticationOptions:
    @pytest.mark.anyio
    async def test_without_user_id(self):
        svc, repo = _make_service()

        result = await svc.generate_authentication_options(user_id=None)

        repo.list_by_user.assert_not_awaited()
        assert "challenge" in result
        assert result["rpId"] == "localhost"
        assert result["allowCredentials"] == []
        assert "userVerification" in result

    @pytest.mark.anyio
    async def test_with_user_id_and_credentials(self):
        svc, repo = _make_service()
        cred = _cred_entity(credential_id="Y3JlZC0x", transports="internal,usb")
        repo.list_by_user.return_value = [cred]

        result = await svc.generate_authentication_options(user_id=42)

        repo.list_by_user.assert_awaited_once_with(42)
        assert len(result["allowCredentials"]) == 1

    @pytest.mark.anyio
    async def test_with_user_id_no_credentials(self):
        svc, repo = _make_service()
        repo.list_by_user.return_value = []

        result = await svc.generate_authentication_options(user_id=42)

        assert result["allowCredentials"] == []

    @pytest.mark.anyio
    async def test_credential_without_transports(self):
        svc, repo = _make_service()
        cred = _cred_entity(credential_id="Y3JlZC0x", transports=None)
        repo.list_by_user.return_value = [cred]

        result = await svc.generate_authentication_options(user_id=42)

        assert len(result["allowCredentials"]) == 1


# ---------------------------------------------------------------------------
# verify_authentication
# ---------------------------------------------------------------------------


class TestVerifyAuthentication:
    @pytest.mark.anyio
    async def test_successful_authentication(self):
        svc, repo = _make_service()
        stored = _cred_entity(user_id=42, public_key=b"\x01\x02", counter=5)
        repo.get_by_credential_id.return_value = stored

        fake_verification = SimpleNamespace(new_sign_count=6)

        with patch("app.domain.passkey.services.verify_authentication_response", return_value=fake_verification):
            user_id = await svc.verify_authentication(
                challenge="Y2hhbGxlbmdl",
                credential={"id": "Y3JlZC1pZA", "response": {}},
            )

        assert user_id == 42
        repo.get_by_credential_id.assert_awaited_once_with("Y3JlZC1pZA")
        repo.update_counter.assert_awaited_once_with(
            credential_id="Y3JlZC1pZA",
            counter=6,
        )

    @pytest.mark.anyio
    async def test_uses_rawId_when_id_missing(self):
        svc, repo = _make_service()
        stored = _cred_entity(user_id=42)
        repo.get_by_credential_id.return_value = stored
        fake_verification = SimpleNamespace(new_sign_count=1)

        with patch("app.domain.passkey.services.verify_authentication_response", return_value=fake_verification):
            await svc.verify_authentication(
                challenge="Y2hhbGxlbmdl",
                credential={"rawId": "cmF3LWlk", "response": {}},
            )

        repo.get_by_credential_id.assert_awaited_once_with("cmF3LWlk")

    @pytest.mark.anyio
    async def test_missing_credential_id_raises_bad_request(self):
        svc, repo = _make_service()

        with pytest.raises(BadRequestError, match="Missing credential ID"):
            await svc.verify_authentication(
                challenge="Y2hhbGxlbmdl",
                credential={"response": {}},
            )

    @pytest.mark.anyio
    async def test_passkey_not_found_raises_not_found(self):
        svc, repo = _make_service()
        repo.get_by_credential_id.return_value = None

        with pytest.raises(NotFoundError, match="Passkey not found"):
            await svc.verify_authentication(
                challenge="Y2hhbGxlbmdl",
                credential={"id": "dW5rbm93bg"},
            )

    @pytest.mark.anyio
    async def test_verification_failure_raises_bad_request(self):
        svc, repo = _make_service()
        stored = _cred_entity()
        repo.get_by_credential_id.return_value = stored

        with (
            patch(
                "app.domain.passkey.services.verify_authentication_response",
                side_effect=Exception("bad signature"),
            ),
            pytest.raises(BadRequestError, match="Passkey authentication failed"),
        ):
            await svc.verify_authentication(
                challenge="Y2hhbGxlbmdl",
                credential={"id": "Y3JlZC1pZA", "response": {}},
            )


# ---------------------------------------------------------------------------
# list_passkeys
# ---------------------------------------------------------------------------


class TestListPasskeys:
    @pytest.mark.anyio
    async def test_returns_formatted_list(self):
        svc, repo = _make_service()
        cred = _cred_entity(
            id=5,
            credential_id="Y3JlZC1pZA",
            device_type="multi_device",
            backed_up=True,
        )
        repo.list_by_user.return_value = [cred]

        result = await svc.list_passkeys(42)

        repo.list_by_user.assert_awaited_once_with(42)
        assert len(result) == 1
        assert result[0]["id"] == 5
        assert result[0]["credentialId"] == "Y3JlZC1pZA"
        assert result[0]["deviceType"] == "multi_device"
        assert result[0]["backedUp"] is True
        assert "createdAt" in result[0]
        assert "updatedAt" in result[0]

    @pytest.mark.anyio
    async def test_empty_list(self):
        svc, repo = _make_service()
        repo.list_by_user.return_value = []

        result = await svc.list_passkeys(42)

        assert result == []


# ---------------------------------------------------------------------------
# delete_passkey
# ---------------------------------------------------------------------------


class TestDeletePasskey:
    @pytest.mark.anyio
    async def test_delete_by_credential_id_succeeds(self):
        svc, repo = _make_service()
        repo.delete_by_credential_id.return_value = True

        result = await svc.delete_passkey(42, "Y3JlZC1pZA")

        assert result is True
        repo.delete_by_credential_id.assert_awaited_once_with("Y3JlZC1pZA", 42)
        repo.delete_by_id.assert_not_awaited()

    @pytest.mark.anyio
    async def test_fallback_to_delete_by_int_id(self):
        svc, repo = _make_service()
        repo.delete_by_credential_id.return_value = False
        repo.delete_by_id.return_value = True

        result = await svc.delete_passkey(42, "123")

        assert result is True
        repo.delete_by_credential_id.assert_awaited_once_with("123", 42)
        repo.delete_by_id.assert_awaited_once_with(123, 42)

    @pytest.mark.anyio
    async def test_fallback_delete_by_id_returns_false(self):
        svc, repo = _make_service()
        repo.delete_by_credential_id.return_value = False
        repo.delete_by_id.return_value = False

        result = await svc.delete_passkey(42, "456")

        assert result is False

    @pytest.mark.anyio
    async def test_non_numeric_credential_id_returns_false(self):
        svc, repo = _make_service()
        repo.delete_by_credential_id.return_value = False

        result = await svc.delete_passkey(42, "not-a-number")

        assert result is False
        repo.delete_by_id.assert_not_awaited()
