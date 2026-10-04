"""The cloud channel: sessions whose tools run in a sandbox on a platform host.

The session itself runs on the central session host (``central_provider``);
this channel only names where its hands are. Which host a session's sandbox is
on is the host pool's to decide when the session first needs hands
(``machine/session_work.ensure``), so nothing here picks or prepares a machine.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.device_provider import DeviceChannel
from app.domain.device.supply import Supply


class CloudChannel(DeviceChannel):
    name = "cloud"
    # 平台开的机器。同一台物理 VM 由人自己接进来时是 self_hosted：入口决定待遇，
    # 机器长什么样不决定 (#282 决定 2)。基类的 `owns` 读的就是这一位。
    supply = Supply.cloud

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker | None = None,
        hub: DeviceHub | None = None,
        configured: bool,
    ) -> None:
        super().__init__(session_factory=session_factory, hub=hub)
        self._configured = configured

    def available(self) -> bool:
        return self._configured
