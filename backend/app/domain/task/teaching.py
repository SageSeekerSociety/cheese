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
from app.domain.space.material_service import member_readable_material_ids
from app.domain.space.models import Space, SpaceCategory
from app.domain.task.models import Task
from app.domain.task.protocol import Protocol, Teaching, resolve


async def protocol_for_task(session: AsyncSession, task: Task) -> Protocol:
    """这道题在三/四级链上读到的那份协议：把 项目集 / 空间 两级补齐后再 ``resolve``。

    ``resolve`` 要的是三行（空间、项目集、题目），而调用方常常手上只有一个 ``Task``
    —— 上游两级去哪取、空 id 就不去查，只在这里写一遍，免得每个读者各写一份、慢慢
    长歪（题目级指导 #944 让每个域都得读协议，这处迟早会多起来）。
    """
    category = (
        await session.get(SpaceCategory, task.category_id)
        if getattr(task, "category_id", None)
        else None
    )
    space = (
        await session.get(Space, task.space_id)
        if getattr(task, "space_id", None)
        else None
    )
    return resolve(space=space, category=category, task=task)


@dataclass(frozen=True)
class TeachingContext:
    """一段解析好的教学上下文，等着渲染进 system prompt。"""

    #: The 项目集's name — what the course calls itself. None when the 赛题 has no
    #: category, which is a course-shaped config with nothing to name.
    course: str | None
    teaching: Teaching
    #: The 课件 an ordinary member may read and that still exist:
    #: `{id, name, url, type}`. A 课件 locked to「仅管理员」on every board that
    #: lists it is dropped before it gets here — see `for_project`.
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
            select(Task, SpaceCategory, Space)
            # outerjoin: a 赛题 whose 项目集 was deleted still carries its own
            # `protocol_override`, and that override is a teaching config too.
            # The 空间 joins on the 赛题's own `space_id`, not through the
            # 项目集, so a 赛题 still reaches its board's default with the
            # 项目集 gone.
            .outerjoin(SpaceCategory, SpaceCategory.id == Task.category_id)
            .outerjoin(Space, Space.id == Task.space_id)
            .where(Task.id == project.external_task_id)
        )
    ).first()
    if row is None:
        return None
    task, category, space = row
    teaching = resolve(
        space=space, category=category, task=task, project=project
    ).teaching
    if teaching.is_empty:
        return None
    # A 课件 locked to「仅管理员」on every board that lists it must not reach a
    # student's agent: the `url` this context carries is public. The check is on
    # the read side because a teacher may flip a 课件's tier *after* a config
    # already named it (`PATCH /spaces/{id}/materials/{mid}`), and nothing goes
    # back to revisit the config. Filter BEFORE the fetch, so a locked 课件 never
    # enters `TeachingContext.materials` at all.
    readable = await member_readable_material_ids(
        session, material_ids=teaching.material_ids
    )
    materials = await MaterialService.for_lookup(session).get_many(
        [mid for mid in teaching.material_ids if mid in readable]
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


async def count_material_references(
    session: AsyncSession, *, space_id: int
) -> dict[int, int]:
    """这块板上还有几处配置列着每一份课件：``{material_id: 处数}``。

    「资料库」那一页用它显示「被 N 处引用」/「未被引用」，好让老师删一份课件之前
    知道自己会留下几处指着空处的配置。

    **数的是「列着」，不是「生效」。** ``resolve`` 只看最里面那个非空层（题目覆盖
    压过项目集、项目集压过空间默认），这里三层各自照数 —— 一份课件可能被空间默认
    列着而板上每道题都被项目集盖住，那时它一处也不生效，但删了它，空间默认那个配置
    里就留下一个悬空 id。这正是老师要看见的事，所以两种口径都对，只是这一格答的是
    前者；数字的含义写在界面的那一句文案上，不要把它读成「会影响 N 道题」。

    数三层：空间自己的默认、板下每个项目集、板下每道题。**项目那一层不数**：
    ``Project.settings["teaching"]`` 今天全仓库没有一个写入口（``resolve`` 读得到
    它，但没人往里写），数它只会永远得 0，还要多扫一遍项目表。判据是「有没有人
    能写出这个引用」，不是「``resolve`` 会不会读它」—— 哪天有了写入口，这里补
    一条。集成测试里那条直接写 ``settings`` 的捷径（``test_teaching_chain.py``）
    是造场景用的，不是写入口。

    **软删掉的行不算**：撤下来的项目集、删掉的赛题，它们那一格 JSON 还留在库里，
    但老师在界面上看不到、也改不了。列着它们的那些 id 不该把这一格撑大 —— 数的是
    「你还能去收拾的配置」。（软删的赛题对已经建好的项目还生效，那是读侧
    ``for_project`` 的事：它不滤 ``deleted_at``。两条口径不同，各有各的用处。）

    **在内存里数，不写 SQL**：配置存在 JSON 列里，「这个数组里有 42」在
    PostgreSQL 上要 ``@>``（只有 JSONB 有）而 SQLite 退化成字符串 LIKE，同一条
    语句跨库不等价（sqlalchemy#12736）。捞出来的只是每处配置那一格 JSON，量级是
    「这块板有多少个分类和题目」，不随课件数增长。

    **已经撤下来的课件照数**：一处配置指着已经被删掉（或改成「仅管理员」）的课件
    时，那个 id 仍然列在那儿等老师收拾。读取时它不算数（见 ``for_project``），两处
    不矛盾。
    """
    counts: dict[int, int] = {}

    def tally(raw: object) -> None:
        for material_id in Teaching.from_json(raw).material_ids:
            counts[material_id] = counts.get(material_id, 0) + 1

    space = await session.get(Space, space_id)
    if space is not None:
        tally(getattr(space, "teaching", None))
    for (raw,) in (
        await session.execute(
            select(SpaceCategory.teaching).where(
                SpaceCategory.space_id == space_id,
                SpaceCategory.deleted_at.is_(None),
            )
        )
    ).all():
        tally(raw)
    for (raw,) in (
        await session.execute(
            select(Task.protocol_override).where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                Task.protocol_override.is_not(None),
            )
        )
    ).all():
        # 题目那一层是 `protocol_override` 这个 JSON 里的一个键，与 `resolve`
        # 读的是同一处（`_level_value` 的字典那一半）。
        tally((raw or {}).get("teaching") if isinstance(raw, dict) else None)
    return counts
