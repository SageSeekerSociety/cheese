"""课程级教学配置 → 这一轮 agent 开场时读到的那一段。

`protocol.resolve` 回答的是「配置是什么」；这个模块回答的是「这一轮的它长什么
样」——把课件/知识材料的引用换成名字和链接，把课程名带上，然后交给
`harness.prompt.teaching_section` 去说。取数和措辞分开，是因为两者失败的方式
不一样：这里失败是查不到东西，那里失败是话说错了。

**引用，不是拷贝。** `material_ids` / `knowledge_ids` 存的是指向现成
`Material` / `Knowledge` 的 id，不是内容的副本。一个课件改了名、一个链接换掉
了，课程不必重新保存配置就跟着变；存副本的话，课程配置会慢慢长成资料库的第二
份，而两份不会同时更新。

**空就是空，而且是不花查询的空。** 非课程项目在这里一次查询都不做：没有
`external_task_id` 直接返回 None，项目集里没配 `teaching` 也一样。这就是「非课
程项目一个字都不多加载」的落点 —— 不是渲染时判断要不要说话，而是这里根本没
取。
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.knowledge.services import KnowledgeService
from app.domain.materials.services import MaterialService
from app.domain.project.models import Project
from app.domain.space.models import SpaceCategory
from app.domain.task.models import Task
from app.domain.task.protocol import Teaching, resolve


@dataclass(frozen=True)
class TeachingContext:
    """一段解析好的教学上下文，等着渲染进 system prompt。"""

    #: The 项目集's name — what the course calls itself. None when the 赛题 has no
    #: category, which is a course-shaped config with nothing to name.
    course: str | None
    teaching: Teaching
    #: The 课件 that still exist: `{id, name, url, type}`.
    materials: list[dict] = field(default_factory=list)
    #: The 知识 entries that are still readable: `{id, name, description}`.
    #:
    #: Name and 描述 only — the `content` blob stays behind the knowledge API.
    #: A 项目集 is allowed to point at material of any size, and a prompt that
    #: grows with someone else's uploads is a prompt that eventually does not
    #: fit; an entry the agent wants in full it can fetch by id.
    knowledge: list[dict] = field(default_factory=list)


async def for_project(
    *, session: AsyncSession, project: Project | None
) -> TeachingContext | None:
    """The teaching context this project's agents should start with, or None.

    Read fresh on every turn rather than remembered on the project: the week is
    a fact about now, and a course that moved from 第 3 周 to 第 4 周 must not
    need every project re-registered to hear about it. What that does and does
    not reach is the 生效语义 `harness.prompt` documents — a running session
    keeps the copy it started with, so this is the answer for the NEXT one.
    """
    if project is None or project.external_task_id is None:
        return None
    row = (
        await session.execute(
            select(Task, SpaceCategory)
            # outerjoin: a 赛题 whose 项目集 was deleted still carries its own
            # `protocol_override`, and that override is a teaching config too.
            .outerjoin(SpaceCategory, SpaceCategory.id == Task.category_id)
            .where(Task.id == project.external_task_id)
        )
    ).first()
    if row is None:
        return None
    task, category = row
    teaching = resolve(category=category, task=task, project=project).teaching
    if teaching.is_empty:
        return None
    materials = await MaterialService.for_lookup(session).get_many(
        teaching.material_ids
    )
    knowledge = await KnowledgeService.for_lookup(session).get_many(
        teaching.knowledge_ids
    )
    return TeachingContext(
        course=category.name if category is not None else None,
        teaching=teaching,
        materials=[
            {"id": m.id, "name": m.name, "url": m.url, "type": m.type}
            for m in materials
        ],
        knowledge=[
            {"id": k.id, "name": k.name, "description": k.description}
            for k in knowledge
        ],
    )
