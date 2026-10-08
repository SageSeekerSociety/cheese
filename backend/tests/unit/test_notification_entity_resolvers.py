"""Unit tests for app.domain.notification.entity_resolvers.

Covers TeamEntityResolver, UserEntityResolver, ProjectEntityResolver.
"""

import uuid
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
    def _make_resolver(self, teams_by_id=None, chosen_avatars=None):
        team_service = AsyncMock()
        team_service.get_teams_by_ids.return_value = teams_by_id or {}
        # Who actually picked a face. A team row alone is not enough: every team is
        # created with the global default avatar id (`CreateTeamRequest`), so an
        # avatar_id on the team says nothing about whether anyone chose it.
        team_service.chosen_avatar_ids.return_value = chosen_avatars or {}
        return TeamEntityResolver(
            team_service, avatar_base_url="https://cdn.example.com/"
        )

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
        team = SimpleNamespace(id=1, handle="alpha", name="Team Alpha", avatar_id=42)
        resolver = self._make_resolver(teams_by_id={1: team}, chosen_avatars={1: 42})

        result = await resolver.resolve(["1"])
        assert "1" in result
        dto = result["1"]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Team Alpha"
        assert dto.type == "team"
        assert dto.url == "/teams/alpha"
        assert dto.avatarUrl == "https://cdn.example.com/avatars/42"

    @pytest.mark.anyio
    async def test_resolve_no_avatar(self):
        team = SimpleNamespace(id=1, handle="alpha", name="Team Alpha", avatar_id=None)
        resolver = self._make_resolver(teams_by_id={1: team})

        result = await resolver.resolve(["1"])
        assert result["1"].avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_no_avatar_for_teams_that_never_picked_one(self):
        # Every team is created with the global default avatar id, so a row that
        # carries one still means "never picked". Sending that URL would give
        # every such team the same face.
        never_picked = SimpleNamespace(id=1, handle="alpha", name="Alpha", avatar_id=1)
        resolver = self._make_resolver(teams_by_id={1: never_picked}, chosen_avatars={})

        result = await resolver.resolve(["1"])
        assert result["1"].avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_only_the_teams_that_picked_one_get_a_url(self):
        picked = SimpleNamespace(id=1, handle="alpha", name="Alpha", avatar_id=9)
        never = SimpleNamespace(id=2, handle="beta", name="Beta", avatar_id=1)
        resolver = self._make_resolver(
            teams_by_id={1: picked, 2: never}, chosen_avatars={1: 9}
        )

        result = await resolver.resolve(["1", "2"])
        assert result["1"].avatarUrl == "https://cdn.example.com/avatars/9"
        assert result["2"].avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        resolver = self._make_resolver(teams_by_id={})

        result = await resolver.resolve(["99"])
        assert result["99"] is None

    @pytest.mark.anyio
    async def test_resolve_mixed(self):
        team = SimpleNamespace(id=1, handle="alpha", name="Alpha", avatar_id=None)
        resolver = self._make_resolver(teams_by_id={1: team})

        result = await resolver.resolve(["1", "2", "abc"])
        assert result["1"] is not None
        assert result["2"] is None


# ---------------------------------------------------------------------------
# UserEntityResolver
# ---------------------------------------------------------------------------


class TestUserEntityResolver:
    def _make_resolver(self, users_by_id=None, handles_by_id=None, chosen_avatars=None):
        user_service = AsyncMock()
        user_service.get_users_by_ids.return_value = users_by_id or {}
        user_service.get_handles_by_ids.return_value = handles_by_id or {}
        # Who actually picked a face. A profile row alone is not enough: every
        # registration path writes the global default avatar into `avatar_id`.
        user_service.chosen_avatar_ids.return_value = chosen_avatars or {}
        return UserEntityResolver(
            user_service, avatar_base_url="https://cdn.example.com/"
        )

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
        resolver = self._make_resolver(
            users_by_id={1: profile},
            handles_by_id={1: "alice"},
            chosen_avatars={1: 10},
        )

        result = await resolver.resolve(["1"])
        assert "1" in result
        dto = result["1"]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Alice"
        assert dto.handle == "alice"
        assert dto.type == "user"
        assert dto.url == "/users/1"
        assert dto.avatarUrl == "https://cdn.example.com/avatars/10"

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        resolver = self._make_resolver(users_by_id={})

        result = await resolver.resolve(["99"])
        assert result["99"] is None

    @pytest.mark.anyio
    async def test_resolve_no_avatar_for_people_who_never_picked_one(self):
        # Registration hardcodes the global default into `avatar_id`, so a row
        # that has one still means "never picked". Sending that URL would give
        # every such person the same face; a row whose id is None used to build
        # `/avatars/None`. Both must come back as "no face", not as a URL.
        never_picked = SimpleNamespace(nickname="Alice", avatar_id=1)
        no_row_at_all = SimpleNamespace(nickname="Bob", avatar_id=None)
        resolver = self._make_resolver(
            users_by_id={1: never_picked, 2: no_row_at_all},
            handles_by_id={1: "alice", 2: "bob"},
            chosen_avatars={},
        )

        result = await resolver.resolve(["1", "2"])
        assert result["1"].avatarUrl is None
        assert result["2"].avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_only_the_people_who_picked_one_get_a_url(self):
        picked = SimpleNamespace(nickname="Alice", avatar_id=10)
        never = SimpleNamespace(nickname="Bob", avatar_id=1)
        resolver = self._make_resolver(
            users_by_id={1: picked, 2: never},
            handles_by_id={1: "alice", 2: "bob"},
            chosen_avatars={1: 10},
        )

        result = await resolver.resolve(["1", "2"])
        assert result["1"].avatarUrl == "https://cdn.example.com/avatars/10"
        assert result["2"].avatarUrl is None


# ---------------------------------------------------------------------------
# ProjectEntityResolver
# ---------------------------------------------------------------------------


class TestProjectEntityResolver:
    # cheesex projects are UUID-keyed, so the resolver parses each id as a UUID
    # and looks the whole batch up in one ``get_projects_by_ids`` call. The tests
    # mock that method to exercise the found/not-found paths.
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
        # non-UUID ids can't name a project → skipped entirely
        resolver = self._make_resolver()
        result = await resolver.resolve(["abc"])
        assert result == {}

    @pytest.mark.anyio
    async def test_resolve_found(self):
        pid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        project = SimpleNamespace(id=pid, name="Project X")
        resolver = self._make_resolver(projects_by_id={pid: project})

        result = await resolver.resolve([str(pid)])
        assert str(pid) in result
        dto = result[str(pid)]
        assert isinstance(dto, ResolvedEntityInfoDTO)
        assert dto.name == "Project X"
        assert dto.type == "project"
        assert dto.url == f"/projects/{pid}"
        assert dto.avatarUrl is None

    @pytest.mark.anyio
    async def test_resolve_not_found(self):
        pid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        resolver = self._make_resolver(projects_by_id={})

        result = await resolver.resolve([str(pid)])
        assert result[str(pid)] is None

    @pytest.mark.anyio
    async def test_resolve_asks_for_the_whole_batch_at_once(self):
        # The point of the batch lookup: N ids, ONE query. A per-id loop would
        # call the service N times and put an N-query page back on the wire.
        first = uuid.UUID("33333333-3333-3333-3333-333333333333")
        second = uuid.UUID("44444444-4444-4444-4444-444444444444")
        service = AsyncMock()
        service.get_projects_by_ids.return_value = {
            first: SimpleNamespace(id=first, name="A"),
            second: SimpleNamespace(id=second, name="B"),
        }
        resolver = ProjectEntityResolver(service)

        result = await resolver.resolve([str(first), str(second)])
        assert result[str(first)].name == "A"
        assert result[str(second)].name == "B"
        assert service.get_projects_by_ids.await_count == 1
        assert set(service.get_projects_by_ids.await_args.args[0]) == {first, second}
