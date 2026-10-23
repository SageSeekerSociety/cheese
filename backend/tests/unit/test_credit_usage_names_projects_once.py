"""额度路由一次取完整批项目名和题目名，不按行逐条 ``get``。

额度页把 ``projects`` 行和 ``packs`` 里引用的项目都按 id 问一次项目表，同一个项目
会在这两组里交叉出现；额度后台的团队页则只有 ``packs``。逐条 ``get`` 是每行一次
往返，现在两处都先把 id 合成一份去重的全集，再用 ``get_projects_by_ids`` /
``get_tasks_by_ids`` 一次取完。

这里钉四件事：

- 三个项目在两组里交叉出现时，项目表只查一次，且拿到的是去重后的全集；
- 题目表也只查一次，有题目的 pack 不再各问一次；
- 输出与逐条版逐字段相同：项目名、题目名、项目不在表里时是 ``None``，以及没有
  项目时那个字段压根不出现；
- 把批量换回逐条，同一条计数断言就红 —— 这是本文件存在的理由，不然计数写成
  多大都照样绿。这一条跑的是真函数，只把批量取那个方法换成逐条取。
"""

import uuid
from types import SimpleNamespace

import pytest

from app.api.routes import admin_credits, credit_usage

# 三个项目在两组里交叉出现：ALPHA 既是 spend 行又是 pack 的目标，BETA 只在项目
# 行，GAMMA 只在 packs 里。MISSING 是个合法 id，但项目表里没有它 —— 改之前的
# ``get`` 会返回 None，批量版也得是 None 而不是 KeyError。
ALPHA, BETA, GAMMA, MISSING = (uuid.uuid4() for _ in range(4))
PROJECTS = {
    ALPHA: SimpleNamespace(name="Alpha"),
    BETA: SimpleNamespace(name="Beta"),
    GAMMA: SimpleNamespace(name="Gamma"),
}
TASKS = {
    "t-first": SimpleNamespace(name="第一题"),
    "t-second": SimpleNamespace(name="第二题"),
}


class _Projects:
    """项目那半边：记下每次问的是哪一批、哪几条。

    ``per_row`` 让 ``get_projects_by_ids`` 自己按 id 一条条问 —— 批量被换回逐条
    时就是这个形状，答案不变，往返次数跟着项目数涨。
    """

    def __init__(self, per_row: bool = False) -> None:
        self.per_row = per_row
        self.batches: list[list[uuid.UUID]] = []
        self.singles: list[uuid.UUID] = []

    async def get_projects_by_ids(self, project_ids):
        self.batches.append(list(project_ids))
        if self.per_row:
            found = {}
            for project_id in project_ids:
                project = await self.get(project_id)
                if project is not None:
                    found[project_id] = project
            return found
        return {pid: PROJECTS[pid] for pid in project_ids if pid in PROJECTS}

    async def get(self, project_id):
        self.singles.append(project_id)
        return PROJECTS.get(project_id)


class _Tasks:
    """题目那半边：记下每次问的是哪一批、哪几条。

    ``per_row`` 让 ``get_tasks_by_ids`` 自己按 id 一条条问 —— 批量被换回逐条时
    就是这个形状，答案不变，往返次数跟着题目数涨。
    """

    def __init__(self, per_row: bool = False) -> None:
        self.per_row = per_row
        self.batches: list[list] = []
        self.singles: list = []

    async def get_tasks_by_ids(self, task_ids):
        self.batches.append(list(task_ids))
        if self.per_row:
            found = {}
            for task_id in task_ids:
                task = await self.get_task(task_id)
                if task is not None:
                    found[task_id] = task
            return found
        return {tid: TASKS[tid] for tid in task_ids if tid in TASKS}

    async def get_task(self, task_id):
        self.singles.append(task_id)
        return TASKS.get(task_id)


class _Plans:
    def __init__(self, team: dict) -> None:
        self._team = team

    async def team(self, team_id):
        return self._team


def _install(monkeypatch, module, projects: _Projects, tasks: _Tasks) -> None:
    monkeypatch.setattr(module, "ProjectService", lambda db: projects)
    monkeypatch.setattr(module, "TaskService", SimpleNamespace(of=lambda db: tasks))


def _assert_read_once(projects: _Projects) -> None:
    """项目表只查一次，且没有一条逐行 ``get``。"""
    assert len(projects.batches) == 1, f"项目表查了 {len(projects.batches)} 次"
    assert projects.singles == [], f"逐条取了 {len(projects.singles)} 次"


def _assert_tasks_read_once(tasks: _Tasks) -> None:
    """题目表只查一次，且没有一条逐行 ``get_task``。"""
    assert len(tasks.batches) == 1, f"题目表查了 {len(tasks.batches)} 次"
    assert tasks.singles == [], f"逐条取了 {len(tasks.singles)} 次"


def _report() -> dict:
    """额度页的一份报告；``_plan_models`` 在用例里被替掉，plan 只留骨架。"""
    return {
        "plan": {"key": "pro"},
        "projects": [
            {"id": str(ALPHA), "credits": 6.0},
            {"id": str(BETA), "credits": 2.0},
        ],
        "packs": [
            {"id": "p1", "project_id": str(ALPHA), "task_id": "t-first"},
            {"id": "p2", "project_id": str(GAMMA), "task_id": None},
            {"id": "p3", "project_id": None, "task_id": "t-second"},
            {"id": "p4", "project_id": str(BETA), "task_id": None},
            {"id": "p5", "project_id": str(MISSING), "task_id": None},
        ],
    }


def _team() -> dict:
    """额度后台团队页的一份 pack 列表；最后两条没有项目、项目查不到。"""
    return {
        "packs": [
            {"id": "p1", "project_id": str(ALPHA), "task_id": "t-first"},
            {"id": "p2", "project_id": str(GAMMA), "task_id": None},
            {"id": "p3", "project_id": str(ALPHA), "task_id": "t-second"},
            {"id": "p4", "project_id": None, "task_id": None},
            {"id": "p5", "project_id": str(MISSING), "task_id": None},
        ]
    }


@pytest.fixture
def no_model_catalog(monkeypatch):
    """``_plan_models`` 问的是模型网关，这里只测项目名。"""
    monkeypatch.setattr(credit_usage, "_plan_models", lambda plan: [])


@pytest.mark.anyio
async def test_a_cross_listed_page_reads_the_project_table_once(
    monkeypatch, no_model_catalog
):
    """ALPHA 在两组里都出现，项目表仍只问一次，且拿到的是去重后的四个 id。"""
    projects, tasks = _Projects(), _Tasks()
    _install(monkeypatch, credit_usage, projects, tasks)

    out = await credit_usage._named(object(), _report())

    _assert_read_once(projects)
    _assert_tasks_read_once(tasks)
    assert sorted(projects.batches[0]) == sorted([ALPHA, BETA, GAMMA, MISSING])
    assert sorted(tasks.batches[0]) == sorted(["t-first", "t-second"])

    assert [row["name"] for row in out["projects"]] == ["Alpha", "Beta"]
    packs = out["packs"]
    assert [pack["project_name"] for pack in packs] == [
        "Alpha",
        "Gamma",
        None,
        "Beta",
        None,
    ]
    assert [pack["task_name"] for pack in packs] == [
        "第一题",
        None,
        "第二题",
        None,
        None,
    ]


@pytest.mark.anyio
async def test_a_batch_read_per_row_answers_the_same_and_fails_the_count(
    monkeypatch, no_model_catalog
):
    """把批量取换回逐条取：答案逐字段相同，「只查一次」那条就红。

    跑的是真 ``_named``，只有它依赖的 ``get_projects_by_ids`` 变成一条条 ``get``
    —— 这正是改动前每个 pid 走的那条路。
    """
    batched_projects = _Projects()
    _install(monkeypatch, credit_usage, batched_projects, _Tasks())
    batched = await credit_usage._named(object(), _report())
    _assert_read_once(batched_projects)

    per_row_projects = _Projects(per_row=True)
    _install(monkeypatch, credit_usage, per_row_projects, _Tasks())
    per_row = await credit_usage._named(object(), _report())

    # 逐字段相同：项目名、题目名、查不到的项目是 None。
    assert per_row == batched
    # 但项目表被一条条问了 —— 上面那条「只查一次」在这条实现下会红。
    assert sorted(per_row_projects.singles) == sorted([ALPHA, BETA, GAMMA, MISSING])
    with pytest.raises(AssertionError):
        _assert_read_once(per_row_projects)


@pytest.mark.anyio
async def test_a_per_row_task_read_answers_the_same_and_fails_the_count(
    monkeypatch, no_model_catalog
):
    """把题目改回 ``get_task`` 逐条问：答案逐字段相同，计数断言就红。

    这正是改动前三处调用点各自的形状。``per_row`` 让它走 ``get_task``，
    ``_assert_tasks_read_once`` 必须拒绝。
    """
    batched_tasks = _Tasks()
    _install(monkeypatch, credit_usage, _Projects(), batched_tasks)
    batched = await credit_usage._named(object(), _report())
    _assert_tasks_read_once(batched_tasks)

    per_row_tasks = _Tasks(per_row=True)
    _install(monkeypatch, credit_usage, _Projects(), per_row_tasks)
    per_row = await credit_usage._named(object(), _report())

    assert per_row == batched
    assert per_row_tasks.singles == ["t-first", "t-second"]
    with pytest.raises(AssertionError):
        _assert_tasks_read_once(per_row_tasks)


@pytest.mark.anyio
async def test_the_admin_team_page_reads_the_project_table_once(monkeypatch):
    """额度后台只有 packs 一组，同样一次取完；没有项目的那条不加字段。"""
    team = _team()
    projects, tasks = _Projects(), _Tasks()
    _install(monkeypatch, admin_credits, projects, tasks)
    monkeypatch.setattr(admin_credits, "PlanService", lambda db: _Plans(team))

    out = await admin_credits.team_credits(1, object(), "admin")

    _assert_read_once(projects)
    assert sorted(projects.batches[0]) == sorted([ALPHA, GAMMA, MISSING])
    packs = out["data"]["packs"]
    assert [pack.get("project_name") for pack in packs] == [
        "Alpha",
        "Gamma",
        "Alpha",
        None,
        None,
    ]
    # 没有题目 / 项目的那几条和改之前一样，压根不带那个字段；查不到的项目留下
    # ``project_name: None``，因为那条 pack 是有 project_id 的。
    assert [pack.get("task_name") for pack in packs] == [
        "第一题",
        None,
        "第二题",
        None,
        None,
    ]
    assert "task_name" not in packs[1] and "task_name" not in packs[3]
    assert "project_name" not in packs[3]
    assert packs[4]["project_name"] is None
    _assert_tasks_read_once(tasks)
    assert sorted(tasks.batches[0]) == sorted(["t-first", "t-second"])


@pytest.mark.anyio
async def test_a_batch_read_per_row_keeps_the_admin_page_the_same(monkeypatch):
    """团队页也钉一条：批量换回逐条，答案一样，计数断言会红。"""
    batched_team = _team()
    batched_projects = _Projects()
    _install(monkeypatch, admin_credits, batched_projects, _Tasks())
    monkeypatch.setattr(admin_credits, "PlanService", lambda db: _Plans(batched_team))
    batched = await admin_credits.team_credits(1, object(), "admin")
    _assert_read_once(batched_projects)

    per_row_team = _team()
    per_row_projects = _Projects(per_row=True)
    _install(monkeypatch, admin_credits, per_row_projects, _Tasks())
    monkeypatch.setattr(admin_credits, "PlanService", lambda db: _Plans(per_row_team))
    per_row = await admin_credits.team_credits(1, object(), "admin")

    assert per_row == batched
    assert sorted(per_row_projects.singles) == sorted([ALPHA, GAMMA, MISSING])
    with pytest.raises(AssertionError):
        _assert_read_once(per_row_projects)
