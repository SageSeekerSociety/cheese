"""题目路由包。

原来单文件 app/api/routes/tasks.py 按声明顺序切成相邻的几段，聚合点这一个
router 是 `routes/` 下唯一的 APIRouter —— main.py 会遍历每个路由模块里的所有
APIRouter 实例，子模块的 router 对象只要不进本模块命名空间就不会被重复挂载，
这一点靠 `from . import <mod>`（而不是 from .<mod> import router）保证。

include 顺序＝原来的声明顺序：它决定 first-match，改动后跑
`uv run python -m scripts.route_index --check` 复核。
"""

from fastapi import APIRouter

from . import (
    create,
    drafts,
    files,
    judging,
    lifecycle,
    participation,
    roster,
    submissions,
)

router = APIRouter(tags=["Tasks"])

router.include_router(create.router)
router.include_router(drafts.router)
router.include_router(files.router)
router.include_router(participation.router)
router.include_router(lifecycle.router)
router.include_router(roster.router)
router.include_router(submissions.router)
# 读评测结果那三条（``code-version``、``submissions/{sid}/judge``、
# ``judge/cases/{caseId}``）挂在最后：它们的路径段是新的，挂在哪都不和上面几条撞，
# 但挂最后能让已有路由的 first-match 位置一个字不动。
router.include_router(judging.router)
# 出题人试跑：``POST /spaces/{spaceId}/test-trials``，权限看空间。
router.include_router(judging.space_router)
