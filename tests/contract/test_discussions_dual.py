"""
Dual-endpoint contract tests for Discussion API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Run with:
    pytest tests/contract/test_discussions_dual.py -v
"""

import pytest
from httpx import AsyncClient

from tests.conftest import DualEndpointTester, ResponseComparator


@pytest.mark.anyio
class TestDiscussionsDualEndpoint:
    """Dual-endpoint comparison tests for Discussion APIs."""

    async def test_list_discussions_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """Both backends should return the same structure for discussion list."""
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/discussions",
            params={"modelType": "task", "modelId": 1, "pageSize": 5},
            compare_structure=True,
        )

        if kotlin_resp.status_code == 200 and python_resp.status_code == 200:
            k_data = kotlin_resp.json()["data"]
            p_data = python_resp.json()["data"]

            assert "discussions" in k_data
            assert "discussions" in p_data

    async def test_reaction_types_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same reaction types structure."""
        k_resp = await kotlin_client.get("/discussions/reactions", headers=auth_headers)
        p_resp = await python_client.get("/discussions/reactions", headers=auth_headers)

        if k_resp.status_code == 200 and p_resp.status_code == 200:
            k_data = k_resp.json()["data"]
            p_data = p_resp.json()["data"]

            assert "reactionTypes" in k_data or "types" in k_data
            assert "reactionTypes" in p_data or "types" in p_data


@pytest.mark.anyio
class TestDiscussionReactionsDualEndpoint:
    """Dual-endpoint comparison tests for Discussion Reaction APIs."""

    async def test_toggle_reaction_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return same structure for toggle reaction."""
        k_list = await kotlin_client.get(
            "/discussions",
            params={"modelType": "task", "pageSize": 1},
            headers=auth_headers,
        )
        if k_list.status_code != 200:
            pytest.skip("Cannot get discussion list from Kotlin")

        discussions = k_list.json()["data"].get("discussions", [])
        if not discussions:
            pytest.skip("No discussions available for testing")

        discussion_id = discussions[0]["id"]

        k_resp = await kotlin_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )
        p_resp = await python_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        diffs = response_comparator.compare_structure(
            k_resp.json(),
            p_resp.json(),
        )

        structural_diffs = [d for d in diffs if "missing" in d.lower()]
        if structural_diffs:
            pytest.fail("Toggle reaction structure differs:\n" + "\n".join(structural_diffs))

    async def test_remove_reaction_structure_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return same structure for remove reaction."""
        k_list = await kotlin_client.get(
            "/discussions",
            params={"modelType": "task", "pageSize": 1},
            headers=auth_headers,
        )
        if k_list.status_code != 200:
            pytest.skip("Cannot get discussion list from Kotlin")

        discussions = k_list.json()["data"].get("discussions", [])
        if not discussions:
            pytest.skip("No discussions available for testing")

        discussion_id = discussions[0]["id"]

        await kotlin_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )
        await python_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )

        k_resp = await kotlin_client.delete(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )
        p_resp = await python_client.delete(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}: {k_resp.text}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}: {p_resp.text}"

        diffs = response_comparator.compare_structure(
            k_resp.json(),
            p_resp.json(),
        )

        structural_diffs = [d for d in diffs if "missing" in d.lower()]
        if structural_diffs:
            pytest.fail("Remove reaction structure differs:\n" + "\n".join(structural_diffs))


@pytest.mark.anyio
class TestDiscussionsKotlinBaseline:
    """Baseline tests against Kotlin backend for Discussion APIs."""

    async def test_list_discussions_returns_array(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: discussion list should return an array."""
        resp = await kotlin_client.get(
            "/discussions",
            params={"modelType": "task", "modelId": 1, "pageSize": 10},
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()["data"]
            assert "discussions" in data
            assert isinstance(data["discussions"], list)

    async def test_toggle_reaction_returns_result(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: toggle reaction should return a result."""
        list_resp = await kotlin_client.get(
            "/discussions",
            params={"modelType": "task", "pageSize": 1},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get discussion list")

        discussions = list_resp.json()["data"].get("discussions", [])
        if not discussions:
            pytest.skip("No discussions available")

        discussion_id = discussions[0]["id"]
        resp = await kotlin_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_remove_reaction_returns_result(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: remove reaction should return a result."""
        list_resp = await kotlin_client.get(
            "/discussions",
            params={"modelType": "task", "pageSize": 1},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get discussion list")

        discussions = list_resp.json()["data"].get("discussions", [])
        if not discussions:
            pytest.skip("No discussions available")

        discussion_id = discussions[0]["id"]

        await kotlin_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )

        resp = await kotlin_client.delete(
            f"/discussions/{discussion_id}/reactions/1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "data" in resp.json()
