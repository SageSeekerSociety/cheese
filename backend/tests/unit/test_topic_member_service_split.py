"""`TopicMemberService` 拆成两半之后，边界自己站得住。

拆的是一条机械的线：方法体里带 `raise` 的留在门面，一句拒绝都不说的搬进
`services/housekeeping.py`。这条线不是排版偏好——两处错误棘轮
（`test_legacy_error_ratchet.py` 和 `test_a_refusal_is_said_with_a_key.py`）按
**文件**记账 `ValidationError` 这类名字的处数，带 `raise` 的方法一动，它们就得跟着
改，而那正是「谁可以、谁不可以」这一半要留在门面、别人改得的地方。

门面是 `services/__init__.py`（原来是 `services.py` 一个 827 行的单文件），搬走的
方法在类上留一行转发，函数体住在 `housekeeping.py`，接收者是 `svc`——跟
`app/domain/oauth/services/` 的 `tokens.py` / `connections.py` 一个写法。拆成包
而不是两个同级模块，是为了不欠新债：C3 的配对记在包一级，同级模块
`housekeeping` 读 `app.domain.topic.models` 会算成一条新配对。

这里补的是拆分之后新出现的几样东西：

- 每个搬走的方法在门面上是一行转发，签名一个不漏（漏一个签名，调用点就可能悄悄
  换掉形参）；
- 函数体只有一份，在 `housekeeping.py` 里；门面里没有第二份定义；
- 新模块不反向 import 门面，也不自己说一句拒绝；
- 老路径 `from app.domain.topic_membership.services import …` 仍然取得到东西。

行为本身由既有测试从门面那条路径覆盖；这里不重复测行为。
"""

import ast
import inspect
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


def _module_functions(module) -> set[str]:
    return {
        node.name
        for node in ast.parse(pathlib.Path(module.__file__).read_text()).body
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


def _forward_call(method):
    """一行转发的那句调用：`return [await] housekeeping.<name>(...)`。

    不是转发就 None。函数体多一句都不算——判断只许住在新模块里。
    """
    tree = ast.parse(pathlib.Path(inspect.getsourcefile(method)).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method.__name__:
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return None
        call = node.body[0].value
        if isinstance(call, ast.Await):
            call = call.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "housekeeping"
            and call.func.attr == method.__name__
        ):
            return call
        return None
    return None


def _is_forwarder(method) -> bool:
    return _forward_call(method) is not None


@pytest.mark.parametrize("name", MOVED)
def test_a_moved_method_is_a_forwarder_at_the_old_name(name):
    """老名字还挂在 `TopicMemberService` 上，而且只是转发到新模块。

    转发（而不是继承）是这里该有的形状：一条判据只有一处实现，读 `__init__.py`
    就知道类上有没有判断；签名留在门面，调用点和子类重载不用动。
    """
    method = getattr(services.TopicMemberService, name)
    assert method.__module__ == services.__name__, method.__module__
    assert _is_forwarder(method), name
    assert getattr(housekeeping, name).__module__ == housekeeping.__name__


def test_the_root_keeps_the_composition_root_and_the_other_half():
    assert _methods_of(services, "TopicMemberService") == set(STAYED) | set(MOVED)
    assert set(MOVED).isdisjoint(STAYED)
    assert _module_functions(housekeeping) == set(MOVED) | {"_public"}
    # `addressable_seat` 建的是门面这个类；它留在 `services.py`，调用点不用动。
    assert services.addressable_seat.__module__ == services.__name__


def _own_arguments(signature) -> list[tuple]:
    """签名里除接收者（`self` / `svc`）以外的形参，逐项带种类和默认值。"""
    values = list(signature.parameters.values())
    if values and values[0].name in ("self", "svc"):
        values = values[1:]
    return [(p.kind, p.name, p.default) for p in values]


@pytest.mark.parametrize("name", STAYED)
def test_a_stayed_method_is_not_a_forwarder(name):
    """留在门面的那些，判断必须还在类里。

    它们要是也变成转发（或者转发到别处），`raise` 就跟着搬家，两处按文件记账的
    棘轮会悄悄少一处 —— 那时这条测试和棘轮一起红，比棘轮单独红好读。
    """
    if name == "__init__":
        return
    assert not _is_forwarder(getattr(services.TopicMemberService, name)), name


@pytest.mark.parametrize("name", MOVED)
def test_a_moved_function_takes_the_instance_first(name):
    """子模块的函数首参是 `svc`（`_is_agent_handle` 是静态判据，两边都不收）。"""
    parameters = list(inspect.signature(getattr(housekeeping, name)).parameters)
    if name == "_is_agent_handle":
        assert parameters == ["handle"]
    else:
        assert parameters[0] == "svc"


@pytest.mark.parametrize("name", MOVED)
def test_a_forwarder_hands_over_every_argument(name):
    """转发把每一个形参原样递过去，一个不漏、一个不错位。

    漏掉一个关键字参数是最容易犯也最难发现的退化了：签名还在，只是调用时少送
    一个，`team` 那种必填关键字会当场 TypeError，而带默认值的会悄悄换个行为。
    """
    method = getattr(services.TopicMemberService, name)
    call = _forward_call(method)
    assert call is not None, name
    parameters = inspect.signature(method).parameters
    own = [p for key, p in parameters.items() if key != "self"]
    wanted_positional = [
        p.name for p in own if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    wanted_keywords = [p.name for p in own if p.kind == p.KEYWORD_ONLY]
    receiver = ["self"] if "self" in parameters else []
    assert [ast.unparse(a) for a in call.args] == receiver + wanted_positional
    assert {k.arg: ast.unparse(k.value) for k in call.keywords} == {
        name: name for name in wanted_keywords
    }


@pytest.mark.parametrize("name", MOVED)
def test_a_forwarders_signature_is_the_original_one(name):
    """形参一个都没变：转发最容易悄悄丢掉关键字参数。"""
    facade = getattr(services.TopicMemberService, name)
    assert _own_arguments(inspect.signature(facade)) == _own_arguments(
        inspect.signature(getattr(housekeeping, name))
    )


def test_no_definition_is_left_behind_in_both_places():
    """两份定义会各自漂移：搬走了就不能在 services.py 里再写一遍。"""
    in_facade = _methods_of(services, "TopicMemberService") & set(MOVED)
    in_submodule = _module_functions(housekeeping) & set(MOVED)
    assert in_facade == set(MOVED), sorted(set(MOVED) - in_facade)
    assert in_submodule == set(MOVED), sorted(set(MOVED) - in_submodule)


def test_the_new_module_says_no_refusal():
    """判据本身：这一半里一个 `raise` 都没有。

    有了它，两处错误棘轮就只看 `services.py` 一个文件——搬代码不会再动它们的
    基线，这条线也就不用靠「记得别搬带 raise 的」来守。
    """
    tree = ast.parse(pathlib.Path(housekeeping.__file__).read_text())
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]


def test_the_new_module_only_names_the_facade_under_type_checking():
    """运行时不许反向 import 门面——那是循环。

    子模块给 `svc` 标注类型时要提到门面这个类，跟
    `app/domain/oauth/services/tokens.py` 一样，放在 `if TYPE_CHECKING:` 里：
    注解求值时它才出现，真跑起来这条边不存在。这里把所有不在
    `TYPE_CHECKING` 块里的 import 收出来，一个都不许指回门面。
    """
    tree = ast.parse(pathlib.Path(housekeeping.__file__).read_text())
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            guarded |= {id(child) for child in ast.walk(node)}
    runtime: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom):
            runtime.add(node.module or "")
            runtime |= {f"{node.module}.{a.name}" for a in node.names if node.module}
        elif isinstance(node, ast.Import):
            runtime |= {a.name for a in node.names}
    facade = f"{services.__name__}."
    backwards = [name for name in runtime if name.startswith(facade)]
    assert not backwards, sorted(runtime)
