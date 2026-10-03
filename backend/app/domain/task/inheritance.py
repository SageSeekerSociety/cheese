"""这一道题「建项目会继承到什么」—— 领题那一步要给人看的那张清单 (#944)。

**为什么要有这个模块。** 「领取这道题」和「从这道题建项目」是建项目之前唯一一次
看清代价的机会：一个项目吃到多少算力、agent 开场读到哪一份指导。这些今天都已经
算得出来，只是散在两条读法里（协议在 ``protocol.resolve``、指导在
``_resolve_teaching``）。成员在按下按钮之前看不到其中任何一条，按下之后才知道
—— 所以这里把它们收成**一份**读结果。

**为什么不在这里重算一遍。** 这个模块不解析继承，它**问**。协议走
``teaching.protocol_for_task()`` 那一句（它自己再落到 ``resolve``），连「来自
哪一层」也是那一个循环带出来的
（``Protocol.teaching_source``），不另写一份判层逻辑。合成只有一份 —— 这一页说
「你会拿到空间那份指导」，项目的 agent 下一秒读到的就必须是同一份；两份合成迟早
会在某次编辑后对不上，而那一天没人会同时看到两个界面。

**这一层只答「看得见吗」**：看不见就返回 None，调用方转成 404
（``TaskVisibilityService.can_view_task``）。**「还没过审」与「超出本板上限」不在
这里** —— 那是接口那条读法上的两道闸（``services.ensure_task_readable``），
路由在调这个函数之前先过；这一层是域里的可见性，那两道是接口的读策略。合成结果
本身没有「该不该给」的判断，也正因为如此，**谁调它谁负责先把闸过完** —— 一份
「建项目会交出什么」的清单不比题目详情更公开。层次行的取法复用
``teaching.protocol_for_task``（项目集 / 空间两级在那一处补齐后再 ``resolve``），
不自己写第二个 join。

**资料（「会被带上的资料」）不在这里。** 板上的资料是 ``app.domain.space`` 那一
域的行，取它要经 ``SpaceMaterialService``。这一域去 import 它是跨域的边，
``.importlinter`` 的 C3 不许新增（今天 ``app.domain.task`` 与
``app.domain.space`` 之间的环已经存在，每一条边都逐条冻结在案）。所以资料由路由
那一层取：``app.api.routes.task_inheritance`` 拿这个结果里的 ``space_id``
去问 ``SpaceMaterialService``，可见性筛选是那一条读法自己的事。
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import Task
from app.domain.task.protocol import Teaching
from app.domain.task.teaching import protocol_for_task
from app.domain.task.visibility_service import TaskVisibilityService


@dataclass(frozen=True)
class TaskInheritance:
    """建项目之前，这一道题会交出去的协议部分 —— 一份人看的清单。"""

    task_id: int
    #: 这道题挂在哪块板上。调用方拿它去问板上的资料清单。
    space_id: int
    #: 机构给的资源包原文，例如 ``{"compute_credits": 100}``。空 dict = 没说。
    resource_pack: dict = field(default_factory=dict)
    #: 四层合成后的教学指导（空间 → 项目集 → 题目 → 项目，最具体的赢）。
    teaching: Teaching = field(default_factory=Teaching)
    #: 上面那份指导**来自哪一层** —— ``space`` / ``category`` / ``task`` /
    #: ``project``，或 None（四层都没说）。由 ``resolve`` 的同一个循环带出，界面
    #: 据此写「来自项目集」而不是只给一串合成结果。
    teaching_source: str | None = None


async def for_task(
    *, session: AsyncSession, task_id: int, user_id: int
) -> TaskInheritance | None:
    """这一道题建项目会继承什么；题不存在或用户看不见时 None。

    判据与 ``GET /tasks/{id}`` 同一句：看不见这题就当它不存在。
    """
    task = await session.get(Task, task_id)
    if task is None:
        return None
    if not await TaskVisibilityService(session=session).can_view_task(
        task=task, user_id=user_id
    ):
        return None

    protocol = await protocol_for_task(session, task)
    return TaskInheritance(
        task_id=task.id,
        space_id=task.space_id,
        resource_pack=dict(protocol.resource_pack),
        teaching=protocol.teaching,
        teaching_source=protocol.teaching_source,
    )
