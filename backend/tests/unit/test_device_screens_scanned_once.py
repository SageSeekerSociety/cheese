"""设备列表一次请求只扫一遍全部在线屏幕。

``device_hub.all_online_screens()`` 每次都遍历全平台的 screen 表。设备列表原来对
每台设备各调一次（``_device_screens`` 里过滤出本设备的），列 N 台设备就扫 N 遍。
现在 ``_screens_by_device()`` 每个请求调一次、按设备分好组，``_device_screens``
只接收分好的那一组。

这里钉三件事：

1. 真把 ``my_devices`` 跑一遍（3 台设备、50 个 screen），hub 只被扫 1 次，每台设备
   拿到的正好是自己的那些 screen；
2. ``_device_screens`` 体内不再出现 ``all_online_screens``——按设备重扫写不回去；
3. ``my_devices`` 与 ``team_devices`` 在设备循环（含推导式）之外各调一次
   ``_screens_by_device()``，循环里一次也不调。
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import types
import uuid
from typing import Any, cast

from app.api.routes.connector import connections
from app.domain.agent.device_hub import HubScreen
from app.domain.device.supply import Supply

DEVICES = ("dev-a", "dev-b", "dev-c")
# 这里的路径不碰数据库：screen 的名字由下面的 _no_name 顶替。
NO_DB = cast(Any, None)


def _screen(i: int, device_id: str) -> HubScreen:
    return HubScreen(
        sid=f"s{i}",
        device_id=device_id,
        command=[],
        token=f"t{i}",
        agent_user_id=i,
        agent_handle=f"cheese-{i}",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
    )


# 50 个 screen：三台列出的设备各分到一些，还有一台不在列表里的别人的机器。
SCREENS = [_screen(i, (*DEVICES, "someone-else")[i % 4]) for i in range(50)]


class _CountingHub:
    def __init__(self) -> None:
        self.scans = 0

    def all_online_screens(self) -> list[HubScreen]:
        self.scans += 1
        return list(SCREENS)

    def is_online(self, device_id: str) -> bool:
        return True

    def target(self, device_id: str) -> str:
        return "linux-x64"


async def _no_name(db, project_id, topic_id, handle) -> dict:
    return {}


def _device(device_id: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        device_id=device_id,
        name=device_id,
        team_id=7,
        last_seen_at=None,
        supply=Supply.cloud,
    )


class _Resolver:
    async def resolve(self):
        return types.SimpleNamespace(authenticated=True, user_id=7)


class _Service:
    async def list_enrolled_by(self, user_id: int):
        return [_device(d) for d in DEVICES]


async def test_listing_devices_scans_the_hub_once(monkeypatch) -> None:
    hub = _CountingHub()
    monkeypatch.setattr(connections, "device_hub", hub)
    monkeypatch.setattr(connections, "screen_agent_name", _no_name)

    out = await connections.my_devices(
        cast(Any, _Resolver()), cast(Any, _Service()), db=NO_DB
    )

    assert hub.scans == 1
    got = {
        view["device_id"]: [s["sid"] for s in view["screens"]]
        for view in out["devices"]
    }
    assert got == {d: [s.sid for s in SCREENS if s.device_id == d] for d in DEVICES}
    assert all(got.values())


async def test_grouping_then_rendering_scans_once(monkeypatch) -> None:
    hub = _CountingHub()
    monkeypatch.setattr(connections, "device_hub", hub)
    monkeypatch.setattr(connections, "screen_agent_name", _no_name)

    by_device = connections._screens_by_device()
    for d in DEVICES:
        rendered = await connections._device_screens(NO_DB, by_device.get(d, []))
        assert [s["sid"] for s in rendered] == [
            s.sid for s in SCREENS if s.device_id == d
        ]

    assert hub.scans == 1


def _function(name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(connections, name))))
    (node,) = tree.body
    assert isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    return node


def _names_called(node: ast.AST) -> list[str]:
    out = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Name):
                out.append(func.id)
            elif isinstance(func, ast.Attribute):
                out.append(func.attr)
    return out


_LOOPS = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def test_device_screens_no_longer_scans_the_hub() -> None:
    body = _function("_device_screens")
    assert "all_online_screens" not in _names_called(body)
    assert not any(
        isinstance(n, ast.Attribute) and n.attr == "all_online_screens"
        for n in ast.walk(body)
    )


def test_list_endpoints_group_once_outside_their_loop() -> None:
    for name in ("my_devices", "team_devices"):
        fn = _function(name)
        assert _names_called(fn).count("_screens_by_device") == 1, name
        loops = [n for n in ast.walk(fn) if isinstance(n, _LOOPS)]
        assert loops, name
        for loop in loops:
            assert "_screens_by_device" not in _names_called(loop), name
            assert "all_online_screens" not in _names_called(loop), name
