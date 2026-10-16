"""`TopicMemberService` 拆成两半之后，边界自己站得住。

拆的是一条机械的线：方法体里带 `raise` 的留在门面，一句拒绝都不说的搬进
`services/housekeeping.py`。这条线不是排版偏好——两处错误棘轮
（`test_legacy_error_ratchet.py` 和 `test_a_refusal_is_said_with_a_key.py`）按
**文件**记账 `ValidationError` 这类名字的处数，带 `raise` 的方法一动，它们就得跟着
改，而那正是「谁可以、谁不可以」这一半要留在门面、别人改得的地方。

门面是 `services/__init__.py`（原来是 `services.py` 一个 827 行的单文件）。拆成包
而不是两个同级模块，是为了不欠新债：C3 的配对记在包一级，同级模块
`housekeeping` 读 `app.domain.topic.models` 会算成一条新配对。

这里补的是拆分之后新出现的几样东西：

- 搬走的方法仍然是同一个函数对象——调用点、子类的重载和测试打在类/实例上的
  monkeypatch 用的都是旧名字；
- 门面里没有第二份定义（同族继承，不是复制）；
- 新模块不反向 import 门面，也不自己说一句拒绝；
- 老路径 `from app.domain.topic_membership.services import …` 仍然取得到东西。

行为本身由既有测试从门面那条路径覆盖；这里不重复测行为。
"""

import ast
import pathlib

import pytest

from app.domain.topic_membership import services
from app.domain.topic_membership.services import housekeeping

#: 搬进 `housekeeping.py` 的那一半。方法名一个都不能少：漏掉一个，那个方法就可能
#: 悄悄在 services.py 里留下第二份定义，而这里还是绿的。
MOVED = (
    "_ensure_member",
    "_is_agent_handle",
    "addressable_agent_handle",
    "agent_handles",
    "ensure_agent_seat",
    "holds_an_agent_seat",
    "joined_ids",
    "joined_topic_ids",
    "may_speak",
    "migrate_shared_agent_seat",
    "on_project",
    "outside",
    "owner_of",
    "people_handles",
    "people_in",
    "people_of",
    "private_seats",
    "reached",
    "revoke_project_seats",
    "seats",
    "seed",
    "seed_private",
    "seen",
    "take_in",
    "topic_ids_for_member",
)

#: 门面自己留着的那些：闸门（`require_*`）和它们问的项目权限，以及体内带延迟
#: import 的读法（本包的新模块不许有函数体内的 import）。
STAYED = (
    "__init__",
    "_brought_in_by",
    "_channel",
    "_ensure_topic",
    "_on_project",
    "_project_agent_seat",
    "add",
    "administers",
    "hand_over",
    "join",
    "leave",
    "list_for_topic",
    "managed_topic_ids",
    "manages",
    "may_bring_in",
    "may_open_channels",
    "project_people",
    "remove",
    "require_administrator",
    "require_manager",
    "resolve_agent_handle",
    "seat_counts",
    "set_members_only",
    "step_in",
)


def _class_of(module, name: str) -> ast.ClassDef:
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _methods_of(module, name: str) -> set[str]:
    return {
        node.name
        for node in _class_of(module, name).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _imported_modules(module) -> set[str]:
    """每个 import 指到的模块全名，含它点名从里面取的那几个。

    `from a.b import c` 既算 `a.b`，也算 `a.b.c`——只收前半截会漏掉
    `from app.domain.topic_membership import services` 这种写法，而那正是本文件
    自己用的写法。相对 import 按本模块的包名还原成全名。
    """
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    package = (module.__package__ or "").split(".")
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = ".".join(package[: len(package) - node.level + 1])
                base = f"{prefix}.{base}" if base else prefix
            modules.add(base)
            modules |= {f"{base}.{alias.name}" for alias in node.names if base}
        elif isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
    return modules


@pytest.mark.parametrize("name", MOVED)
def test_a_moved_method_is_the_same_object_behind_the_root(name):
    """门面是同族继承：同一个函数对象，不是一行行转发。"""
    method = getattr(services.TopicMemberService, name)
    assert method is getattr(housekeeping.Housekeeping, name)
    assert method.__module__ == housekeeping.__name__


def test_the_root_keeps_the_composition_root_and_the_other_half():
    assert housekeeping.Housekeeping in services.TopicMemberService.__mro__
    assert _methods_of(services, "TopicMemberService") == set(STAYED)
    assert _methods_of(housekeeping, "Housekeeping") == set(MOVED)
    # `addressable_seat` 建的是门面这个类；它留在 `services.py`，调用点不用动。
    assert services.addressable_seat.__module__ == services.__name__


def test_no_moved_definition_is_left_behind_in_the_root():
    """两份定义会各自漂移：搬走了就不能在 services.py 里再写一遍。"""
    left = _methods_of(services, "TopicMemberService") & set(MOVED)
    assert not left, sorted(left)


def test_the_new_module_says_no_refusal():
    """判据本身：这一半里一个 `raise` 都没有。

    有了它，两处错误棘轮就只看 `services.py` 一个文件——搬代码不会再动它们的
    基线，这条线也就不用靠「记得别搬带 raise 的」来守。
    """
    tree = ast.parse(pathlib.Path(housekeeping.__file__).read_text())
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]


def test_the_new_module_does_not_import_the_facade_back():
    """反向 import 就是循环，门面也就不是门面了。"""
    assert "app.domain.topic_membership.services" not in _imported_modules(housekeeping)
