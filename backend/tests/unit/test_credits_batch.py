"""`project_credits_batch` 的纯单测：批量聚合与逐项目 `summary` 同一口径。

这一层不碰数据库（`tests/unit` 的纪律），两个仓储缝用替身钉住
（`ProjectService.teams_for_projects` 与 `ComputeGrantRepository.list_for_scope`
都是 AsyncMock）。钉两件事：

1. **口径逐字段相等**。eligible = 项目 earmark ∪ 本队池（team_id 匹配且
   project_id 为 NULL）；一个都没有 = unlimited。期望值在测试里按这同一句
   定义从夹具数据现算 —— 与逐项目的 `summary()`（SQL 版同一句话）对齐的
   那份语义，批量版在 Python 里一个字都不许走样。
2. **编排形状**。一批项目只问仓储两次（归属一次、grant 一次），逐项目的
   `team_for_project` / `list_for_project` 一次都不许被叫到 —— 「2N+1 降到
   3」在缝的这一侧就是这句话。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.project.services import ProjectService
from app.domain.usage.repositories import ComputeGrantRepository
from app.domain.usage.services import UsageService


def _project(*, team_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), team_id=team_id, owner_handle="h")


def _grant(*, project_id=None, team_id=None, total=100, used=10) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        team_id=team_id,
        credits_total=total,
        credits_used=used,
    )


def _expected(project, teams, grants) -> dict:
    """按口径定义现算的期望值：earmark ∪ 本队池；无 grant = unlimited。"""
    eligible = [
        g
        for g in grants
        if g.project_id == project.id
        or (
            g.project_id is None
            and g.team_id is not None
            and teams.get(project.id) == g.team_id
        )
    ]
    total = sum(g.credits_total for g in eligible)
    used = sum(g.credits_used for g in eligible)
    return {
        "unlimited": not eligible,
        "credits_total": total,
        "credits_used": used,
        "credits_remaining": total - used,
    }


@pytest.fixture
def seams(monkeypatch: pytest.MonkeyPatch):
    """两个仓储缝的替身 + 两把「被叫到就炸」的逐项目旧路。"""
    teams = AsyncMock()
    grants = AsyncMock()
    monkeypatch.setattr(ProjectService, "teams_for_projects", teams)
    monkeypatch.setattr(ComputeGrantRepository, "list_for_scope", grants)

    async def _no_per_project(*_a, **_k):
        raise AssertionError("批量路径不许叫逐项目的旧查询")

    monkeypatch.setattr(ProjectService, "team_for_project", _no_per_project)
    monkeypatch.setattr(ComputeGrantRepository, "list_for_project", _no_per_project)
    return SimpleNamespace(teams=teams, grants=grants)


async def test_batch_matches_the_per_project_definition(seams):
    """多项目 × 多 grant（earmark、队池、两者都有、无 grant=unlimited、别队
    的池、别项目的 earmark）逐字段对齐。"""
    p_both = _project()
    p_pool_only = _project()
    p_earmark_only = _project()
    p_none = _project()
    p_orphan = _project()  # 归属解析不到小队：只有 earmark eligible
    projects = [p_both, p_pool_only, p_earmark_only, p_none, p_orphan]

    teams = {
        p_both.id: 1,
        p_pool_only.id: 1,
        p_earmark_only.id: 2,
        p_none.id: None,
        p_orphan.id: None,
    }
    grants = [
        _grant(project_id=p_both.id, team_id=1, total=50, used=5),  # p_both 的 earmark
        _grant(team_id=1, total=200, used=20),  # 1 队池 → p_both + p_pool_only
        _grant(team_id=2, total=300, used=0),  # 2 队池 → p_earmark_only 也 eligible
        _grant(project_id=p_earmark_only.id, team_id=2, total=70, used=7),
        _grant(team_id=99, total=999, used=99),  # 别队的池：谁也不 eligible
        _grant(project_id=uuid.uuid4(), team_id=1, total=888, used=88),  # 别项目
    ]
    seams.teams.return_value = teams
    seams.grants.return_value = grants

    out = await UsageService(None).project_credits_batch(projects)  # type: ignore[arg-type]

    for project in projects:
        assert out[project.id] == _expected(project, teams, grants), project.id
    # 抽查两句人话：unlimited 只在「一个 eligible 都没有」时；队池不重复计给别队。
    assert out[p_none.id]["unlimited"] is True
    assert out[p_pool_only.id]["credits_total"] == 200


async def test_batch_asks_each_repository_exactly_once(seams):
    """「2N+1 → 3」在缝的这一侧：N 个项目也是归属一次 + grant 一次，逐项目
    的旧查询一次都不叫（叫了由 fixture 直接炸）。"""
    projects = [_project() for _ in range(5)]
    team_map = {p.id: (i % 2) + 1 for i, p in enumerate(projects)}
    seams.teams.return_value = team_map
    seams.grants.return_value = []

    out = await UsageService(None).project_credits_batch(projects)  # type: ignore[arg-type]

    assert seams.teams.await_count == 1
    assert seams.teams.await_args.args[0] == projects
    assert seams.grants.await_count == 1
    project_ids, team_ids = seams.grants.await_args.args
    assert project_ids == [p.id for p in projects]
    # 小队 id 去重排序后一次带过去。
    assert team_ids == [1, 2]
    assert all(out[p.id]["unlimited"] for p in projects)


async def test_batch_with_no_projects_asks_nothing(seams):
    """空批次不该发任何 grant 查询（`list_for_scope` 对空条件直接短路），
    但归属那一次是仓储自己的事，这里不钉它。"""
    seams.teams.return_value = {}
    seams.grants.return_value = []

    out = await UsageService(None).project_credits_batch([])  # type: ignore[arg-type]

    assert out == {}
    seams.grants.assert_awaited_once_with([], [])
