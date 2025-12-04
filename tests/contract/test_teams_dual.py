"""
Dual-endpoint contract tests for Team API.

These tests call both Kotlin and Python backends with the same requests
and compare the responses to ensure behavioral parity.

Run with:
    pytest tests/contract/test_teams_dual.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import DualEndpointTester, ResponseComparator


@pytest.mark.anyio
class TestTeamsDualEndpoint:
    """Dual-endpoint comparison tests for Team APIs."""

    async def test_list_teams_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """Both backends should return the same structure for team list."""
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/teams",
            params={"pageSize": 5},
            compare_structure=True,
        )

        assert kotlin_resp.status_code == 200, f"Kotlin returned {kotlin_resp.status_code}"
        assert python_resp.status_code == 200, f"Python returned {python_resp.status_code}"

        k_data = kotlin_resp.json()["data"]
        p_data = python_resp.json()["data"]

        assert "teams" in k_data
        assert "teams" in p_data

        if diffs:
            relevant_diffs = [d for d in diffs if "missing" in d.lower() or "extra" in d.lower()]
            if relevant_diffs:
                pytest.fail(f"Structure differences:\n" + "\n".join(relevant_diffs))

    async def test_my_teams_structure_match(
        self,
        dual_tester: DualEndpointTester,
    ) -> None:
        """Both backends should return the same structure for my-teams."""
        kotlin_resp, python_resp, diffs = await dual_tester.get(
            "/teams/my-teams",
            params={"pageSize": 10},
            compare_structure=True,
        )

        if kotlin_resp.status_code == 200 and python_resp.status_code == 200:
            k_data = kotlin_resp.json()["data"]
            p_data = python_resp.json()["data"]

            assert "teams" in k_data or "myTeams" in k_data
            assert "teams" in p_data or "myTeams" in p_data

    async def test_get_team_by_id_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Both backends should return the same team detail structure."""
        k_list = await kotlin_client.get("/teams", params={"pageSize": 1}, headers=auth_headers)
        if k_list.status_code != 200:
            pytest.skip("Cannot get team list from Kotlin")

        teams = k_list.json()["data"].get("teams", [])
        if not teams:
            pytest.skip("No teams available for testing")

        team_id = teams[0]["id"]

        k_resp = await kotlin_client.get(f"/teams/{team_id}", headers=auth_headers)
        p_resp = await python_client.get(f"/teams/{team_id}", headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin returned {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python returned {p_resp.status_code}"

        diffs = response_comparator.compare_structure(
            k_resp.json()["data"],
            p_resp.json()["data"],
        )

        if diffs:
            structural_diffs = [d for d in diffs if "missing" in d.lower()]
            if structural_diffs:
                pytest.fail(f"Team detail structure differs:\n" + "\n".join(structural_diffs))


@pytest.mark.anyio
class TestTeamsKotlinBaseline:
    """Baseline tests against Kotlin backend for Team APIs."""

    async def test_list_teams_returns_array(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: team list should return an array."""
        resp = await kotlin_client.get("/teams", params={"pageSize": 10}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "teams" in data
        assert isinstance(data["teams"], list)

    async def test_team_has_required_fields(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: each team should have required fields."""
        resp = await kotlin_client.get("/teams", params={"pageSize": 5}, headers=auth_headers)
        assert resp.status_code == 200

        teams = resp.json()["data"]["teams"]
        if teams:
            required_fields = {"id", "name"}
            for team in teams:
                missing = required_fields - set(team.keys())
                assert not missing, f"Team missing fields: {missing}"

    async def test_team_members_endpoint(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: team members endpoint should work."""
        list_resp = await kotlin_client.get("/teams", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get team list")

        teams = list_resp.json()["data"].get("teams", [])
        if not teams:
            pytest.skip("No teams available")

        team_id = teams[0]["id"]
        resp = await kotlin_client.get(f"/teams/{team_id}/members", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        assert "members" in data
        assert isinstance(data["members"], list)

    async def test_invalid_team_id_returns_404(
        self,
        kotlin_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Kotlin: invalid team ID should return 404."""
        resp = await kotlin_client.get("/teams/999999999", headers=auth_headers)
        assert resp.status_code in (404, 400)


@pytest.mark.anyio
class TestTeamsPythonParity:
    """Parity tests: verify Python matches Kotlin behavior for Teams."""

    async def test_list_teams_same_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python team list should have same structure as Kotlin."""
        k_resp = await kotlin_client.get("/teams", params={"pageSize": 5}, headers=auth_headers)
        p_resp = await python_client.get("/teams", params={"pageSize": 5}, headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        diffs = response_comparator.compare_structure(
            k_resp.json(),
            p_resp.json(),
        )

        structural = [d for d in diffs if "missing in Python" in d]
        if structural:
            pytest.fail(f"Python missing structure:\n" + "\n".join(structural))

    async def test_team_members_same_structure(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        response_comparator: ResponseComparator,
    ) -> None:
        """Python team members should have same structure as Kotlin."""
        list_resp = await kotlin_client.get("/teams", params={"pageSize": 1}, headers=auth_headers)
        if list_resp.status_code != 200:
            pytest.skip("Cannot get team list")

        teams = list_resp.json()["data"].get("teams", [])
        if not teams:
            pytest.skip("No teams available")

        team_id = teams[0]["id"]

        k_resp = await kotlin_client.get(f"/teams/{team_id}/members", headers=auth_headers)
        p_resp = await python_client.get(f"/teams/{team_id}/members", headers=auth_headers)

        assert k_resp.status_code == 200
        assert p_resp.status_code == 200

        diffs = response_comparator.compare_structure(
            k_resp.json()["data"],
            p_resp.json()["data"],
        )

        if diffs:
            pytest.fail(f"Members structure differs:\n" + "\n".join(diffs))

    async def test_invalid_team_id_same_error(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Python should return similar error for invalid team ID."""
        k_resp = await kotlin_client.get("/teams/999999999", headers=auth_headers)
        p_resp = await python_client.get("/teams/999999999", headers=auth_headers)

        assert k_resp.status_code in (400, 404)
        assert p_resp.status_code in (400, 404)

    async def test_keyword_search_works(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Both backends should support keyword search."""
        params = {"keywords": "test", "pageSize": 5}

        k_resp = await kotlin_client.get("/teams", params=params, headers=auth_headers)
        p_resp = await python_client.get("/teams", params=params, headers=auth_headers)

        assert k_resp.status_code == 200, f"Kotlin search failed: {k_resp.status_code}"
        assert p_resp.status_code == 200, f"Python search failed: {p_resp.status_code}"
