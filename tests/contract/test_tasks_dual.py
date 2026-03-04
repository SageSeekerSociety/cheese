"""
Dual-endpoint contract tests for Task API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Run with:
    pytest tests/contract/test_tasks_dual.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import DualEndpointTester, ResponseComparator


@pytest.mark.anyio
class TestTasksDualEndpoint:
    """Dual-endpoint comparison tests for Task APIs."""

    async def test_list_tasks_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """Both backends should return the same structure for task list."""
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/tasks",
            params={"pageSize": 5},
            compare_structure=True,
        )

        assert kotlin_resp.status_code == 200, f"Kotlin returned {kotlin_resp.status_code}"
        assert python_resp.status_code == 200, f"Python returned {python_resp.status_code}"

        k_data = kotlin_resp.json()["data"]
        p_data = python_resp.json()["data"]

        assert "tasks" in k_data
        assert "tasks" in p_data

        if diffs:
            relevant_diffs = [d for d in diffs if "missing" in d.lower() or "extra" in d.lower()]
            if relevant_diffs:
                pytest.fail("Structure differences:\n" + "\n".join(relevant_diffs))

    async def test_get_task_by_id_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same task detail structure."""
        k_list = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if k_list.status_code != 200:
            pytest.skip("Cannot get task list from Kotlin")

        tasks = k_list.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available for testing")

        task_id = tasks[0]["id"]

        k_resp = await kotlin_client.get(f"/tasks/{task_id}", headers=auth_headers)
        p_resp = await python_client.get(f"/tasks/{task_id}", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        diffs = response_comparator.compare_structure(
            k_resp.json()["data"],
            p_resp.json()["data"],
        )

        if diffs:
            structural_diffs = [d for d in diffs if "missing" in d.lower()]
            if structural_diffs:
                pytest.fail("Task detail structure differs:\n" + "\n".join(structural_diffs))


@pytest.mark.anyio
class TestTasksKotlinBaseline:
    """Baseline tests against Kotlin backend for Task APIs."""

    async def test_list_tasks_returns_array(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: task list should return an array."""
        resp = await kotlin_client.get("/tasks", params={"pageSize": 10}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "tasks" in data
        assert isinstance(data["tasks"], list)

    async def test_task_has_required_fields(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: each task should have required fields."""
        resp = await kotlin_client.get("/tasks", params={"pageSize": 5}, headers=auth_headers)
        assert resp.status_code == 200

        tasks = resp.json()["data"]["tasks"]
        if tasks:
            required_fields = {"id", "name"}
            for task in tasks:
                missing = required_fields - set(task.keys())
                assert not missing, f"Task missing fields: {missing}"

    async def test_task_participants_endpoint(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: task participants endpoint should work."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]
        resp = await kotlin_client.get(f"/tasks/{task_id}/participants", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        assert "participants" in data
        assert isinstance(data["participants"], list)

    async def test_space_filter_works(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: space filter should work."""
        resp = await kotlin_client.get(
            "/tasks",
            params={"space": 1, "pageSize": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_approved_filter_works(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: approved filter should work."""
        resp = await kotlin_client.get(
            "/tasks",
            params={"approved": "APPROVED", "pageSize": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        tasks = resp.json()["data"]["tasks"]
        for task in tasks:
            assert task.get("approved") == "APPROVED"

    async def test_invalid_task_id_returns_404(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: invalid task ID should return 404."""
        resp = await kotlin_client.get("/tasks/999999999", headers=auth_headers)
        assert resp.status_code in (404, 400)


@pytest.mark.anyio
class TestTasksPythonParity:
    """Parity tests: verify Python matches Kotlin behavior for Tasks."""

    async def test_list_tasks_same_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python task list should have same structure as Kotlin."""
        k_resp = await kotlin_client.get("/tasks", params={"pageSize": 5}, headers=auth_headers)
        p_resp = await python_client.get("/tasks", params={"pageSize": 5}, headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        diffs = response_comparator.compare_structure(
            k_resp.json(),
            p_resp.json(),
        )

        structural = [d for d in diffs if "missing in Python" in d]
        if structural:
            pytest.fail("Python missing structure:\n" + "\n".join(structural))

    async def test_participants_same_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python task participants should have same structure as Kotlin."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]

        k_resp = await kotlin_client.get(f"/tasks/{task_id}/participants", headers=auth_headers)
        p_resp = await python_client.get(f"/tasks/{task_id}/participants", headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        diffs = response_comparator.compare_structure(
            k_resp.json()["data"],
            p_resp.json()["data"],
        )

        if diffs:
            pytest.fail("Participants structure differs:\n" + "\n".join(diffs))

    async def test_space_filter_works(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should support space filter like Kotlin."""
        params = {"space": 1, "pageSize": 5}

        k_resp = await kotlin_client.get("/tasks", params=params, headers=auth_headers)
        p_resp = await python_client.get("/tasks", params=params, headers=auth_headers)

        assert k_resp.status_code == p_resp.status_code

    async def test_approved_filter_works(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should support approved filter like Kotlin."""
        params = {"approved": "APPROVED", "pageSize": 5}

        k_resp = await kotlin_client.get("/tasks", params=params, headers=auth_headers)
        p_resp = await python_client.get("/tasks", params=params, headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        p_tasks = p_resp.json()["data"]["tasks"]
        for task in p_tasks:
            assert task.get("approved") == "APPROVED", (
                f"Python returned non-APPROVED task: {task.get('id')}"
            )

    async def test_keyword_search_works(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should support keyword search."""
        params = {"keywords": "test", "pageSize": 5}

        k_resp = await kotlin_client.get("/tasks", params=params, headers=auth_headers)
        p_resp = await python_client.get("/tasks", params=params, headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin search failed: {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python search failed: {p_resp.status_code}"

    async def test_invalid_task_id_same_error(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return similar error for invalid task ID."""
        k_resp = await kotlin_client.get("/tasks/999999999", headers=auth_headers)
        p_resp = await python_client.get("/tasks/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_submissions_endpoint_exists(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should have submissions endpoint."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]

        k_parts = await kotlin_client.get(f"/tasks/{task_id}/participants", headers=auth_headers)
        if k_parts.status_code != 200:
            pytest.skip("Cannot get participants")

        participants = k_parts.json()["data"].get("participants", [])
        if not participants:
            pytest.skip("No participants available")

        part_id = participants[0]["id"]

        k_resp = await kotlin_client.get(
            f"/tasks/{task_id}/participants/{part_id}/submissions",
            headers=auth_headers,
        )
        p_resp = await python_client.get(
            f"/tasks/{task_id}/participants/{part_id}/submissions",
            headers=auth_headers,
        )

        assert k_resp.status_code in (200, 403, 404)
        assert p_resp.status_code in (200, 403, 404)
