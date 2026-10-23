"""读现状，顺带决定要不要在后台重写。

读的人从不等模型：先拿到上一次写下的；该重写时由读的这一次领下来，交给后台去写，
写完告诉开着这个项目的页面。

什么时候该重写（2026-10-16 定）：

- 从没写过：有人打开就写。
- 上次之后有大的进展（任务开了、做完了、交付被采纳了、项目目标改了）：离上次动笔
  满 ``MAJOR_GAP`` 就重写。刚做完一件事回来看，现状还说「进行中」，这段话就不可信了。
- 只是频道里有人说话：离上次动笔满 ``MINOR_GAP`` 才重写。活跃的项目时时有人说话，
  每说一句都重写，等于每次打开都重算。

没人打开就不写，没人看的项目不花钱。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import background
from app.domain import project_material
from app.domain.agent.project_feed import SITUATION, tell_project
from app.domain.project_situation import writer
from app.domain.project_situation.models import ProjectSituation
from app.domain.topic.models import Topic, TopicKind

logger = logging.getLogger(__name__)

MAJOR_GAP = timedelta(minutes=5)
MINOR_GAP = timedelta(hours=1)
#: 领了以后这么久还没写完，就当那个后端没了，别人可以再领。
CLAIM = timedelta(minutes=10)


@dataclass(frozen=True)
class Situation:
    #: 上一次写下的；还没写成过、或者项目里还没有可写的事，是空串。
    content: str
    written_at: datetime | None
    #: 后台正在重写。
    writing: bool


def _gap(
    row: ProjectSituation | None, marks: project_material.Marks
) -> timedelta | None:
    """离上次动笔至少隔多久才该重写；不该重写是 None。"""
    if row is None or row.written_at is None:
        return MAJOR_GAP
    if row.major != marks.major:
        return MAJOR_GAP
    if row.minor != marks.minor:
        return MINOR_GAP
    return None


def _writing(row: ProjectSituation | None, now: datetime) -> bool:
    return bool(row and row.writing_since and now - row.writing_since < CLAIM)


async def read(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    models: writer.ProjectModels,
    now: datetime | None = None,
) -> Situation:
    now = now or datetime.now(UTC)
    row = await session.get(ProjectSituation, project_id)
    marks = await project_material.marks(session, project_id)
    writing = _writing(row, now)
    gap = _gap(row, marks)
    if gap is not None and not writing:
        engine = _engine_of(session)
        if await _claim(engine, project_id, now=now, gap=gap):
            writing = True
            background.spawn(
                _rewrite(engine, project_id, models), name="project situation"
            )
    if row is None:
        return Situation(content="", written_at=None, writing=writing)
    return Situation(content=row.content, written_at=row.written_at, writing=writing)


def _engine_of(session: AsyncSession) -> AsyncEngine:
    bind = session.bind
    return bind if isinstance(bind, AsyncEngine) else bind.engine


async def _claim(
    engine: AsyncEngine, project_id: uuid.UUID, *, now: datetime, gap: timedelta
) -> bool:
    """领下这一次重写；别的后端正在写、或者离上次动笔还不够久，领不到。

    在自己的会话里提交：领没领到要让别的后端马上看得见，不能等读的那个请求结束。
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(
            insert(ProjectSituation)
            .values(project_id=project_id)
            .on_conflict_do_nothing(index_elements=[ProjectSituation.project_id])
        )
        claimed = await session.scalar(
            update(ProjectSituation)
            .where(
                ProjectSituation.project_id == project_id,
                or_(
                    ProjectSituation.writing_since.is_(None),
                    ProjectSituation.writing_since < now - CLAIM,
                ),
                or_(
                    ProjectSituation.tried_at.is_(None),
                    ProjectSituation.tried_at <= now - gap,
                ),
            )
            .values(writing_since=now, tried_at=now)
            .returning(ProjectSituation.project_id)
        )
        await session.commit()
    return claimed is not None


async def _rewrite(
    engine: AsyncEngine, project_id: uuid.UUID, models: writer.ProjectModels
) -> None:
    written: writer.Written | None = None
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            # 先记下这一刻的进展，再读材料：读的时候又来的进展，下次读会再算一遍。
            marks = await project_material.marks(session, project_id)
            material = await project_material.gather(session, project_id)
            if material.quiet:
                # 近两周什么都没发生：没东西可写，不问模型，也不留上一次的旧话。
                written = writer.Written(content="", model="")
            else:
                written = await writer.write(
                    session,
                    project_id=project_id,
                    material=material.text,
                    models=models,
                )
        except Exception:  # the last one written stands
            logger.warning(
                "project situation failed project=%s", project_id, exc_info=True
            )
            await session.rollback()
        values: dict = {"writing_since": None}
        if written is not None:
            values |= {
                "content": written.content,
                "written_at": datetime.now(UTC),
                "major": marks.major,
                "minor": marks.minor,
                "model": written.model,
            }
        await session.execute(
            update(ProjectSituation)
            .where(ProjectSituation.project_id == project_id)
            .values(**values)
        )
        await session.commit()
        root = await session.scalar(
            select(Topic.id).where(
                Topic.project_id == project_id, Topic.kind == TopicKind.root
            )
        )
    if root is not None:
        await tell_project(root, SITUATION, project_id=project_id)
