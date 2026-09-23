"""纯层（`tests/unit`）共用的默认值。

这一层跑在**没有数据库**的环境里（`test.yml` 把两个 URL 指向没人监听的端口），
而一台机器只有一种启动环境之后，开屏必经 `_device_ccproxy_upstream`——它开一个
会话去读 device 行。所以默认值放在这里，而不是放在第一个撞上它的文件里：开屏
是「接着上一段对话说」「系统提示词投递」这些测试里的一个步骤，不是它们的主题，
每个文件自己补一遍，等于让下一个文件重新发现同一个超时。

真要测「这台机器带着自己的 ccproxy 身份」的那几条，自己 monkeypatch 回真值。
"""

import pytest

from app.domain.agent.device_provider import DeviceChannel


@pytest.fixture(autouse=True)
def _device_brings_no_ccproxy_identity(monkeypatch):
    async def none(_self, _device_id):
        return ""

    monkeypatch.setattr(DeviceChannel, "_device_ccproxy_upstream", none)
