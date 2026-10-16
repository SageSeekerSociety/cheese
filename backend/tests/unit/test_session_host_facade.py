"""门面守卫：``session_host/host.py`` 按职责拆开之后，门面和边界都不许动。

925 行的 ``host.py`` 摊成了四个同包子模块——``starting`` 把一条会话跑起来、
``speaking`` 对它说话、``control`` 问它并放它走、``sessions`` 记这条进程手里的会话
和它依的那些事实——``host.py`` 留成门面：对外名字照样从这里取得到，``SessionHost``
上的方法一个个转发到子模块里那份实现。

调用方（``block``、``room``、``personal``、``document``、几条路由）和已有测试一个都
没改，靠的就是这个门面。这道测试不连数据库，只钉住四件事：

1. 门面把该给的名字都给出来，而且给的是子模块里那**一个**对象。
2. ``SessionHost`` 的方法一个不少、不多，且还是**真方法**（类自己的 ``__dict__``
   里就有），不是 ``__getattr__`` 转发来的。
3. 每个搬走的实现只有一句转发，指向它该去的那个子模块；没搬的仍然定义在
   ``host.py`` 里。
4. 读循环留在 ``host.py``：``READ_WAIT_S``、``GIVE_UP_CHECK_S``、``asyncio`` 是已有
   测试按**模块属性**打补丁的名字，读它们的地方一搬走，补丁就静默失效。
5. 子模块指 ``SessionHost`` 的类型走的是包，不是兄弟模块 ``host``——两条边都有的
   话 C3（``sibling domains are acyclic``）就是个环。
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import textwrap
from pathlib import Path

from app.domain.agent.session_host import control, host, sessions, speaking, starting
from app.domain.agent.session_host.host import SessionHost

#: 这几片要指 ``SessionHost`` 时只能从这个包取（见 ``__init__.py`` 里那条再导出）。
_PACKAGE = "app.domain.agent.session_host"

#: 门面照旧要给出的名字 → 它现在写在哪个子模块里。名字少一个，就是拆分把谁的路
#: 断了；名字指到别处，就是同一件东西有了两个版本。
DOOR: dict[str, str] = {
    "keeps_memory": "sessions",
    "PICTURES": "sessions",
    "with_pictures": "sessions",
    "_Running": "sessions",
    "_seat": "sessions",
    "_mirror": "sessions",
    "RunnerUnsupported": "starting",
}

#: 搬进子模块的方法 → （子模块，那里面接手它的函数）。
FORWARDS: dict[str, tuple[str, str]] = {
    "start": ("starting", "start"),
    "adopt": ("starting", "adopt"),
    "attach": ("starting", "attach"),
    "send": ("speaking", "send"),
    "steer": ("speaking", "steer"),
    "answers": ("speaking", "answers"),
    "stop": ("control", "stop"),
    "release": ("control", "release"),
    "status": ("control", "status"),
    "memory": ("control", "memory"),
    "control": ("control", "control"),
    "control_state": ("control", "control_state"),
}

#: 留在 ``host.py`` 里的：这条进程怎么够到会话（驱动、线、平台地址），它记着什么，
#: 以及读循环本身。``_look`` 是这次新加的——只有它用的 ``QUICK_S`` 和读循环的计时
#: 放在一处，省得同一个「等一下要多久」有两个说法。
OWN = (
    "__init__",
    "hub",
    "_driver",
    "_wire",
    "_api",
    "_look",
    "available",
    "reads_on_accept",
    "_started",
    "_wake",
    "_wait_for_memory",
    "read",
    "_pump",
    "_forget",
    "_wait",
    "stop_listening",
)

#: 这次拆分给这五个文件定的上限。仓库那条总红线是 1500
#: （``.claude/scripts/check-file-sizes.py``）；650 是这一次自己收的，再长就该接着拆。
SPLIT_CEILING = 650


def _part(name: str):
    return importlib.import_module(f"app.domain.agent.session_host.{name}")


def _defined_in(member) -> str:
    if isinstance(member, property):
        member = member.fget
    return member.__module__


def _forward_of(name: str) -> tuple[str, str]:
    """``name`` 这个方法体里那一句转发去的（子模块，函数名）。"""
    source = textwrap.dedent(inspect.getsource(SessionHost.__dict__[name]))
    body = ast.parse(source).body[0].body
    assert len(body) == 1, f"{name} 不只是转发"
    value = body[0].value
    # ``return await x.y(...)`` is an await holding the call.
    if isinstance(value, ast.Await):
        value = value.value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute), (
        f"{name} 里那一句不是调用"
    )
    target = value.func.value
    assert isinstance(target, ast.Name), f"{name} 转发的不是一个模块"
    return target.id, value.func.attr


def test_the_door_still_hands_out_what_it_did() -> None:
    """门面给出的名字，和子模块里那一个是同一个对象。"""
    for name, part in DOOR.items():
        assert getattr(host, name) is getattr(_part(part), name), (
            f"{name} 不再是 {part} 里那一个"
        )


def test_the_names_a_test_patches_are_still_on_this_module() -> None:
    """按模块属性打补丁的三个名字还在，而且还在这个模块上。"""
    assert host.asyncio is asyncio
    assert host.logger.name == "app.domain.agent.session_host.host"
    source = inspect.getsource(host)
    for name in ("READ_WAIT_S", "GIVE_UP_CHECK_S", "QUICK_S"):
        assert f"\n{name} = " in source, f"{name} 不再是这个模块自己的赋值"


def test_the_host_keeps_its_methods_and_they_are_real_ones() -> None:
    """方法一个不少、不多，而且都在类自己的 ``__dict__`` 里。"""
    # 类自己的 ``__dict__``；解释器往里面塞的 ``__module__``、``__firstlineno__``
    # 之类不算它的方法，``__init__`` 是。
    own = {
        name
        for name in vars(SessionHost)
        if name == "__init__" or not name.startswith("__")
    }
    assert own == set(OWN) | set(FORWARDS)
    assert "__getattr__" not in own


def test_every_moved_method_forwards_to_the_part_that_owns_it() -> None:
    """搬走的方法只剩一句转发，而且转发去的那份实现真在那个子模块里。"""
    for name, (part, function) in FORWARDS.items():
        assert _forward_of(name) == (part, function)
        owner = _part(part)
        assert getattr(owner, function).__module__ == owner.__name__


def test_what_did_not_move_is_defined_here_and_the_reading_stays() -> None:
    """没搬的还是这个模块的，读循环尤其是：它读的就是这里的计时。"""
    for name in OWN:
        assert _defined_in(SessionHost.__dict__[name]) == host.__name__, (
            f"{name} 不在 {host.__name__} 里了"
        )
    # 子模块不持有读循环和它读的计时，补丁才不会被旁路掉。
    for part in (starting, speaking, control, sessions):
        for name in ("read", "_pump", "READ_WAIT_S", "GIVE_UP_CHECK_S"):
            assert not hasattr(part, name), f"{part.__name__} 里有 {name}"


def test_the_parts_name_the_host_through_the_package() -> None:
    """包再导出 ``SessionHost``，几片从那儿取类型，不从兄弟模块取。"""
    package = importlib.import_module(_PACKAGE)
    assert package.SessionHost is SessionHost
    for part in (starting, speaking, control):
        source = Path(part.__file__).read_text("utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module == _PACKAGE:
                taken = {alias.name for alias in node.names}
                assert "host" not in taken, f"{part.__name__} 又去取兄弟模块 host 了"
                assert "SessionHost" in taken, f"{part.__name__} 取的还是包里的门"
            if isinstance(node, ast.ImportFrom) and node.module == f"{_PACKAGE}.host":
                raise AssertionError(f"{part.__name__} 直接从 {node.module} 取东西了")


def test_no_part_of_the_split_grows_past_the_ceiling() -> None:
    """拆完的每一份都在这次给自己定的上限以内。"""
    for name in ("host", "starting", "speaking", "control", "sessions"):
        path = Path(host.__file__).with_name(f"{name}.py")
        lines = len(path.read_text("utf-8").splitlines())
        assert lines <= SPLIT_CEILING, f"{path.name} 又长到 {lines} 行"
