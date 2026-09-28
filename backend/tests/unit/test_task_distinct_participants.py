"""首页那句「参与 N 人」背后的那个数。

`_count_distinct_participants` 只做一件事：把 `TaskMembership` 行数**按人**去重。
用错的两种方向都很容易被忽略，因为它们给出的都是一个看着正常的整数：

- 忘了去重 → 一个人领三道题算三人，「参与人数」比「领取次数」还像次数；
- 忘了按 `task_ids` 收口 → 数成了全板的人，和同一行上「领取次数」（那是这一页的
  和）说的不是同一件事。

所以下面两条各钉一个方向，另加一条「没题可数时不许去查库」。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _membership(task_id: int, member_id: int) -> SimpleNamespace:
    """够 `_count_distinct_participants` 读的一行 `TaskMembership`。"""
    return SimpleNamespace(task_id=task_id, member_id=member_id)


def _patched_repo(memberships: list[SimpleNamespace]):
    """把路由模块里那个仓库换成一份假名单。"""
    return patch(
        "app.api.routes.tasks.TaskMembershipRepository",
        return_value=SimpleNamespace(
            list_memberships_for_space=AsyncMock(return_value=memberships),
        ),
    )


class TestCountDistinctParticipants:
    @pytest.mark.anyio
    async def test_counts_a_person_once_across_tasks(self):
        from app.api.routes.tasks import _count_distinct_participants

        # 7 号领了两道题（一行是重复的），8 号领了一道 —— 一共两个人。
        memberships = [
            _membership(1, 7),
            _membership(2, 7),
            _membership(1, 7),
            _membership(1, 8),
        ]

        with _patched_repo(memberships):
            count = await _count_distinct_participants(
                AsyncMock(), space_id=100, task_ids=[1, 2]
            )

        assert count == 2

    @pytest.mark.anyio
    async def test_ignores_memberships_of_tasks_outside_this_page(self):
        from app.api.routes.tasks import _count_distinct_participants

        # 9 号领的是 99 号题，而这一页只有 1、2 两道 —— 他不算。
        memberships = [
            _membership(1, 7),
            _membership(2, 8),
            _membership(99, 9),
        ]

        with _patched_repo(memberships):
            count = await _count_distinct_participants(
                AsyncMock(), space_id=100, task_ids=[1, 2]
            )

        assert count == 2

    @pytest.mark.anyio
    async def test_no_tasks_means_zero_and_no_query(self):
        from app.api.routes.tasks import _count_distinct_participants

        repo = SimpleNamespace(list_memberships_for_space=AsyncMock(return_value=[]))

        with patch("app.api.routes.tasks.TaskMembershipRepository", return_value=repo):
            count = await _count_distinct_participants(
                AsyncMock(), space_id=100, task_ids=[]
            )

        assert count == 0
        repo.list_memberships_for_space.assert_not_awaited()
