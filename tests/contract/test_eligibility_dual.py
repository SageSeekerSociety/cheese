"""
Dual-endpoint contract tests for Task Participation Eligibility API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity for eligibility checks.

Run with:
    pytest tests/contract/test_eligibility_dual.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import ResponseComparator


@pytest.mark.anyio
class TestEligibilityKotlinBaseline:
    """Baseline tests against Kotlin backend for Eligibility APIs."""

    async def test_eligibility_endpoint_exists(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: eligibility endpoint should exist for a valid task."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]
        resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        assert resp.status_code == 200

    async def test_eligibility_has_user_or_teams(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: eligibility response should have user or teams field."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]
        resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        has_user = "user" in data and data["user"] is not None
        has_teams = "teams" in data and data["teams"] is not None
        assert has_user or has_teams, "Eligibility should have either user or teams"

    async def test_user_eligibility_has_required_fields(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: user eligibility should have eligible and reasons fields."""
        list_resp = await kotlin_client.get(
            "/tasks",
            params={"pageSize": 10},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        user_tasks = [t for t in tasks if t.get("submitterType") == "USER"]
        if not user_tasks:
            pytest.skip("No USER type tasks available")

        task_id = user_tasks[0]["id"]
        resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        if data.get("user") is not None:
            user = data["user"]
            assert "eligible" in user, "Missing 'eligible' field"
            assert "reasons" in user, "Missing 'reasons' field"
            assert isinstance(user["eligible"], bool)
            assert isinstance(user["reasons"], list)

    async def test_team_eligibility_structure(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: team eligibility should be an array with team and eligibility."""
        list_resp = await kotlin_client.get(
            "/tasks",
            params={"pageSize": 10},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        team_tasks = [t for t in tasks if t.get("submitterType") == "TEAM"]
        if not team_tasks:
            pytest.skip("No TEAM type tasks available")

        task_id = team_tasks[0]["id"]
        resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        if data.get("teams") is not None:
            teams = data["teams"]
            assert isinstance(teams, list), "teams should be an array"
            for team_entry in teams:
                assert "team" in team_entry, "Missing 'team' field"
                assert "eligibility" in team_entry, "Missing 'eligibility' field"
                eligibility = team_entry["eligibility"]
                assert "eligible" in eligibility
                assert "reasons" in eligibility

    async def test_invalid_task_returns_error(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: invalid task ID should return 404."""
        resp = await kotlin_client.get("/tasks/999999999/eligibility", headers=auth_headers)
        assert resp.status_code in (400, 404)


@pytest.mark.anyio
class TestEligibilityPythonParity:
    """Parity tests: verify Python matches Kotlin behavior for Eligibility."""

    async def test_eligibility_same_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python eligibility should have same structure as Kotlin."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        task_id = tasks[0]["id"]

        k_resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        p_resp = await python_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        diffs = response_comparator.compare_structure(
            k_resp.json()["data"],
            p_resp.json()["data"],
        )

        structural = [d for d in diffs if "missing in Python" in d]
        if structural:
            pytest.fail(f"Python missing structure:\n" + "\n".join(structural))

    async def test_user_eligibility_parity(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python USER eligibility should match Kotlin structure."""
        list_resp = await kotlin_client.get(
            "/tasks",
            params={"pageSize": 10},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        user_tasks = [t for t in tasks if t.get("submitterType") == "USER"]
        if not user_tasks:
            pytest.skip("No USER type tasks available")

        task_id = user_tasks[0]["id"]

        k_resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        p_resp = await python_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        if k_data.get("user") is not None:
            assert p_data.get("user") is not None, "Python missing user field"
            diffs = response_comparator.compare_structure(k_data["user"], p_data["user"])
            if diffs:
                pytest.fail(f"User eligibility differs:\n" + "\n".join(diffs))

    async def test_team_eligibility_parity(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python TEAM eligibility should match Kotlin structure."""
        list_resp = await kotlin_client.get(
            "/tasks",
            params={"pageSize": 10},
            headers=auth_headers,
        )
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        team_tasks = [t for t in tasks if t.get("submitterType") == "TEAM"]
        if not team_tasks:
            pytest.skip("No TEAM type tasks available")

        task_id = team_tasks[0]["id"]

        k_resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
        p_resp = await python_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        k_data = k_resp.json()["data"]
        p_data = p_resp.json()["data"]

        if k_data.get("teams") is not None:
            assert p_data.get("teams") is not None, "Python missing teams field"
            if len(k_data["teams"]) > 0 and len(p_data["teams"]) > 0:
                diffs = response_comparator.compare_structure(
                    k_data["teams"][0],
                    p_data["teams"][0],
                )
                if diffs:
                    pytest.fail(f"Team eligibility structure differs:\n" + "\n".join(diffs))

    async def test_invalid_task_same_error(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return similar error for invalid task ID."""
        k_resp = await kotlin_client.get("/tasks/999999999/eligibility", headers=auth_headers)
        p_resp = await python_client.get("/tasks/999999999/eligibility", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_eligibility_reason_codes_match(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should use the same reason codes as Kotlin."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 5}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        valid_codes = {
            "TASK_NOT_APPROVED",
            "REGISTRATION_NOT_STARTED",
            "REGISTRATION_CLOSED",
            "PARTICIPANT_LIMIT_REACHED",
            "ALREADY_PARTICIPATING",
            "MISSING_REAL_NAME",
            "USER_RANK_NOT_HIGH_ENOUGH",
            "TEAM_TOO_SMALL",
            "TEAM_TOO_LARGE",
            "TEAM_MEMBER_MISSING_REAL_NAME",
            "TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH",
        }

        for task in tasks:
            task_id = task["id"]
            p_resp = await python_client.get(
                f"/tasks/{task_id}/eligibility",
                headers=auth_headers,
            )
            if p_resp.status_code != 200:
                continue

            data = p_resp.json()["data"]

            if data.get("user") and data["user"].get("reasons"):
                for reason in data["user"]["reasons"]:
                    code = reason.get("code")
                    assert code in valid_codes, f"Unknown reason code: {code}"

            if data.get("teams"):
                for team_entry in data["teams"]:
                    if team_entry.get("eligibility", {}).get("reasons"):
                        for reason in team_entry["eligibility"]["reasons"]:
                            code = reason.get("code")
                            assert code in valid_codes, f"Unknown reason code: {code}"


@pytest.mark.anyio
class TestEligibilityDualEndpoint:
    """Dual-endpoint comparison tests for Eligibility API."""

    async def test_multiple_tasks_eligibility_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return consistent eligibility structure for multiple tasks."""
        list_resp = await kotlin_client.get("/tasks", params={"pageSize": 5}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get task list")

        tasks = list_resp.json()["data"].get("tasks", [])
        if not tasks:
            pytest.skip("No tasks available")

        for task in tasks:
            task_id = task["id"]

            k_resp = await kotlin_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)
            p_resp = await python_client.get(f"/tasks/{task_id}/eligibility", headers=auth_headers)

            if k_resp.status_code != 200 or p_resp.status_code != 200:
                continue

            k_data = k_resp.json()["data"]
            p_data = p_resp.json()["data"]

            k_has_user = k_data.get("user") is not None
            p_has_user = p_data.get("user") is not None
            k_has_teams = k_data.get("teams") is not None
            p_has_teams = p_data.get("teams") is not None

            assert k_has_user == p_has_user, (
                f"Task {task_id}: user field mismatch "
                f"(Kotlin: {k_has_user}, Python: {p_has_user})"
            )
            assert k_has_teams == p_has_teams, (
                f"Task {task_id}: teams field mismatch "
                f"(Kotlin: {k_has_teams}, Python: {p_has_teams})"
            )
