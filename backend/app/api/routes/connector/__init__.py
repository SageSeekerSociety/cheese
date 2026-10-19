"""connector 路由包。

原来单文件 ``app/api/routes/connector.py`` 按声明顺序切成相邻的几段：

- ``devices.py`` —— 设备流（``/auth/device/*``、``/connect``）、设备控制通道
  （``/device/me``、``/agent``）与机器重连时的业务恢复；
- ``screen.py`` —— 现场 viewer（``/session/{sid}/screen``）；
- ``connections.py`` —— 「我的设备 / Agent」管理（``/my/*``、``/teams/*``）；
- ``schemas.py`` —— 线上形状：这些路由的请求与响应模型。

门面里这一个 router 是 ``routes/`` 下唯一的 APIRouter —— main.py 会遍历每个路由模块
里的所有 APIRouter 实例，子模块的 router 对象只要不进本模块命名空间就不会被重复挂载，
这一点靠 ``from . import <mod>``（而不是 from .<mod> import router）保证。

include 顺序＝原来的声明顺序：它决定 first-match，改动后跑
``uv run python -m scripts.route_index --check`` 复核。
"""

from fastapi import APIRouter

from . import connections, devices, running_sessions, screen

# 前缀与标签挂在各子 router 上：聚合点再加一次 `tags=["connector"]`，include 时
# 会被拼成 `["connector", "connector"]`，OpenAPI 就不是逐字节相同的了。
router = APIRouter()

router.include_router(devices.router)
router.include_router(screen.router)
router.include_router(connections.router)

router.include_router(running_sessions.router)
