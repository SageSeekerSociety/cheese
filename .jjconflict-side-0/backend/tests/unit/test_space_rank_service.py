from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.space.rank_service import SpaceRankService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _space(enable_rank: bool = True):
    return SimpleNamespace(enable_rank=enable_rank)


def _make_service(
    space_repo=None, rank_repo=None
) -> tuple[SpaceRankService, AsyncMock, AsyncMock]:
    space_repo = space_repo or AsyncMock()
    rank_repo = rank_repo or AsyncMock()
    svc = SpaceRankService(space_repo=space_repo, rank_repo=rank_repo)
    return svc, space_repo, rank_repo


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestSpaceRankServiceInit:
    def test_stores_repos(self):
        svc, space_repo, rank_repo = _make_service()
        assert svc._space_repo is space_repo
        assert svc._rank_repo is rank_repo


# ---------------------------------------------------------------------------
# award_rank
# ---------------------------------------------------------------------------


class TestAwardRank:
    @pytest.mark.anyio
    async def test_successful_award(self):
        """Lines 24-28: space exists with enable_rank=True, increment succeeds."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=True)

        result = await svc.award_rank(space_id=1, user_id=42, delta=3)

        assert result is True
        space_repo.get_by_id.assert_awaited_once_with(1)
        rank_repo.increment_rank.assert_awaited_once_with(
            space_id=1, user_id=42, delta=3
        )

    @pytest.mark.anyio
    async def test_default_delta_is_one(self):
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=True)

        result = await svc.award_rank(space_id=1, user_id=42)

        assert result is True
        rank_repo.increment_rank.assert_awaited_once_with(
            space_id=1, user_id=42, delta=1
        )

    @pytest.mark.anyio
    async def test_space_id_none_returns_false(self):
        """Line 16-23: early return when space_id is None."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank(space_id=None, user_id=42)

        assert result is False
        space_repo.get_by_id.assert_not_awaited()

    @pytest.mark.anyio
    async def test_user_id_none_returns_false(self):
        """Line 16-23: early return when user_id is None."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank(space_id=1, user_id=None)

        assert result is False

    @pytest.mark.anyio
    async def test_space_repo_none_returns_false(self):
        """Line 16-23: early return when space_repo is None."""
        svc = SpaceRankService(space_repo=None, rank_repo=AsyncMock())

        result = await svc.award_rank(space_id=1, user_id=42)

        assert result is False

    @pytest.mark.anyio
    async def test_rank_repo_none_returns_false(self):
        """Line 16-23: early return when rank_repo is None."""
        svc = SpaceRankService(space_repo=AsyncMock(), rank_repo=None)

        result = await svc.award_rank(space_id=1, user_id=42)

        assert result is False

    @pytest.mark.anyio
    async def test_delta_zero_returns_false(self):
        """Line 16-23: delta <= 0 returns False."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank(space_id=1, user_id=42, delta=0)

        assert result is False

    @pytest.mark.anyio
    async def test_delta_negative_returns_false(self):
        """Line 16-23: delta <= 0 returns False."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank(space_id=1, user_id=42, delta=-1)

        assert result is False

    @pytest.mark.anyio
    async def test_space_not_found_returns_false(self):
        """Line 25: space is None."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = None

        result = await svc.award_rank(space_id=1, user_id=42)

        assert result is False
        rank_repo.increment_rank.assert_not_awaited()

    @pytest.mark.anyio
    async def test_space_rank_disabled_returns_false(self):
        """Line 25: space.enable_rank is False."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=False)

        result = await svc.award_rank(space_id=1, user_id=42)

        assert result is False
        rank_repo.increment_rank.assert_not_awaited()


# ---------------------------------------------------------------------------
# award_rank_if_higher
# ---------------------------------------------------------------------------


class TestAwardRankIfHigher:
    @pytest.mark.anyio
    async def test_sets_rank_when_higher(self):
        """Lines 45-52: task_rank > current_rank, set_rank called."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=True)
        rank_repo.get_rank.return_value = 3

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is True
        rank_repo.get_rank.assert_awaited_once_with(space_id=1, user_id=42)
        rank_repo.set_rank.assert_awaited_once_with(space_id=1, user_id=42, rank=5)

    @pytest.mark.anyio
    async def test_does_not_set_when_current_is_equal(self):
        """Line 49-50: current_rank >= task_rank returns False."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=True)
        rank_repo.get_rank.return_value = 5

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False
        rank_repo.set_rank.assert_not_awaited()

    @pytest.mark.anyio
    async def test_does_not_set_when_current_is_higher(self):
        """Line 49-50: current_rank >= task_rank returns False."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=True)
        rank_repo.get_rank.return_value = 10

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False
        rank_repo.set_rank.assert_not_awaited()

    @pytest.mark.anyio
    async def test_space_id_none_returns_false(self):
        """Line 37-44: early return when space_id is None."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank_if_higher(space_id=None, user_id=42, task_rank=5)

        assert result is False

    @pytest.mark.anyio
    async def test_user_id_none_returns_false(self):
        """Line 37-44: early return when user_id is None."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank_if_higher(space_id=1, user_id=None, task_rank=5)

        assert result is False

    @pytest.mark.anyio
    async def test_task_rank_none_returns_false(self):
        """Line 37-44: early return when task_rank is None."""
        svc, space_repo, rank_repo = _make_service()

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=None)

        assert result is False

    @pytest.mark.anyio
    async def test_space_repo_none_returns_false(self):
        """Line 37-44: early return when space_repo is None."""
        svc = SpaceRankService(space_repo=None, rank_repo=AsyncMock())

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False

    @pytest.mark.anyio
    async def test_rank_repo_none_returns_false(self):
        """Line 37-44: early return when rank_repo is None."""
        svc = SpaceRankService(space_repo=AsyncMock(), rank_repo=None)

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False

    @pytest.mark.anyio
    async def test_space_not_found_returns_false(self):
        """Line 46: space is None."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = None

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False
        rank_repo.get_rank.assert_not_awaited()

    @pytest.mark.anyio
    async def test_space_rank_disabled_returns_false(self):
        """Line 46: space.enable_rank is False."""
        svc, space_repo, rank_repo = _make_service()
        space_repo.get_by_id.return_value = _space(enable_rank=False)

        result = await svc.award_rank_if_higher(space_id=1, user_id=42, task_rank=5)

        assert result is False
        rank_repo.get_rank.assert_not_awaited()
