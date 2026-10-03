"""题目路由包。

原来单文件 app/api/routes/tasks.py 按声明顺序切成相邻的几段，聚合点这一个
router 是 `routes/` 下唯一的 APIRouter —— main.py 会遍历每个路由模块里的所有
APIRouter 实例，子模块的 router 对象只要不进本模块命名空间就不会被重复挂载，
这一点靠 `from . import <mod>`（而不是 from .<mod> import router）保证。

include 顺序＝原来的声明顺序：它决定 first-match，被 route_index.json 与
route_index_frozen.py 钉住，改动必须重生成索引并复核。
"""

from fastapi import APIRouter

from . import (
    attachments,
    create,
    lifecycle,
    participation,
    publish_pdf,
    roster,
    submissions,
)

router = APIRouter(tags=["Tasks"])

router.include_router(create.router)
router.include_router(attachments.router)
router.include_router(publish_pdf.router)
router.include_router(participation.router)
router.include_router(lifecycle.router)
router.include_router(roster.router)
router.include_router(submissions.router)
