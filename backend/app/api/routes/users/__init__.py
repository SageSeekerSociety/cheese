"""users 路由包。

原来单文件 app/api/routes/users.py 按声明顺序切成相邻的几段，聚合点这一个
router 是 `routes/` 下唯一的 APIRouter —— main.py 会遍历每个路由模块里的所有
APIRouter 实例，子模块的 router 对象只要不进本模块命名空间就不会被重复挂载，
这一点靠 `from . import <mod>`（而不是 from .<mod> import router）保证。

include 顺序＝原来的声明顺序：它决定 first-match（`/users/{userId}` 会遮住后注册
的同形路径），被 route_index.json 与 route_index_frozen.py 钉住，改动必须重生成
索引并复核。
"""

from fastapi import APIRouter

from . import (
    account,
    auth,
    connections,
    directory,
    invite_codes,
    oauth,
    preferences,
    registration,
)

# 标签挂在各子 router 上：聚合点再加一次 `tags=["Users"]`，include 时会被拼成
# `["Users", "Users"]`，OpenAPI 就不是逐字节相同的了。
router = APIRouter()

router.include_router(directory.router)
router.include_router(registration.router)
router.include_router(account.router)
router.include_router(auth.router)
router.include_router(preferences.router)
router.include_router(oauth.router)
router.include_router(invite_codes.router)
router.include_router(connections.router)
