"""
Dual-endpoint contract tests for Passkey/WebAuthn API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Note: Full WebAuthn flow requires actual authenticators, so these tests
focus on challenge generation and structure comparison.

Run with:
    pytest tests/contract/test_passkey_dual.py -v
"""

import pytest
from httpx import AsyncClient

from tests.conftest import ResponseComparator


@pytest.mark.anyio
class TestPasskeyDualEndpoint:
    """Dual-endpoint comparison tests for Passkey APIs."""

    async def test_register_challenge_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for registration challenge."""
        k_resp = await kotlin_client.post(
            "/users/auth/passkey/register/challenge",
            headers=auth_headers,
        )
        p_resp = await python_client.post(
            "/users/auth/passkey/register/challenge",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}: {k_resp.text}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}: {p_resp.text}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "options" in k_data
        assert "options" in p_data

        k_options = k_data["options"]
        p_options = p_data["options"]

        required_fields = {"challenge", "rp", "user"}
        k_fields = set(k_options.keys())
        p_fields = set(p_options.keys())

        assert required_fields <= k_fields, f"Kotlin options missing: {required_fields - k_fields}"
        assert required_fields <= p_fields, f"Python options missing: {required_fields - p_fields}"

    async def test_authenticate_challenge_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for authentication challenge."""
        k_resp = await kotlin_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )
        p_resp = await python_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}: {k_resp.text}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}: {p_resp.text}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "options" in k_data
        assert "options" in p_data

        k_options = k_data["options"]
        p_options = p_data["options"]

        required_fields = {"challenge", "rpId", "timeout"}
        k_fields = set(k_options.keys())
        p_fields = set(p_options.keys())

        assert required_fields <= k_fields, f"Kotlin options missing: {required_fields - k_fields}"
        assert required_fields <= p_fields, f"Python options missing: {required_fields - p_fields}"

    async def test_list_passkeys_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        test_user_id: str,
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for passkey list."""
        user_id = test_user_id

        k_resp = await kotlin_client.get(
            f"/users/{user_id}/passkeys",
            headers=auth_headers,
        )
        p_resp = await python_client.get(
            f"/users/{user_id}/passkeys",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}: {k_resp.text}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}: {p_resp.text}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "passkeys" in k_data
        assert "passkeys" in p_data
        assert isinstance(k_data["passkeys"], list)
        assert isinstance(p_data["passkeys"], list)


@pytest.mark.anyio
class TestPasskeyKotlinBaseline:
    """Baseline tests against Kotlin backend for Passkey APIs."""

    async def test_register_challenge_returns_options(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: register challenge should return WebAuthn options."""
        resp = await kotlin_client.post(
            "/users/auth/passkey/register/challenge",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rp" in options
        assert "user" in options

    async def test_authenticate_challenge_returns_options(
        self,
        kotlin_client: AsyncClient,
    ) -> None:
        """Kotlin: authenticate challenge should return WebAuthn options."""
        resp = await kotlin_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rpId" in options

    async def test_list_passkeys_returns_array(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
        test_user_id: str,
    ) -> None:
        """Kotlin: passkey list should return an array."""
        resp = await kotlin_client.get(
            f"/users/{test_user_id}/passkeys",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "passkeys" in data
        assert isinstance(data["passkeys"], list)

    async def test_register_verify_invalid_challenge_fails(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: invalid challenge should return error."""
        resp = await kotlin_client.post(
            "/users/auth/passkey/register/verify",
            json={
                "challenge": "invalid-challenge-xyz",
                "credential": {"id": "test", "type": "public-key"},
            },
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    async def test_authenticate_verify_invalid_challenge_fails(
        self,
        kotlin_client: AsyncClient,
    ) -> None:
        """Kotlin: invalid authentication challenge should return error."""
        resp = await kotlin_client.post(
            "/users/auth/passkey/authenticate/verify",
            json={
                "challenge": "invalid-challenge-xyz",
                "credential": {"id": "test", "type": "public-key"},
            },
        )
        assert resp.status_code in (400, 422)


@pytest.mark.anyio
class TestPasskeyPythonParity:
    """Parity tests: verify Python matches Kotlin behavior for Passkey."""

    async def test_register_challenge_same_fields(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python register challenge should have same fields as Kotlin."""
        k_resp = await kotlin_client.post(
            "/users/auth/passkey/register/challenge",
            headers=auth_headers,
        )
        p_resp = await python_client.post(
            "/users/auth/passkey/register/challenge",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_options = k_resp.json()["data"]["options"]
        p_options = p_resp.json()["data"]["options"]

        k_fields = set(k_options.keys())
        p_fields = set(p_options.keys())

        missing_in_python = k_fields - p_fields
        if missing_in_python:
            pytest.fail(f"Python missing fields: {missing_in_python}")

    async def test_authenticate_challenge_same_fields(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Python authenticate challenge should have same fields as Kotlin."""
        k_resp = await kotlin_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )
        p_resp = await python_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_options = k_resp.json()["data"]["options"]
        p_options = p_resp.json()["data"]["options"]

        k_fields = set(k_options.keys())
        p_fields = set(p_options.keys())

        missing_in_python = k_fields - p_fields
        if missing_in_python:
            pytest.fail(f"Python missing fields: {missing_in_python}")

    async def test_invalid_challenge_same_error_code(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return similar error code for invalid challenge."""
        k_resp = await kotlin_client.post(
            "/users/auth/passkey/register/verify",
            json={
                "challenge": "invalid-test-challenge",
                "credential": {"id": "test", "type": "public-key"},
            },
            headers=auth_headers,
        )
        p_resp = await python_client.post(
            "/users/auth/passkey/register/verify",
            json={
                "challenge": "invalid-test-challenge",
                "credential": {"id": "test", "type": "public-key"},
            },
            headers=auth_headers,
        )

        assert k_resp.status_code in (400, 422)
        assert p_resp.status_code in (400, 422)

    async def test_delete_nonexistent_passkey_same_behavior(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        test_user_id: str,
    ) -> None:
        """Python should return same status for deleting nonexistent passkey."""
        k_resp = await kotlin_client.delete(
            f"/users/{test_user_id}/passkeys/nonexistent-id-12345",
            headers=auth_headers,
        )
        p_resp = await python_client.delete(
            f"/users/{test_user_id}/passkeys/nonexistent-id-12345",
            headers=auth_headers,
        )

        assert k_resp.status_code == p_resp.status_code, (
            f"Status code mismatch: Kotlin={k_resp.status_code}, Python={p_resp.status_code}"
        )
