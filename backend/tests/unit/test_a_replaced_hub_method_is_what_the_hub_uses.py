"""替换掉 hub 上的一个方法之后，hub 自己身子里的调用也换成那一个。

hub 的几处身子是合起来做一个决定的：关屏幕要先把 `session.close` 交给机器并等它
确认（`session_request`），「哪块屏幕还在」要看链路状态（`is_online`）。这些内部调用
必须落在对象上（`hub.` 而不是某个模块里的函数），否则换掉方法的人只换到他自己那一
次调用，hub 内部照旧走旧的路——集成测试是把机器判成在线/离线来跑设备的，就是靠这
一层。拆成包时最容易悄悄丢掉它。
"""

import uuid

import pytest

from app.domain.agent import device_hub as facade
from app.domain.agent.device_hub import DeviceHub


class FakeDeviceTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _machines_here_do_not_come_back(monkeypatch):
    """丢链路的机器立刻算不在，不等它回来（那件事是 test_device_reconnect_grace）。"""
    monkeypatch.setattr(facade, "RECONNECT_GRACE_S", 0.0)


def _screen_args() -> dict:
    return {
        "agent_user_id": uuid.uuid4(),
        "agent_handle": "agent-x",
        "project_id": uuid.uuid4(),
        "topic_id": uuid.uuid4(),
    }


async def test_close_and_list_screens_go_through_a_replaced_session_request():
    hub = DeviceHub()
    await hub.attach_device("dev", FakeDeviceTransport())
    screen = await hub.open_screen("dev", ["sleep", "60"], **_screen_args())

    asked: list[tuple[str, dict]] = []

    async def replaced(device_id, message):
        asked.append((device_id, message))
        return {"screens": []}

    hub.session_request = replaced

    assert await hub.close_screen("dev", screen.sid) is True
    assert await hub.list_screens("dev") == {"screens": []}
    assert [message["t"] for _, message in asked] == ["session.close", "session.list"]


async def test_screen_queries_count_what_a_replaced_is_online_says():
    hub = DeviceHub()
    await hub.attach_device("dev", FakeDeviceTransport())
    project_id = uuid.uuid4()
    await hub.open_screen(
        "dev", ["sleep", "60"], **{**_screen_args(), "project_id": project_id}
    )

    hub.is_online = lambda device_id: False

    assert hub.screens_in_project(project_id) == []
    assert hub.all_online_screens() == []
