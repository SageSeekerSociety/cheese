"""users 路由包。

原来单文件 app/api/routes/users.py 按声明顺序切成相邻的几段，聚合点这一个
router 是 `routes/` 下唯一的 APIRouter —— main.py 会遍历每个路由模块里的所有
APIRouter 实例，子模块的 router 对象只要不进本模块命名空间就不会被重复挂载，
这一点靠 `from . import <mod>`（而不是 from .<mod> import router）保证。

include 顺序决定 first-match（`/users/{userId}` 会遮住后注册的同形路径），改动后跑
`uv run python -m scripts.route_index --check` 复核。
"""

from fastapi import APIRouter

from . import (
    account,
    auth,
    connections,
    directory,
    github_account,
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
# Before account: its `GET /users/{userId}` would otherwise take
# `/users/invite-codes` and answer 400 on the int parse.
router.include_router(invite_codes.router)
# `/users/me/github-account/...` before account's `/users/{userId}/...`.
router.include_router(github_account.router)
router.include_router(account.router)
router.include_router(auth.router)
router.include_router(preferences.router)
router.include_router(oauth.router)
router.include_router(connections.router)
