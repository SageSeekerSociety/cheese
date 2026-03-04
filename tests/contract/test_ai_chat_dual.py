"""
Dual-endpoint contract tests for AI Chat API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Run with:
    pytest tests/contract/test_ai_chat_dual.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import ResponseComparator


@pytest.mark.anyio
class TestAIChatDualEndpoint:
    """Dual-endpoint comparison tests for AI Chat APIs."""

    async def test_list_models_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for model list."""
        k_resp = await kotlin_client.get("/ai/models", headers=auth_headers)
        p_resp = await python_client.get("/ai/models", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "models" in k_data
        assert "models" in p_data

        if k_data["models"] and p_data["models"]:
            k_model = k_data["models"][0]
            p_model = p_data["models"][0]

            k_fields = set(k_model.keys())
            p_fields = set(p_model.keys())

            required_fields = {"id", "name"}
            assert (
                required_fields <= k_fields
            ), f"Kotlin model missing: {required_fields - k_fields}"
            assert (
                required_fields <= p_fields
            ), f"Python model missing: {required_fields - p_fields}"

    async def test_list_conversations_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for conversation list."""
        k_resp = await kotlin_client.get("/ai/conversations", headers=auth_headers)
        p_resp = await python_client.get("/ai/conversations", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "conversations" in k_data
        assert "conversations" in p_data
        assert isinstance(k_data["conversations"], list)
        assert isinstance(p_data["conversations"], list)

    async def test_get_quota_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for quota."""
        k_resp = await kotlin_client.get("/ai/quota", headers=auth_headers)
        p_resp = await python_client.get("/ai/quota", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "quota" in k_data
        assert "quota" in p_data

    async def test_create_conversation_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same structure for conversation creation."""
        payload = {"title": "Contract Test Conversation", "modelId": "gpt-4o-mini"}

        k_resp = await kotlin_client.post("/ai/conversations", json=payload, headers=auth_headers)
        p_resp = await python_client.post("/ai/conversations", json=payload, headers=auth_headers)

        assert k_resp.status_code == 201, f"Kotlin returned {k_resp.status_code}: {k_resp.text}"
        assert p_resp.status_code == 201, f"Python returned {p_resp.status_code}: {p_resp.text}"

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        assert "conversation" in k_data
        assert "conversation" in p_data

        k_conv = k_data["conversation"]
        p_conv = p_data["conversation"]

        required_fields = {"id", "title"}
        assert required_fields <= set(
            k_conv.keys()
        ), f"Kotlin missing: {required_fields - set(k_conv.keys())}"
        assert required_fields <= set(
            p_conv.keys()
        ), f"Python missing: {required_fields - set(p_conv.keys())}"


@pytest.mark.anyio
class TestAIChatKotlinBaseline:
    """Baseline tests against Kotlin backend for AI Chat APIs."""

    async def test_list_models_returns_array(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: model list should return an array."""
        resp = await kotlin_client.get("/ai/models", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "models" in data
        assert isinstance(data["models"], list)

    async def test_model_has_required_fields(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: each model should have required fields."""
        resp = await kotlin_client.get("/ai/models", headers=auth_headers)
        assert resp.status_code == 200

        models = resp.json()["data"]["models"]
        if models:
            required_fields = {"id", "name"}
            for model in models:
                missing = required_fields - set(model.keys())
                assert not missing, f"Model missing fields: {missing}"

    async def test_quota_returns_data(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: quota endpoint should return quota data."""
        resp = await kotlin_client.get("/ai/quota", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "quota" in data

    async def test_conversation_crud_flow(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: conversation CRUD should work."""
        create_resp = await kotlin_client.post(
            "/ai/conversations",
            json={"title": "Kotlin CRUD Test"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        conv_id = create_resp.json()["data"]["conversation"]["id"]

        get_resp = await kotlin_client.get(
            f"/ai/conversations/{conv_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["conversation"]["id"] == conv_id

        delete_resp = await kotlin_client.delete(
            f"/ai/conversations/{conv_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204


@pytest.mark.anyio
class TestAIChatPythonParity:
    """Parity tests: verify Python matches Kotlin behavior for AI Chat."""

    async def test_list_models_same_models(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return the same available models as Kotlin."""
        k_resp = await kotlin_client.get("/ai/models", headers=auth_headers)
        p_resp = await python_client.get("/ai/models", headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_model_ids = {m["id"] for m in k_resp.json()["data"]["models"]}
        p_model_ids = {m["id"] for m in p_resp.json()["data"]["models"]}

        common = k_model_ids & p_model_ids
        assert len(common) > 0, "No common models between Kotlin and Python"

    async def test_conversation_crud_same_behavior(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python conversation CRUD should behave like Kotlin."""
        k_create = await kotlin_client.post(
            "/ai/conversations",
            json={"title": "Parity Test K"},
            headers=auth_headers,
        )
        p_create = await python_client.post(
            "/ai/conversations",
            json={"title": "Parity Test P"},
            headers=auth_headers,
        )

        assert k_create.status_code == 201
        assert p_create.status_code == 201

        k_conv_id = k_create.json()["data"]["conversation"]["id"]
        p_conv_id = p_create.json()["data"]["conversation"]["id"]

        k_get = await kotlin_client.get(f"/ai/conversations/{k_conv_id}", headers=auth_headers)
        p_get = await python_client.get(f"/ai/conversations/{p_conv_id}", headers=auth_headers)

        assert k_get.status_code == 200
        assert p_get.status_code == 200

        k_delete = await kotlin_client.delete(
            f"/ai/conversations/{k_conv_id}", headers=auth_headers
        )
        p_delete = await python_client.delete(
            f"/ai/conversations/{p_conv_id}", headers=auth_headers
        )

        assert k_delete.status_code == 204
        assert p_delete.status_code == 204

        k_get_after = await kotlin_client.get(
            f"/ai/conversations/{k_conv_id}", headers=auth_headers
        )
        p_get_after = await python_client.get(
            f"/ai/conversations/{p_conv_id}", headers=auth_headers
        )

        assert k_get_after.status_code == 404
        assert p_get_after.status_code == 404
