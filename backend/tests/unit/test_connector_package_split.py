"""门面守卫：``api/routes/connector.py`` 拆成同名包之后，路由面一个不多、一个不少。

``app/api/routes/connector.py``（1174 行）按原来的声明顺序切成相邻的四段身子，门面
``connector/__init__.py`` 把三个子 router 按那个顺序接起来：

- ``devices.py`` —— 设备流（``/auth/device/*``、``/connect``）与设备控制通道
  （``/device/me``、``/agent``）；
- ``screen.py`` —— 现场 viewer（``/session/{sid}/screen``）；
- ``connections.py`` —— 「我的设备 / Agent」管理（``/my/*``、``/teams/*``）；
- ``schemas.py`` —— 这些路由的请求与响应模型。

拆成**包**而不是四个同级模块：``routes/`` 下每个模块都会被
``main._discover_routers`` 逐个 import，并把它命名空间里**每一个** ``APIRouter``
挂上 app（按 ``id()`` 去重）。四个同级模块会把 include 顺序交给文件名的字母序，而
first-match 是 ``scripts/route_index_frozen.py`` 按「路径＋处理函数」钉住的，字母序
排不出来。

这道测试钉四件事：

1. 旧的单文件不在了，身子住在该住的四个文件里，每个名字只有一处实现；
2. 门面的命名空间里**只有一个** ``APIRouter`` —— 子 router 对象漏进来一个，
   ``_discover_routers`` 就会把它再挂一遍，症状是同一批路径被注册两次；
3. 接起来的顺序就是原来的声明顺序：``devices → screen → connections``；
4. 按 discovery 的同一句话挂一遍，每个 connector 路径只出现一次，而且和门面自己展开
   的那一份逐条相同。
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi import APIRouter, FastAPI

from app.api.routes import connector
from app.api.routes.connector import connections, devices, schemas, screen
from scripts.route_index import collect

PACKAGE = Path(connector.__file__).resolve().parent

#: 身子模块，按声明顺序。
PIECES = (devices, screen, connections, schemas)

#: 带路由的那三个，按门面 include 它们的顺序 —— 也就是旧文件里的声明顺序。
ORDER = (devices, screen, connections)

#: 每个从旧文件搬出来的名字现在住哪儿，照搬运后那份文件抄的。``router`` 和
#: ``logger`` 是每个带路由的身子各自的一句（``schemas`` 没有），不在这张表里。
HOME = {
    "devices.py": (
        "get_device_service",
        "DeviceServiceDep",
        "_RECOVERY_AT_ONCE",
        "_recovering",
        "recover_business_state",
        "_check_sandbox",
        "_recover_business_state",
        "device_start",
        "device_poll",
        "device_proposed_name",
        "device_connect",
        "_WebSocketDeviceTransport",
        "device_me",
        "agent_socket",
        "SEEN_EVERY_S",
        "_note_seen",
    ),
    "screen.py": (
        "_WebSocketViewerTransport",
        "_sign_in_lasts",
        "_may_view_screen",
        "_as_int",
        "VIEW_RECHECK_EVERY_S",
        "viewer_socket",
    ),
    "connections.py": (
        "_require_user",
        "_device_screens",
        "_device_view",
        "my_devices",
        "_my_self_hosted",
        "_login_out",
        "my_model_connections",
        "_my_account_connection",
        "add_account_connection",
        "check_account_connection",
        "rename_account_connection",
        "remove_account_connection",
        "check_my_claude_code",
        "revoke_my_claude_code",
        "rename_my_claude_code",
        "use_my_model_service",
        "check_my_sandbox",
        "rename_my_device",
        "unbind_my_device",
        "register_device_for_team",
        "team_devices",
        "unregister_device_from_team",
    ),
    "schemas.py": (
        "DeviceStartRequest",
        "DevicePollRequest",
        "ConnectRequest",
        "RenameDeviceRequest",
        "BindTeamRequest",
        "ClaudeCodeLoginOut",
        "ModelConnectionOut",
        "AccountConnectionOut",
        "ModelConnectionsOut",
        "AccountConnectionIn",
        "ConnectionRename",
        "ModelServiceRequest",
    ),
}

#: 每个带路由的身子自己那一句，`schemas` 只放模型，两样都没有。
PER_PIECE = ("router", "logger")
WITH_ROUTES = ("devices.py", "screen.py", "connections.py")


def _top_level_names(path: Path) -> set[str]:
    """这份文件在自己这一层定义的名字：函数、类、赋值（含带标注的赋值）。"""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8"), str(path)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _where_each_name_is_defined() -> dict[str, list[str]]:
    """名字 → 定义它的那几个文件名，按 ``PIECES`` 的顺序。"""
    found: dict[str, list[str]] = {}
    for module in PIECES:
        path = Path(module.__file__)
        for name in sorted(_top_level_names(path)):
            found.setdefault(name, []).append(path.name)
    return found


def _surface(routes) -> list[tuple[str, str, str, frozenset[str]]]:
    """挂出来的每一条：协议、路径、处理函数名、方法。顺序就是注册顺序。"""
    return [
        (record.protocol, record.path, record.name, record.methods)
        for record in collect(routes)
    ]


def test_the_single_file_is_gone_and_the_pieces_are_here() -> None:
    """旧路径那份模块不能再在：它和包同名，留着就是两份实现。"""
    assert not (PACKAGE.parent / "connector.py").exists()
    assert {path.name for path in PACKAGE.glob("*.py")} == {
        "__init__.py",
        "devices.py",
        "screen.py",
        "connections.py",
        "schemas.py",
    }


def test_every_name_has_exactly_one_home() -> None:
    """一个名字在两个文件里各留一份，是这种拆分最容易出的错，两边还都跑得通。"""
    expected: dict[str, list[str]] = {
        name: [piece] for piece, names in HOME.items() for name in names
    }
    for name in PER_PIECE:
        expected[name] = list(WITH_ROUTES)

    assert _where_each_name_is_defined() == expected


def test_the_facade_holds_nothing_of_its_own_but_the_router() -> None:
    """门面自己一个类、一个函数都不定义：它只 `from . import` 再 include。"""
    assert _top_level_names(PACKAGE / "__init__.py") == {"router"}

    assert all(Path(module.__file__).parent == PACKAGE for module in PIECES)


def test_the_facade_hands_discovery_exactly_one_router() -> None:
    """`_discover_routers` 挂的是这里列出来的每一个 APIRouter，漏进来就挂两遍。"""
    exposed = [
        name for name, value in vars(connector).items() if isinstance(value, APIRouter)
    ]

    assert exposed == ["router"]

    # 三个身子是按 `from . import …` 进来的：门面上答出来的就是那三个模块对象，
    # 而不是它们的 router —— 这一句正是上面只有一个 APIRouter 的原因。
    assert connector.devices is devices
    assert connector.screen is screen
    assert connector.connections is connections


def test_the_aggregate_is_the_pieces_in_declaration_order() -> None:
    """顺序变了 first-match 就变了，而那是 `route_index_frozen.py` 钉住的东西。"""
    expected = [row for module in ORDER for row in _surface(module.router)]

    assert _surface(connector.router) == expected
    assert len(expected) == 23


def test_mounting_the_way_discovery_does_keeps_every_path_once() -> None:
    """照 `_discover_routers` 的同一句话挂一遍：每条路径只出现一次。"""
    app = FastAPI()
    for value in vars(connector).values():
        if isinstance(value, APIRouter):
            app.include_router(value)

    mounted = [row for row in _surface(app) if row[1].startswith("/connector")]

    assert mounted == _surface(connector.router)
