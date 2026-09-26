"""学习维度: 成员怎么与 AI 协作、卡在哪 (issue #945 的管理员看板 · 学习).

旁边五格读的是 赛题 与报名表，答的是「这门课组织得怎么样」；这一格读的是**成员
项目里的对话**，答的是「成员卡在哪，下一讲该讲什么」。两种数据，两种问题。

三件事，一件一个方法：

- ``filters``: 这门课里，看的人能看见哪些成员、哪些知识点。
- ``questions``: 按成员 / 时间 / 知识点筛出成员说过的话，每条都带得回原文的坐标。
- ``queues`` / ``outline``: 共性问题两个队列，以及从勾中的几条拼出来的讲解提纲。

**谁能看什么，不在这里判。** 每一个项目都得先过
``app.auth.project_access.may_read_project`` —— 同一条判据也在
``ActorResolver.authorize_project`` 上。能打开课程页不等于能读这门课下每一个成员
项目的对话，而「出题者能读自己那门课的产出」正是那份判据里已有的第四种主张。
列表处用布尔版（一个列表不能为第 40 个项目抛 403 就整体失败），取原文处用抛错版
（单条取，错了就该说出来）。两份判据同一处定义，不另写一份。

**这里没有的东西**（写清楚，免得下一个人以为漏了）：

- ``review_flag``: 全仓没有这一列、也没有这张表。芝士「这道题我答不了」的判断
  从来没有落库，所以 ``reviewFlag`` 那个队列只能报 ``available: false``，把缺什么
  写进返回里，不拿别的信号冒充它。
- 「再给一点提示」的点击: 同样没有落库 —— 没有这个按钮，也没有这个事件。
- 知识点: 没有教学单元，也没有题目维度的标签。今天能用的是**课程分类**
  (``space_categories``) —— 项目所从属的那道赛题挂在哪个分类下。那是课程设计者
  自己划的格子，够用，但它不是知识点。

私聊不进这一格: ``Topic.is_private`` 为真的房间是成员自己的对话，跟管理员看板无关
(issue #945: 「个人私聊和其他项目不会出现在导师对话视图中」)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_project
from app.domain.block.authorship import participant_blocks
from app.domain.block.models import Block, BlockKind
from app.domain.identity.handles import agent_handle_column
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.space.models import SpaceCategory
from app.domain.task.models import Task
from app.domain.topic.models import Topic
from app.domain.user.models import User, UserProfile

# 一条「提问」在界面上占多少字。摘要是给人扫一眼的，完整的原文点回去看。
QUOTE_LIMIT = 160
# 一次最多带回多少条。管理员看板是拿来挑几条讲的，不是拿来翻页的。
QUESTION_LIMIT = 300

#: 没有落库的那一维，连理由一起报出去 —— 空队列和一个坏掉的队列必须分得开。
REVIEW_FLAG_MISSING = (
    "平台没有记录这个信号：芝士判断「这道题我答不了」（超纲、资料没覆盖、"
    "课程规则冲突）时不会写下任何东西，库里既没有 review_flag 这一列，也没有"
    "对应的表。这一格等那个信号落库之后才会有内容，现在不拿别的数据顶替。"
)


class _Question:
    """一条能点回原文的成员发言，以及它落在哪个知识点上。"""

    __slots__ = (
        "block_id",
        "topic_id",
        "project_id",
        "project_name",
        "student",
        "student_name",
        "topic_title",
        "knowledge_point",
        "created_at",
        "quote",
    )

    def __init__(
        self,
        *,
        block_id: uuid.UUID,
        topic_id: uuid.UUID,
        project: Project,
        student_name: str,
        topic_title: str,
        knowledge_point: str | None,
        created_at: datetime,
        quote: str,
    ) -> None:
        self.block_id = block_id
        self.topic_id = topic_id
        self.project_id = project.id
        self.project_name = project.name
        self.student = project.owner_handle or ""
        self.student_name = student_name
        self.topic_title = topic_title
        self.knowledge_point = knowledge_point
        self.created_at = created_at
        self.quote = quote

    def to_api(self) -> dict[str, Any]:
        return {
            "blockId": str(self.block_id),
            "topicId": str(self.topic_id),
            "projectId": str(self.project_id),
            "projectName": self.project_name,
            "student": self.student,
            "studentName": self.student_name,
            "topicTitle": self.topic_title,
            "knowledgePoint": self.knowledge_point,
            "createdAt": int(self.created_at.timestamp() * 1000),
            "quote": self.quote,
        }


class SpaceLearningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 谁能看什么
    # ------------------------------------------------------------------

    async def _readable_project_ids(
        self, *, space_id: int, handle: str | None
    ) -> list[uuid.UUID]:
        """这门课下，看的人读得了的那些项目。

        课程 → 项目的路只有一条: 赛题挂在 space 上，项目记着它是从哪道赛题开的
        (``Project.external_task_id``)。走的是 ``list_ids_for_space_tasks`` ——
        同一条路机构看板已经在走。

        逐个项目过 ``may_read_project``: 课程页对所有人可见（``Role.GUEST`` 就能
        读 Space），成员项目的对话不是。一个都不许读时就返回空表，页面画空态。
        """
        ids = await ProjectRepository(self._session).list_ids_for_space_tasks(space_id)
        allowed: list[uuid.UUID] = []
        for project_id in ids:
            readable = await may_read_project(
                self._session, project_id=project_id, handle=handle
            )
            if readable:
                allowed.append(project_id)
        return allowed

    async def _load_projects(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Project]:
        if not project_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Project).where(Project.id.in_(project_ids))
            )
        ).scalars()
        return {p.id: p for p in rows}

    async def _knowledge_points(
        self, projects: list[Project]
    ) -> tuple[dict[uuid.UUID, int | None], dict[int, str]]:
        """每个项目挂在哪个知识点（分类）下，以及分类 id → 名字。

        知识点今天读的是项目所从属那道赛题的课程分类 —— 见模块说明里为什么没有
        更细的那一维。项目没挂赛题、或那道赛题没归类，就是 None（界面上是「未归类」）。

        按 **id** 回来而不是名字: 筛选那一维传的是 id，同名分类是允许的（分类名
        没有唯一约束），拿名字比会把两个格子并成一个。
        """
        task_ids = {
            p.external_task_id for p in projects if p.external_task_id is not None
        }
        categories_by_task: dict[int, int] = {}
        if task_ids:
            task_rows = (
                await self._session.execute(
                    select(Task.id, Task.category_id).where(Task.id.in_(task_ids))
                )
            ).all()
            categories_by_task = {row[0]: row[1] for row in task_rows}

        category_ids = {c for c in categories_by_task.values() if c is not None}
        names_by_category: dict[int, str] = {}
        if category_ids:
            cat_rows = (
                await self._session.execute(
                    select(SpaceCategory.id, SpaceCategory.name).where(
                        SpaceCategory.id.in_(category_ids)
                    )
                )
            ).all()
            names_by_category = {row[0]: row[1] for row in cat_rows}

        by_project: dict[uuid.UUID, int | None] = {}
        for project in projects:
            by_project[project.id] = (
                categories_by_task.get(project.external_task_id)
                if project.external_task_id is not None
                else None
            )
        return by_project, names_by_category

    async def _knowledge_point_options(self, space_id: int) -> list[dict[str, Any]]:
        """这门课可筛的知识点 —— 分类本身就是课程设计的格子，不限于已有项目的那些。"""
        rows = (
            await self._session.execute(
                select(SpaceCategory.id, SpaceCategory.name)
                .where(
                    SpaceCategory.space_id == space_id,
                    SpaceCategory.deleted_at.is_(None),
                )
                .order_by(SpaceCategory.name)
            )
        ).all()
        return [{"categoryId": row[0], "name": row[1]} for row in rows]

    # ------------------------------------------------------------------
    # 读对话
    # ------------------------------------------------------------------

    def _student_question_blocks(
        self,
        project_ids: list[uuid.UUID],
        *,
        from_dt: datetime | None,
        to_dt: datetime | None,
    ):
        """成员说的话: 参与者写的、而且不是芝士（或它的分身）说的。

        「是不是人说的」问的是署名而不是档位 —— ``AuthorType`` 早就只剩
        「参与者 / 平台自己」两档，人和 agent 都是 participant（见
        ``block/authorship.py``）。所以判据是 ``agent_handle_column`` 的否定，
        和「这句是不是芝士说的」同一个前缀，改命名规则两边一起改。
        """
        stmt = (
            select(Block)
            .join(Topic, Topic.id == Block.topic_id)
            .where(
                Block.project_id.in_(project_ids),
                Block.kind == BlockKind.message,
                participant_blocks(),
                ~agent_handle_column(Block.author),
                # 私聊不属于管理员看板。
                Topic.is_private.is_(False),
            )
            .order_by(Block.created_at.desc())
            .limit(QUESTION_LIMIT)
        )
        if from_dt is not None:
            stmt = stmt.where(Block.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(Block.created_at <= to_dt)
        return stmt

    async def _display_names(self, handles: set[str]) -> dict[str, str]:
        """handle → 给人看的名字。名字在 profile 上，不在 user 上。"""
        if not handles:
            return {}
        rows = (
            await self._session.execute(
                select(User.username, UserProfile.nickname)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .where(User.username.in_(handles))
            )
        ).all()
        # 没起昵称就退回 handle —— 空白比 handle 更难认。
        return {row[0]: (row[1] or row[0]) for row in rows}

    async def _collect_questions(
        self,
        projects: dict[uuid.UUID, Project],
        *,
        student: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        knowledge_point: int | None,
    ) -> list[_Question]:
        category_of_project, names_by_category = await self._knowledge_points(
            list(projects.values())
        )

        project_ids = [
            pid
            for pid, project in projects.items()
            if (student is None or (project.owner_handle or "") == student)
            and (
                knowledge_point is None
                or category_of_project.get(pid) == knowledge_point
            )
        ]
        if not project_ids:
            return []

        blocks = list(
            (
                await self._session.execute(
                    self._student_question_blocks(
                        project_ids, from_dt=from_dt, to_dt=to_dt
                    )
                )
            )
            .scalars()
            .all()
        )
        if not blocks:
            return []

        topic_ids = {b.topic_id for b in blocks}
        topic_rows = (
            await self._session.execute(
                select(Topic.id, Topic.title).where(Topic.id.in_(topic_ids))
            )
        ).all()
        titles_by_topic = {row[0]: row[1] for row in topic_rows}

        names = await self._display_names({b.author for b in blocks})

        result: list[_Question] = []
        for block in blocks:
            project = projects.get(block.project_id)
            if project is None:
                continue
            name = _knowledge_point_name(
                category_of_project, names_by_category, project.id
            )
            result.append(
                _Question(
                    block_id=block.id,
                    topic_id=block.topic_id,
                    project=project,
                    student_name=names.get(
                        project.owner_handle or "", project.owner_handle or ""
                    ),
                    topic_title=titles_by_topic.get(block.topic_id, ""),
                    knowledge_point=name,
                    created_at=block.created_at,
                    quote=_excerpt(block.content),
                )
            )
        return result

    # ------------------------------------------------------------------
    # 对外三件事
    # ------------------------------------------------------------------

    async def filters(self, *, space_id: int, handle: str | None) -> dict[str, Any]:
        """这门课里能筛的两维: 成员、知识点。时间是前端那一条，不在这里列。"""
        project_ids = await self._readable_project_ids(space_id=space_id, handle=handle)
        projects = await self._load_projects(project_ids)

        owners = sorted({p.owner_handle for p in projects.values() if p.owner_handle})
        names = await self._display_names(set(owners))

        return {
            "students": [{"handle": h, "name": names.get(h, h)} for h in owners],
            "knowledgePoints": await self._knowledge_point_options(space_id),
            "projectCount": len(projects),
        }

    async def questions(
        self,
        *,
        space_id: int,
        handle: str | None,
        student: str | None,
        from_ts: int | None,
        to_ts: int | None,
        knowledge_point: int | None,
    ) -> dict[str, Any]:
        """按成员 / 时间 / 知识点筛出来的成员发言，每条都能点回原文。"""
        project_ids = await self._readable_project_ids(space_id=space_id, handle=handle)
        projects = await self._load_projects(project_ids)
        items = await self._collect_questions(
            projects,
            student=student,
            from_dt=_from_ms(from_ts),
            to_dt=_to_ms(to_ts),
            knowledge_point=knowledge_point,
        )
        return {"questions": [q.to_api() for q in items], "total": len(items)}

    async def queues(
        self,
        *,
        space_id: int,
        handle: str | None,
        student: str | None,
        from_ts: int | None,
        to_ts: int | None,
    ) -> dict[str, Any]:
        """共性问题两条来源，各自一个队列。

        第一条 (`reviewFlag`) 没有数据源，如实报缺。第二条 (`stuckPoints`) 是统计
        出来的: 同一批成员发言按知识点归堆，数**有多少个不同的成员**在这堆里说过
        话 —— 「多少人卡在同一处」才是共性，一个人问十遍不是。

        每条都带一个 ``example``（一条真实的发言 + 它的房间坐标），所以整个队列
        里没有一行是点不回去的。
        """
        project_ids = await self._readable_project_ids(space_id=space_id, handle=handle)
        projects = await self._load_projects(project_ids)
        questions = await self._collect_questions(
            projects,
            student=student,
            from_dt=_from_ms(from_ts),
            to_dt=_to_ms(to_ts),
            knowledge_point=None,
        )

        grouped: dict[str, dict[str, Any]] = {}
        for question in questions:
            key = question.knowledge_point or ""
            bucket = grouped.get(key)
            if bucket is None:
                bucket = {
                    "knowledgePoint": question.knowledge_point,
                    "students": set(),
                    "projects": set(),
                    "questionCount": 0,
                    "latestAt": 0,
                    "example": question.to_api(),
                }
                grouped[key] = bucket
            bucket["students"].add(question.student)
            bucket["projects"].add(str(question.project_id))
            bucket["questionCount"] += 1
            # questions 是按 created_at 倒序来的，所以第一条就是最新的 —— 拿它当
            # 例子，管理员看到的是最近那个成员说的话。
            if question.created_at.timestamp() * 1000 > bucket["latestAt"]:
                bucket["latestAt"] = int(question.created_at.timestamp() * 1000)
                bucket["example"] = question.to_api()

        stuck_points = [
            {
                "knowledgePoint": bucket["knowledgePoint"],
                "studentCount": len(bucket["students"]),
                "projectCount": len(bucket["projects"]),
                "questionCount": bucket["questionCount"],
                "latestAt": bucket["latestAt"],
                "example": bucket["example"],
            }
            for bucket in grouped.values()
        ]
        # 先按「多少个成员撞上」排，再按问题条数 —— 一个成员问十遍不如十个成员各
        # 问一遍值得讲。
        stuck_points.sort(
            key=lambda item: (item["studentCount"], item["questionCount"]),
            reverse=True,
        )

        return {
            "reviewFlag": {
                "available": False,
                "reason": REVIEW_FLAG_MISSING,
                "items": [],
            },
            "stuckPoints": stuck_points,
        }

    async def outline(
        self,
        *,
        space_id: int,
        handle: str | None,
        block_ids: list[uuid.UUID],
    ) -> dict[str, Any]:
        """把勾中的几条拼成一份能直接上课用的提纲。

        只拼，不生成: 提纲里的每一句都是某个成员真的说过的话，加一条原文坐标，
        加它落在哪个知识点上，加它是第几讲。没有一句是新写的 —— 管理员要的是「拿
        这几条去讲」，不是「让 AI 替我写一节课」。

        ``block_ids`` 指回具体的消息，所以这里能**逐条**再判一次权限: 勾的时候
        能读，不等于递提纲的时候还能读（被移出项目、被撤权的管理员都走过这条路）。
        """
        if not block_ids:
            return {"title": "", "sections": [], "missing": []}

        blocks = list(
            (
                await self._session.execute(
                    select(Block).where(
                        Block.id.in_(block_ids),
                        Block.kind == BlockKind.message,
                        participant_blocks(),
                        ~agent_handle_column(Block.author),
                    )
                )
            )
            .scalars()
            .all()
        )
        blocks_by_id = {b.id: b for b in blocks}

        # 勾中的那些，逐条问一遍门 —— 这是取原文的地方，该拦就拦。许可名单算一次:
        # 一道提纲里几百条，每条都重走一遍课程→赛题→项目的路就该挨骂了。
        allowed_projects = set(
            await self._readable_project_ids(space_id=space_id, handle=handle)
        )
        readable: list[Block] = []
        missing: list[str] = []
        for block_id in block_ids:
            block = blocks_by_id.get(block_id)
            if block is None or block.project_id is None:
                missing.append(str(block_id))
                continue
            if block.project_id not in allowed_projects:
                missing.append(str(block_id))
                continue
            readable.append(block)

        if not readable:
            return {"title": "", "sections": [], "missing": missing}

        projects = await self._load_projects(list({b.project_id for b in readable}))
        category_of_project, names_by_category = await self._knowledge_points(
            list(projects.values())
        )
        topic_rows = (
            await self._session.execute(
                select(Topic.id, Topic.title).where(
                    Topic.id.in_({b.topic_id for b in readable})
                )
            )
        ).all()
        titles_by_topic = {row[0]: row[1] for row in topic_rows}
        names = await self._display_names(
            {(projects[b.project_id].owner_handle or "") for b in readable}
        )

        grouped: dict[str, list[Block]] = {}
        for block in readable:
            name = _knowledge_point_name(
                category_of_project, names_by_category, block.project_id
            )
            grouped.setdefault(name or "未归类", []).append(block)

        sections: list[dict[str, Any]] = []
        for index, (point, point_blocks) in enumerate(grouped.items(), start=1):
            excerpts = []
            for block in sorted(point_blocks, key=lambda b: b.created_at):
                project = projects[block.project_id]
                owner = project.owner_handle or ""
                excerpts.append(
                    {
                        "blockId": str(block.id),
                        "topicId": str(block.topic_id),
                        "projectId": str(block.project_id),
                        "student": owner,
                        "studentName": names.get(owner, owner),
                        "topicTitle": titles_by_topic.get(block.topic_id, ""),
                        "createdAt": int(block.created_at.timestamp() * 1000),
                        "quote": _excerpt(block.content),
                    }
                )
            sections.append(
                {
                    "knowledgePoint": point,
                    # 建议讲次: 一个知识点一讲，按「多少人撞上」排出来的顺序就是讲
                    # 的顺序。这是排序的结果，不是课表 —— 排第几由这门课自己定。
                    "session": index,
                    "excerpts": excerpts,
                }
            )

        space_name = await self._space_name(space_id)
        return {
            "title": f"{space_name} · 共性问题讲解提纲",
            "sections": sections,
            "missing": missing,
        }

    async def _space_name(self, space_id: int) -> str:
        from app.domain.space.models import Space

        name = await self._session.scalar(
            select(Space.name).where(Space.id == space_id)
        )
        return name or f"课程 {space_id}"


def _knowledge_point_name(
    category_of_project: dict[uuid.UUID, int | None],
    names_by_category: dict[int, str],
    project_id: uuid.UUID,
) -> str | None:
    """项目挂在的那个知识点的名字，没归类就是 None（界面上是「未归类」）。"""
    category_id = category_of_project.get(project_id)
    if category_id is None:
        return None
    return names_by_category.get(category_id)


def _excerpt(content: str) -> str:
    """一条发言的摘要。

    换行压成空格: 摘要是列表里的一行，原文点回去看，带换行的引用会把行高撑开而
    且看不出它在哪儿断的。超长截断，尾部加省略号，让人知道后面还有。
    """
    flat = " ".join(content.split())
    if len(flat) <= QUOTE_LIMIT:
        return flat
    return flat[:QUOTE_LIMIT] + "…"


def _from_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _to_ms(value: int | None) -> datetime | None:
    return _from_ms(value)
