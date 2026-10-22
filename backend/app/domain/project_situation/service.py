"""读现状，顺带决定要不要在后台重写。

读的人从不等模型：先拿到上一次写下的；该重写时由读的这一次领下来，交给后台去写，
写完告诉开着这个项目的页面。

什么时候该重写（2026-10-16 定）：

- 从没写过：有人打开就写。
- 上次之后有新进展（任务开了、做完了、交付被采纳了、项目目标改了）：离上次动笔满
  ``GAP`` 就重写。刚做完一件事回来看，现状还说「进行中」，这段话就不可信了。

频道里有人说话不算进展：说的话不是现状的材料（``records``）。没人打开就不写，没人
看的项目不花钱。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import background
from app.domain.agent.project_feed import SITUATION, tell_project
from app.domain.project_situation import card, records, writer
from app.domain.project_situation.models import ProjectSituation
from app.domain.topic.models import Topic, TopicKind

logger = logging.getLogger(__name__)

GAP = timedelta(minutes=5)
#: 领了以后这么久还没写完，就当那个后端没了，别人可以再领。
CLAIM = timedelta(minutes=10)


@dataclass(frozen=True)
class Situation:
    #: 上一次写下的那几块；还没写成过、或者项目里还没有可写的事，是空的。
    parts: list
    written_at: datetime | None
    #: 后台正在重写。
    writing: bool


def _due(row: ProjectSituation | None, mark: str) -> bool:
    """从没写成过，或者上次之后有了新进展。"""
    return row is None or row.written_at is None or row.major != mark


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
    mark = await records.mark(session, project_id)
    writing = _writing(row, now)
    if _due(row, mark) and not writing:
        engine = _engine_of(session)
        if await _claim(engine, project_id, now=now):
            writing = True
            background.spawn(
                _rewrite(engine, project_id, models), name="project situation"
            )
    if row is None:
        return Situation(parts=[], written_at=None, writing=writing)
    return Situation(parts=row.parts, written_at=row.written_at, writing=writing)


def _engine_of(session: AsyncSession) -> AsyncEngine:
    bind = session.bind
    return bind if isinstance(bind, AsyncEngine) else bind.engine


async def _claim(engine: AsyncEngine, project_id: uuid.UUID, *, now: datetime) -> bool:
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
                    ProjectSituation.tried_at <= now - GAP,
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
    values: dict = {"writing_since": None}
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            # 先记下这一刻的进展，再读记录：读的时候又来的进展，下次读会再算一遍。
            mark = await records.mark(session, project_id)
            found = await records.gather(session, project_id)
            written: writer.Written | None = None
            if found.quiet:
                # 近两周什么都没发生：没东西可写，不问模型，也不留上一次的旧话。
                written = writer.Written(answer={}, model="")
            else:
                written = await writer.write(
                    session,
                    project_id=project_id,
                    material=found.text(),
                    models=models,
                )
            if written is not None:
                values |= {
                    "parts": card.build(written.answer, found),
                    "written_at": datetime.now(UTC),
                    "major": mark,
                    "model": written.model,
                }
        except Exception:  # the last one written stands
            logger.warning(
                "project situation failed project=%s", project_id, exc_info=True
            )
            await session.rollback()
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
