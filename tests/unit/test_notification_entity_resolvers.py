"""Unit tests for app.domain.notification.entity_resolvers.

Covers TeamEntityResolver, UserEntityResolver, ProjectEntityResolver.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.notification.dto import ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import (
    ProjectEntityResolver,
    TeamEntityResolver,
    UserEntityResolver,
)

# ---------------------------------------------------------------------------
# TeamEntityResolver
# ---------------------------------------------------------------------------


class TestTeamEntityResolver:
    def _make_resolver(self, teams_by_id=None):
        team_service = AsyncMock()
        team_service.get_teams_by_ids.return_value = teams_by_id or {}
        return TeamEntityResolver(team_service, avatar_base_url="https://cdn.example.com/")

    def test_supported_entity_type(self):
        resolver = self._make_resolver()
        assert resolver.supported_entity_type() == "team"

    @pytest.mark.anyio
    async def test_resolve_empty_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve([])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_invalid_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve(["abc", "xyz"])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_found(self):
        team = SimpleNamespace(id=1, name="Team Alpha", avatar_id=42)
        resolver = self._make_resolver(teams_by_id={1: team})

        result = await resolver.resolve(["1"])
        assert "1" in result
        dto = result["1"]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Team Alpha"
        assert dto.type == "team"
        assert dto.url == "/teams/1"
        assert dto.avatarUrl == "https://cdn.example.com/avatars/42"

    @pytest.mark.anyio
    async def test_resolve_no_avatar(self):
        team = SimpleNamespace(id=1, name="Team Alpha", avatar_id=None)
        resolver = self._make_resolver(teams_by_id={1: team})

        result = await resolver.resolve(["1"])
        assert result["1"].avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        resolver = self._make_resolver(teams_by_id={})

        result = await resolver.resolve(["99"])
        assert result["99"] is None

    @pytest.mark.anyio
    async def test_resolve_mixed(self):
        team = SimpleNamespace(id=1, name="Alpha", avatar_id=None)
        resolver = self._make_resolver(teams_by_id={1: team})

        result = await resolver.resolve(["1", "2", "abc"])
        assert result["1"] is not None
        assert result["2"] is None


# ---------------------------------------------------------------------------
# UserEntityResolver
# ---------------------------------------------------------------------------


class TestUserEntityResolver:
    def _make_resolver(self, users_by_id=None):
        user_service = AsyncMock()
        user_service.get_users_by_ids.return_value = users_by_id or {}
        return UserEntityResolver(user_service, avatar_base_url="https://cdn.example.com/")

    def test_supported_entity_type(self):
        resolver = self._make_resolver()
        assert resolver.supported_entity_type() == "user"

    @pytest.mark.anyio
    async def test_resolve_empty_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve([])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_invalid_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve(["abc"])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_found(self):
        profile = SimpleNamespace(nickname="Alice", avatar_id=10)
        resolver = self._make_resolver(users_by_id={1: profile})

        result = await resolver.resolve(["1"])
        assert "1" in result
        dto = result["1"]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Alice"
        assert dto.type == "user"
        assert dto.url == "/users/1"
        assert dto.avatarUrl == "https://cdn.example.com/avatars/10"

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        resolver = self._make_resolver(users_by_id={})

        result = await resolver.resolve(["99"])
        assert result["99"] is None


# ---------------------------------------------------------------------------
# ProjectEntityResolver
# ---------------------------------------------------------------------------


class TestProjectEntityResolver:
    def _make_resolver(self, projects_by_id=None):
        project_service = AsyncMock()
        project_service.get_projects_by_ids.return_value = projects_by_id or {}
        return ProjectEntityResolver(project_service)

    def test_supported_entity_type(self):
        resolver = self._make_resolver()
        assert resolver.supported_entity_type() == "project"

    @pytest.mark.anyio
    async def test_resolve_empty_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve([])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_invalid_ids(self):
        resolver = self._make_resolver()
        result = await resolver.resolve(["abc"])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_found(self):
        project = SimpleNamespace(id=1, name="Project X")
        resolver = self._make_resolver(projects_by_id={1: project})

        result = await resolver.resolve(["1"])
        assert "1" in result
        dto = result["1"]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Project X"
        assert dto.type == "project"
        assert dto.url == "/projects/1"
        assert dto.avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        resolver = self._make_resolver(projects_by_id={})

        result = await resolver.resolve(["99"])
        assert result["99"] is None
