"""
Dual-endpoint contract tests for Notification API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Prerequisites:
- Kotlin backend running at KOTLIN_BASE_URL (default: http://localhost:8080)
- TEST_USER_TOKEN and TEST_USER_ID environment variables configured
- Both backends connected to the same database (or synced test data)

Run with:
    pytest tests/contract/test_notifications_dual.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import DualEndpointTester, ResponseComparator


@pytest.mark.anyio
class TestNotificationsDualEndpoint:
    """Dual-endpoint comparison tests for Notification APIs."""

    async def test_unread_count_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """
        Both backends should return the same structure for unread count.
        Note: The actual count may differ if data isn't synced.
        """
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/notifications/unread-count",
            compare_structure=True,
            compare_values=False,
        )

        assert kotlin_resp.status_code == 200, f"Kotlin returned {kotlin_resp.status_code}"
        assert python_resp.status_code == 200, f"Python returned {python_resp.status_code}"

        if diffs:
            pytest.fail("Structure differences found:\n" + "\n".join(diffs))

        k_data = kotlin_resp.json()["data"]
        p_data = python_resp.json()["data"]

        assert "count" in k_data, "Kotlin missing 'count' field"
        assert "count" in p_data, "Python missing 'count' field"
        assert isinstance(k_data["count"], int)
        assert isinstance(p_data["count"], int)

    async def test_list_notifications_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """
        Both backends should return the same list structure.
        """
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/notifications",
            params={"pageSize": 5},
            compare_structure=True,
        )

        assert kotlin_resp.status_code == 200, f"Kotlin returned {kotlin_resp.status_code}"
        assert python_resp.status_code == 200, f"Python returned {python_resp.status_code}"

        k_data = kotlin_resp.json()["data"]
        p_data = python_resp.json()["data"]

        assert "notifications" in k_data
        assert "notifications" in p_data

        if k_data["notifications"] and p_data["notifications"]:
            p_first = p_data["notifications"][0]

            required_fields = {"id", "type", "read", "createdAt"}
            p_fields = set(p_first.keys())

            missing_in_python = required_fields - p_fields
            if missing_in_python:
                pytest.fail(f"Python notification missing fields: {missing_in_python}")

    async def test_pagination_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """
        Both backends should return pagination metadata.
        """
        kotlin_resp, python_resp, _ = await dual_tester.get(
            "/notifications",
            params={"pageSize": 2},
        )

        assert kotlin_resp.status_code == 200
        assert python_resp.status_code == 200

        p_data = python_resp.json()["data"]

        assert "page" in p_data, "Python should have 'page' field"

        p_page = p_data["page"]
        required_page_fields = {"pageStart", "pageSize", "hasMore", "nextStart", "total"}
        missing = required_page_fields - set(p_page.keys())
        if missing:
            pytest.fail(f"Python pagination missing fields: {missing}")


@pytest.mark.anyio
class TestNotificationsKotlinBaseline:
    """
    Baseline tests against Kotlin backend.
    These establish the expected behavior that Python should match.
    """

    async def test_unread_count_returns_integer(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: unread count should return an integer."""
        resp = await kotlin_client.get(
            "/notifications/unread-count",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data["count"], int)
        assert data["count"] >= 0

    async def test_list_with_type_filter(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: list should support type filter."""
        resp = await kotlin_client.get(
            "/notifications",
            params={"type": "TEAM_INVITATION", "pageSize": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "notifications" in data

        for notification in data["notifications"]:
            assert notification.get("type") == "TEAM_INVITATION"

    async def test_list_with_read_filter(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: list should support read filter."""
        resp = await kotlin_client.get(
            "/notifications",
            params={"read": "false", "pageSize": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]

        for notification in data["notifications"]:
            assert notification.get("read") is False

    async def test_invalid_notification_id_returns_error(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: invalid notification ID should return 4xx error."""
        resp = await kotlin_client.get(
            "/notifications/999999999",
            headers=auth_headers,
        )
        assert resp.status_code in (400, 404, 422)


@pytest.mark.anyio
class TestNotificationsPythonParity:
    """
    Parity tests: verify Python matches Kotlin behavior.
    """

    async def test_unread_count_same_structure_as_kotlin(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python unread count should have same structure as Kotlin."""
        k_resp = await kotlin_client.get(
            "/notifications/unread-count",
            headers=auth_headers,
        )
        p_resp = await python_client.get(
            "/notifications/unread-count",
            headers=auth_headers,
        )

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        diffs = response_comparator.compare_structure(k_resp.json(), p_resp.json())
        if diffs:
            pytest.fail("Structure mismatch:\n" + "\n".join(diffs))

    async def test_type_filter_works_same_as_kotlin(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should support the same type filter as Kotlin."""
        params = {"type": "TEAM_INVITATION", "pageSize": 10}

        k_resp = await kotlin_client.get("/notifications", params=params, headers=auth_headers)
        p_resp = await python_client.get("/notifications", params=params, headers=auth_headers)

        assert (
            k_resp.status_code == p_resp.status_code
        ), f"Status mismatch: Kotlin={k_resp.status_code}, Python={p_resp.status_code}"

        if k_resp.status_code == 200 and p_resp.status_code == 200:
            p_notifications = p_resp.json()["data"]["notifications"]

            for n in p_notifications:
                assert (
                    n.get("type") == "TEAM_INVITATION"
                ), f"Python returned wrong type: {n.get('type')}"

    async def test_read_filter_works_same_as_kotlin(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should support the same read filter as Kotlin."""
        params = {"read": "false", "pageSize": 10}

        k_resp = await kotlin_client.get("/notifications", params=params, headers=auth_headers)
        p_resp = await python_client.get("/notifications", params=params, headers=auth_headers)

        assert k_resp.status_code == p_resp.status_code

        if p_resp.status_code == 200:
            p_notifications = p_resp.json()["data"]["notifications"]
            for n in p_notifications:
                assert n.get("read") is False, f"Python returned read notification: {n.get('id')}"

    async def test_invalid_id_error_similar_to_kotlin(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return similar error for invalid ID."""
        k_resp = await kotlin_client.get("/notifications/999999999", headers=auth_headers)
        p_resp = await python_client.get("/notifications/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404, 422)
        assert p_resp.status_code in (400, 404, 422)
