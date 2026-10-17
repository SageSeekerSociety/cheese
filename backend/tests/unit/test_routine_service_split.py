"""门面守卫：``app.domain.routine.service`` 拆包之后，公开面和行为一个都不能变。

2026-10-15 那次拆分把 ``routine/service.py``（1078 行）按职责拆成
``app/domain/routine/service/`` 这个包：``rules`` 管规则自己那一行（建、启停、
改配置、删、手动执行），``scheduling`` 管现在该不该响，``firing`` 管响了
之后，``settlement`` 管一次执行怎么结束，``archive`` 管归档对账，``sweep`` 是
30 秒一趟的时针，``_shared`` 放处处要用的常量与 ``now``。``RoutineService`` 上
每个方法只剩一句转发。

调用方完全没改：``periodic.py`` 读的仍是 ``from app.domain.routine import service``
并按模块属性取 ``service.sweep``（单测里 ``"app.domain.routine.service.sweep"``
那个 patch 点就压在这上面），路由读 ``routines.publish_run_messages``，集成测试
读 ``from app.domain.routine.service import REPORT_TIMEOUT, sweep``。

有一处老写法**不再成立**，下一个压这两个名字的测试要知道：`_fire` 和
`publish_run_messages` 被子模块**在 import 时按值绑走**了（`rules` / `scheduling`
从 `firing` 直接 import，`sweep` 调自己那一个），所以在包上
``monkeypatch.setattr("app.domain.routine.service._fire", fake)`` 改不到包内的调用点
—— 压上去不报错，只是压了个空。仓库里现在没有测试这么压；要压就压调用点所在的
子模块（`periodic.py` 读的 `service.sweep`、`test_periodic_registration.py` 压的
``"app.domain.routine.service.sweep"`` 都仍挂在包上，不受这条影响）。

这道测试不连数据库，钉住四件事：

1. 旧路径仍然答得出那一组名字（22 个），而且**都是同一个对象**（不是抄了
   一份出来）。少一个，某个调用点就是 ImportError。
2. ``RoutineService`` 的 18 个成员（含 ``__init__``）一个不少，而且是**真方法**
   （类自己的 ``__dict__`` 里就有），不是 ``__getattr__`` 之类动态转发的。
3. ``__init__`` 没动：``_session`` 仍从这里赋值。
4. **拆的是文件，不是行为**：每个方法体恰好一句转发，转给子模块里同名、首参为
   ```svc`` 的那一个函数；转发表与那张形参表逐条对齐（名字、次序、位置还是关键字、
   默认值）。谁把方法体抄回类里一份，或者转发时漏掉一个关键字参数，这条就红。

第 4 条为什么值得这么细：转发漏一个参数是**静默改行为** —— 调用方拿到的是默认值
或 TypeError，而名字集合、方法数、导入路径全都看不出来。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap

import pytest

from app.domain.delivery import agent as delivery_agent
from app.domain.routine import service as routine_service
from app.domain.routine.service import (
    _shared,
    archive,
    firing,
    rules,
    scheduling,
    settlement,
)
from app.domain.routine.service import service as class_module

# 包门面把 `sweep` 这个**函数**再导出了，于是包上那个属性指的是函数（拆之前也是
# 它 —— `periodic.py` 的 `service.sweep` 和单测的 patch 点都要的就是这个）；同名
# 子模块因此取不到属性，要从 sys.modules 里拿。
sweep_module = importlib.import_module("app.domain.routine.service.sweep")

# 18 个成员，按 `service/service.py` 里类体的定义先后抄的。
ROUTINE_SERVICE_METHODS = [
    "__init__",
    "_check_feedback_batch",
    "get",
    "list",
    "runs",
    "latest_run_statuses",
    "_agent_for",
    "create",
    "_activate",
    "_settled",
    "pause",
    "resume",
    "update",
    "run_now",
    "delete",
    "announce_archived_rooms",
    "archived_room_ids",
    "report",
]

#: 方法体搬去的那三个模块，按转发里写的名字。`firing` / `scheduling` / `sweep`
#: 里没有类的方法体，只被同级函数调用，所以不在转发表里。
SIBLINGS = {"rules": rules, "archive": archive, "settlement": settlement}

#: 转发到子模块时改了名的：模块级的 `list` 会盖掉内建（`runs` 要用 `list(...)`）。
RENAMED = {"list": "list_for_project"}

#: 拆之前那份 service.py 的模块名字空间。名字 -> 它现在定义在哪个子模块。
#: 这就是「对外导出完全兼容」那份账：调用点读得出、且是同一个对象。
FACADE = {
    "_shared": [
        "MISSED_GRACE",
        "START_TIMEOUT",
        "REPORT_TIMEOUT",
        "NO_REPORT",
        "EVENT_RUNS_PER_HOUR",
        "EVENT_TRIGGERS",
        "TRIGGER_LABELS",
        "NO_FEEDBACK_WAITING",
        "now",
    ],
    "rules": ["name_of", "validate_spec"],
    "firing": ["run_prompt", "_fire"],
    "scheduling": ["_fire_schedules", "_events_for", "_fire_events"],
    "settlement": ["_turn_failure", "_settle_open_runs", "_announce_finished"],
    "sweep": ["publish_run_messages", "sweep"],
    # 不是本域定义的：拆之前那份 service.py 从 `delivery.agent` import 进来，活在
    # 旧模块的名字空间里，路由按模块属性取它（`routines.dispatch_pending`）。
    "delivery.agent": ["dispatch_pending"],
}


def test_the_old_import_path_still_answers_the_old_names() -> None:
    """22 个名字逐个在场，而且都是子模块里那一个对象——不是又造了一份。"""
    homes = {
        "_shared": _shared,
        "rules": rules,
        "firing": firing,
        "scheduling": scheduling,
        "settlement": settlement,
        "sweep": sweep_module,
        "delivery.agent": delivery_agent,
    }
    missing = []
    copied = []
    for home, names in FACADE.items():
        owner = homes.get(home)
        for name in names:
            if not hasattr(routine_service, name):
                missing.append(name)
                continue
            if owner is not None and getattr(routine_service, name) is not getattr(
                owner, name
            ):
                copied.append(name)
    assert missing == [], f"旧路径答不出的名字：{missing}"
    assert copied == [], f"这些名字被复制了一份，不是同一个对象：{copied}"
    assert routine_service.RoutineService is class_module.RoutineService


def test_the_sweep_patch_point_is_the_package_attribute() -> None:
    """``periodic.py`` 按模块属性取 `service.sweep`，单测也压这个名字。

    `tests/unit/test_periodic_registration.py` 压的是
    ``app.domain.routine.service.sweep``；`periodic.py` 里写的是
    ``service.sweep(...)``。两条都要求这个名字**挂在包上**，而且就是 `sweep`
    子模块里那一个函数 —— 被搬进子模块却忘了再导出，那条测试会静默地压不住。
    """
    assert "sweep" in vars(routine_service), "sweep 没再导出到包上，patch 点没了"
    assert routine_service.sweep.__module__ == "app.domain.routine.service.sweep"
    assert routine_service.publish_run_messages.__module__ == (
        "app.domain.routine.service.sweep"
    )


def test_the_class_keeps_all_18_members() -> None:
    """18 个成员一个不少，而且都是类自己定义的。"""
    assert len(ROUTINE_SERVICE_METHODS) == 18
    found = {name for name, _ in inspect.getmembers(routine_service.RoutineService)}
    assert [n for n in ROUTINE_SERVICE_METHODS if n not in found] == []

    # 真方法：类自己的 __dict__ 里就有，不是动态转发来的。
    own = set(vars(routine_service.RoutineService))
    assert [n for n in ROUTINE_SERVICE_METHODS if n not in own] == []
    assert "__getattr__" not in own


def test_init_still_builds_the_session() -> None:
    """拆分没动 __init__：仍**在这里**把 session 赋给 `_session`。

    看的是赋值本身，不是「源码里出现过这个词」——把赋值改成一句提到 `_session`
    的注释不该让这条过。
    """
    init = ast.parse(
        textwrap.dedent(inspect.getsource(routine_service.RoutineService.__init__))
    ).body[0]
    assert [a.arg for a in init.args.args] == ["self", "session"]

    assigned: dict[str, str] = {}
    for node in ast.walk(init):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned[target.attr] = ast.unparse(node.value)
    assert assigned == {"_session": "session"}


@pytest.mark.parametrize(
    "name", [n for n in ROUTINE_SERVICE_METHODS if n != "__init__"]
)
def test_every_method_is_one_forward_to_its_sibling_module(name: str) -> None:
    """方法体恰好一句转发，转给子模块里的那个函数——实现只有一份。

    转发本身也是行为：每个形参都要按同名的关键字转出去，一个不漏、一个不多；而调用
    方调的是门面，所以**门面自己那张形参表**（名字、顺序、位置还是关键字、默认值）
    必须和子模块实现的一致。少转一个关键字（`topic_id` 丢了）、把 `by` 挪到 `*`
    后面、或者改掉一个默认值，都是静默改行为，这条红。关键字在源码里怎么排版不在此
    列：转发全用关键字，书写顺序对调用方不可观测。
    """
    method = getattr(routine_service.RoutineService, name)
    declaration = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    body = declaration.body
    is_docstring = (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    )
    if is_docstring:
        body = body[1:]  # 方法自己的 docstring 跟着方法走了
    assert len(body) == 1, f"{name} 的方法体不止一句，实现被抄回来了"

    statement = body[0]
    assert isinstance(statement, ast.Return), f"{name} 不是转发"
    call = statement.value
    if isinstance(call, ast.Await):
        call = call.value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    target_module = call.func.value
    assert isinstance(target_module, ast.Name) and target_module.id in SIBLINGS
    assert call.func.attr == RENAMED.get(name, name), (
        f"{name} 转发去了另一个名字 {call.func.attr}"
    )

    # 转发把服务原样交出去：子模块函数的首参就是它。
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "self"
    assert call.args[1:] == [], f"{name} 除了实例还多传了位置参数"

    forwarded = getattr(SIBLINGS[target_module.id], RENAMED.get(name, name))
    assert callable(forwarded)
    parameters = inspect.signature(forwarded).parameters
    assert next(iter(parameters)) == "svc"
    assert not hasattr(forwarded, "__wrapped__"), f"{name} 被子模块包了一层"

    # 一对一：每个形参一个同名的关键字实参，一个不漏、一个不多。
    passed = {keyword.arg: keyword.value for keyword in call.keywords}
    assert None not in passed, f"{name} 用了 **kwargs 摊开，转发不再是一对一"
    assert [n for n in passed if not isinstance(passed[n], ast.Name)] == []
    assert {n: passed[n].id for n in passed} == {n: n for n in passed}, (
        f"{name} 转发时换了名字：{ {n: passed[n].id for n in passed} }"
    )
    expected = list(parameters)[1:]
    missing = set(expected) - set(passed)
    extra = set(passed) - set(expected)
    assert set(passed) == set(expected), (
        f"{name} 的转发表和 {target_module.id}.{RENAMED.get(name, name)} 的形参对不上："
        f"漏了 {missing}，多了 {extra}"
    )

    # 门面自己那张形参表：名字与顺序（AST 里读，看的是写下来的那张表）……
    own = [p for p in method_signature(method) if p != "self"]
    assert own == expected, (
        f"{name} 自己的形参表和 {target_module.id}.{RENAMED.get(name, name)} 的"
        f"不是一张表：{own} vs {expected}"
    )
    # ……以及位置还是关键字、默认值（签名上读）。名字集合看不出来这两样：
    # `update` 的 `by_agent` 挪到 `*` 前面，路由里那句关键字调用就不再是唯一写法；
    # `runs` 的 `limit` 默认值从 50 改掉，调用方拿到的是另一个页大小。
    own_parameters = inspect.signature(method).parameters
    drifted = [
        p
        for p in expected
        if (own_parameters[p].kind, own_parameters[p].default)
        != (parameters[p].kind, parameters[p].default)
    ]
    assert drifted == [], (
        f"{name} 的形参种类或默认值和 {target_module.id}.{RENAMED.get(name, name)} 的"
        f"不一致：{drifted}"
    )


def method_signature(method: object) -> list[str]:
    """方法自己的形参名（含 `self`），用来和转发表对照。"""
    declaration = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = declaration.args
    return [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
