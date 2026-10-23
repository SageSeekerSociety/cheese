"""一次设备掉线的 409 带什么头，全平台只有一个答案。

``offline_headers`` 给出 ``X-Device-Id``（机器不在），链路断在一次调用的半路时再加
``X-Device-Link: interrupted``（这件事做没做过，这一侧不知道）。两条 HTTP 出口都读
它：业务后端的错误树（``core/errors.py``）和连接所有者
（``device_connection_app.py``）。两份逐字相同的实现一旦分叉——加一个头、改一个取值
——其中一条出口的客户端就读不到新信号，而且没有任何调用点会因此报错。

判据是**同一个函数对象**：两份改成一模一样也要红。要拦的就是「再抄一份」。
"""

from app.core import device_failures
from app.domain.agent import device_contract, device_hub


def test_the_hub_and_the_contract_re_export_the_one_rule():
    assert device_hub.offline_headers is device_failures.offline_headers
    assert device_contract.offline_headers is device_failures.offline_headers


def test_an_offline_machine_is_named_by_its_id():
    assert device_hub.offline_headers(device_failures.DeviceOffline("m-1")) == {
        "X-Device-Id": "m-1"
    }


def test_a_link_that_broke_mid_call_says_the_outcome_is_unknown():
    assert device_hub.offline_headers(device_failures.LinkInterrupted("m-1")) == {
        "X-Device-Id": "m-1",
        "X-Device-Link": "interrupted",
    }
