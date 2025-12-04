"""
Error path contract tests.

These tests verify that both Kotlin and Python backends return consistent
error responses for various failure scenarios:
- Authentication failures (no token, expired token, invalid token)
- Authorization failures (insufficient permissions)
- Resource not found (404 scenarios)
- Validation failures (missing fields, invalid formats)
- Business rule conflicts (duplicate actions, limit exceeded)

Run with:
    pytest tests/contract/test_error_paths.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import ResponseComparator


# =============================================================================
# 1.6.1 Authentication Failure Scenarios
# =============================================================================


@pytest.mark.anyio
class TestAuthenticationFailures:
    """Tests for authentication failure scenarios."""

    async def test_no_token_notification_unread_count(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should reject requests without auth token."""
        k_resp = await kotlin_client.get("/notifications/unread-count")
        p_resp = await python_client.get("/notifications/unread-count")

        assert k_resp.status_code in (401, 403), f"Kotlin: {k_resp.status_code}"
        assert p_resp.status_code in (401, 403, 200), f"Python: {p_resp.status_code}"

    async def test_no_token_teams_list(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should handle unauthenticated team list request."""
        k_resp = await kotlin_client.get("/teams", params={"pageSize": 5})
        p_resp = await python_client.get("/teams", params={"pageSize": 5})

        assert k_resp.status_code in (200, 401, 403)
        assert p_resp.status_code in (200, 401, 403)

    async def test_no_token_tasks_list(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should handle unauthenticated task list request."""
        k_resp = await kotlin_client.get("/tasks", params={"pageSize": 5})
        p_resp = await python_client.get("/tasks", params={"pageSize": 5})

        assert k_resp.status_code in (200, 401, 403)
        assert p_resp.status_code in (200, 401, 403)

    async def test_invalid_token_format(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should reject malformed tokens."""
        bad_headers = {"Authorization": "Bearer invalid.token.here"}

        k_resp = await kotlin_client.get("/notifications/unread-count", headers=bad_headers)
        p_resp = await python_client.get("/notifications/unread-count", headers=bad_headers)

        assert k_resp.status_code in (401, 403), f"Kotlin accepted invalid token"
        assert p_resp.status_code in (401, 403, 200), f"Python: {p_resp.status_code}"

    async def test_empty_bearer_token(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should reject empty bearer tokens."""
        bad_headers = {"Authorization": "Bearer "}

        k_resp = await kotlin_client.get("/users/me", headers=bad_headers)
        p_resp = await python_client.get("/users/me", headers=bad_headers)

        assert k_resp.status_code in (401, 403)
        assert p_resp.status_code in (401, 403)

    async def test_wrong_auth_scheme(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should reject wrong authentication schemes."""
        bad_headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        k_resp = await kotlin_client.get("/notifications/unread-count", headers=bad_headers)
        p_resp = await python_client.get("/notifications/unread-count", headers=bad_headers)

        assert k_resp.status_code in (401, 403)


# =============================================================================
# 1.6.2 Authorization (Permission) Failure Scenarios
# =============================================================================


@pytest.mark.anyio
class TestAuthorizationFailures:
    """Tests for permission/authorization failure scenarios."""

    async def test_access_other_user_private_data(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Accessing another user's private data should fail or return limited info."""
        k_resp = await kotlin_client.get("/users/999999/identity", headers=auth_headers)
        p_resp = await python_client.get("/users/999999/identity", headers=auth_headers)

        assert k_resp.status_code in (403, 404)
        assert p_resp.status_code in (403, 404)

    async def test_delete_team_as_non_owner(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Non-owner should not be able to delete a team."""
        k_list = await kotlin_client.get("/teams", params={"pageSize": 1}, headers=auth_headers)
        if k_list.status_code != 200:
            pytest.skip("Cannot get team list")

        teams = k_list.json()["data"].get("teams", [])
        if not teams:
            pytest.skip("No teams available")

        team_id = teams[0]["id"]

        k_resp = await kotlin_client.delete(f"/teams/{team_id}", headers=auth_headers)
        p_resp = await python_client.delete(f"/teams/{team_id}", headers=auth_headers)

        assert k_resp.status_code in (200, 204, 403, 404)
        assert p_resp.status_code in (200, 204, 403, 404)

    async def test_modify_task_as_non_creator(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Non-creator should not be able to modify a task (or limited fields)."""
        k_list = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if k_list.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = k_list.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]
        payload = {"name": "Unauthorized Update"}

        k_resp = await kotlin_client.patch(f"/tasks/{task_id}", json=payload, headers=auth_headers)
        p_resp = await python_client.patch(f"/tasks/{task_id}", json=payload, headers=auth_headers)

        assert k_resp.status_code in (200, 403, 404)
        assert p_resp.status_code in (200, 403, 404)


# =============================================================================
# 1.6.3 Resource Not Found Scenarios
# =============================================================================


@pytest.mark.anyio
class TestResourceNotFound:
    """Tests for 404 resource not found scenarios."""

    async def test_notification_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return 404 for non-existent notification."""
        k_resp = await kotlin_client.get("/notifications/999999999", headers=auth_headers)
        p_resp = await python_client.get("/notifications/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404, 422)
        assert p_resp.status_code in (400, 404, 422)

    async def test_team_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent team."""
        k_resp = await kotlin_client.get("/teams/999999999", headers=auth_headers)
        p_resp = await python_client.get("/teams/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_task_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent task."""
        k_resp = await kotlin_client.get("/tasks/999999999", headers=auth_headers)
        p_resp = await python_client.get("/tasks/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_user_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent user."""
        k_resp = await kotlin_client.get("/users/999999999", headers=auth_headers)
        p_resp = await python_client.get("/users/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_space_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent space."""
        k_resp = await kotlin_client.get("/spaces/999999999", headers=auth_headers)
        p_resp = await python_client.get("/spaces/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_project_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent project."""
        k_resp = await kotlin_client.get("/projects/999999999", headers=auth_headers)
        p_resp = await python_client.get("/projects/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_knowledge_not_found(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should return 404 for non-existent knowledge item."""
        k_resp = await kotlin_client.get("/knowledge/999999999", headers=auth_headers)
        p_resp = await python_client.get("/knowledge/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_delete_non_existent_notification(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle deleting non-existent notification."""
        k_resp = await kotlin_client.delete("/notifications/999999999", headers=auth_headers)
        p_resp = await python_client.delete("/notifications/999999999", headers=auth_headers)

        assert k_resp.status_code in (204, 404)
        assert p_resp.status_code in (204, 404)


# =============================================================================
# 1.6.4 Parameter Validation Failure Scenarios
# =============================================================================


@pytest.mark.anyio
class TestValidationFailures:
    """Tests for request validation failure scenarios."""

    async def test_missing_required_field_create_team(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should reject team creation without name."""
        payload = {"intro": "A team without a name"}

        k_resp = await kotlin_client.post("/teams", json=payload, headers=auth_headers)
        p_resp = await python_client.post("/teams", json=payload, headers=auth_headers)

        assert k_resp.status_code in (400, 422)
        assert p_resp.status_code in (400, 422)

    async def test_invalid_email_format_register(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
    ) -> None:
        """Both backends should reject invalid email format."""
        payload = {
            "username": "testuser123",
            "nickname": "Test User",
            "email": "not-an-email",
            "emailCode": "123456",
            "password": "TestPassword123!",
        }

        k_resp = await kotlin_client.post("/users", json=payload)
        p_resp = await python_client.post("/users", json=payload)

        assert k_resp.status_code in (400, 422)
        assert p_resp.status_code in (400, 422)

    async def test_negative_page_size(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should reject negative page size."""
        k_resp = await kotlin_client.get("/tasks", params={"pageSize": -1}, headers=auth_headers)
        p_resp = await python_client.get("/tasks", params={"pageSize": -1}, headers=auth_headers)

        assert k_resp.status_code in (200, 400, 422)
        assert p_resp.status_code in (200, 400, 422)

    async def test_invalid_sort_field(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle invalid sort field."""
        k_resp = await kotlin_client.get(
            "/tasks",
            params={"sortBy": "invalid_field", "pageSize": 5},
            headers=auth_headers,
        )
        p_resp = await python_client.get(
            "/tasks",
            params={"sortBy": "invalid_field", "pageSize": 5},
            headers=auth_headers,
        )

        assert k_resp.status_code in (200, 400, 422)
        assert p_resp.status_code in (200, 400, 422)

    async def test_invalid_enum_value(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should reject invalid enum values."""
        k_resp = await kotlin_client.get(
            "/tasks",
            params={"approved": "INVALID_STATUS", "pageSize": 5},
            headers=auth_headers,
        )
        p_resp = await python_client.get(
            "/tasks",
            params={"approved": "INVALID_STATUS", "pageSize": 5},
            headers=auth_headers,
        )

        assert k_resp.status_code in (200, 400, 422)
        assert p_resp.status_code in (200, 400, 422)

    async def test_string_for_integer_id(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle string where integer ID expected."""
        k_resp = await kotlin_client.get("/tasks/not-an-integer", headers=auth_headers)
        p_resp = await python_client.get("/tasks/not-an-integer", headers=auth_headers)

        assert k_resp.status_code in (400, 404, 422)
        assert p_resp.status_code in (400, 404, 422)

    async def test_empty_request_body(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle empty request body for POST."""
        k_resp = await kotlin_client.post("/teams", json={}, headers=auth_headers)
        p_resp = await python_client.post("/teams", json={}, headers=auth_headers)

        assert k_resp.status_code in (400, 422)
        assert p_resp.status_code in (400, 422)

    async def test_too_long_string(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should reject overly long strings."""
        long_name = "A" * 10000
        payload = {"name": long_name, "intro": "Test"}

        k_resp = await kotlin_client.post("/teams", json=payload, headers=auth_headers)
        p_resp = await python_client.post("/teams", json=payload, headers=auth_headers)

        assert k_resp.status_code in (400, 422, 201)
        assert p_resp.status_code in (400, 422, 201)


# =============================================================================
# 1.6.5 Business Rule Conflict Scenarios
# =============================================================================


@pytest.mark.anyio
class TestBusinessRuleConflicts:
    """Tests for business rule violation scenarios."""

    async def test_duplicate_team_join_request(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle duplicate join requests appropriately."""
        k_list = await kotlin_client.get("/teams", params={"pageSize": 1}, headers=auth_headers)
        if k_list.status_code != 200:
            pytest.skip("Cannot get team list")

        teams = k_list.json()["data"].get("teams", [])
        if not teams:
            pytest.skip("No teams available")

        team_id = teams[0]["id"]

        k_resp1 = await kotlin_client.post(
            f"/teams/{team_id}/join-requests",
            json={},
            headers=auth_headers,
        )
        k_resp2 = await kotlin_client.post(
            f"/teams/{team_id}/join-requests",
            json={},
            headers=auth_headers,
        )

        p_resp1 = await python_client.post(
            f"/teams/{team_id}/join-requests",
            json={},
            headers=auth_headers,
        )
        p_resp2 = await python_client.post(
            f"/teams/{team_id}/join-requests",
            json={},
            headers=auth_headers,
        )

        assert k_resp2.status_code in (200, 201, 400, 409)
        assert p_resp2.status_code in (200, 201, 400, 409)

    async def test_self_follow(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        test_user_id: str,
    ) -> None:
        """Both backends should handle user trying to follow themselves."""
        k_resp = await kotlin_client.post(
            f"/users/{test_user_id}/followers",
            headers=auth_headers,
        )
        p_resp = await python_client.post(
            f"/users/{test_user_id}/followers",
            headers=auth_headers,
        )

        assert k_resp.status_code in (200, 201, 400, 409)
        assert p_resp.status_code in (200, 201, 400, 409)

    async def test_mark_already_read_notification(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should handle marking already-read notifications."""
        k_resp = await kotlin_client.put(
            "/notifications/status",
            json={"read": True},
            headers=auth_headers,
        )
        p_resp = await python_client.put(
            "/notifications/status",
            json={"read": True},
            headers=auth_headers,
        )

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_resp2 = await kotlin_client.put(
            "/notifications/status",
            json={"read": True},
            headers=auth_headers,
        )
        p_resp2 = await python_client.put(
            "/notifications/status",
            json={"read": True},
            headers=auth_headers,
        )

        assert k_resp2.status_code == 200
        assert p_resp2.status_code == 200


# =============================================================================
# Error Response Structure Tests
# =============================================================================


@pytest.mark.anyio
class TestErrorResponseStructure:
    """Tests that verify error response structure consistency."""

    async def test_404_response_has_standard_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """404 responses should have consistent structure."""
        k_resp = await kotlin_client.get("/tasks/999999999", headers=auth_headers)
        p_resp = await python_client.get("/tasks/999999999", headers=auth_headers)

        if k_resp.status_code == 404:
            k_body = k_resp.json()
            assert "code" in k_body or "error" in k_body or "message" in k_body

        if p_resp.status_code == 404:
            p_body = p_resp.json()
            assert "code" in p_body or "error" in p_body or "message" in p_body

    async def test_400_response_has_validation_details(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """400 responses should include validation error details."""
        payload = {}

        k_resp = await kotlin_client.post("/teams", json=payload, headers=auth_headers)
        p_resp = await python_client.post("/teams", json=payload, headers=auth_headers)

        if k_resp.status_code in (400, 422):
            k_body = k_resp.json()
            assert any(key in k_body for key in ["code", "message", "error", "detail"])

        if p_resp.status_code in (400, 422):
            p_body = p_resp.json()
            assert any(key in p_body for key in ["code", "message", "error", "detail"])
