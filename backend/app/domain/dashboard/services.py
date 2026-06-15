"""Aggregation services — the attention-management layer (spec §7.2/§7.3).

These read across domains to produce the "one level up" summaries:
- project overview (事维度): milestones, topics×status, 等你处理的事, risks (eval G2)
- Space board (机构): every linked team in one table (eval F3)

Read-only; the data is owned by the per-domain tables and stays the source of
truth.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.membership.repositories import MemberRepository
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.space.repositories import SpaceRepository
from app.domain.task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.topic.models import TopicStatus
from app.domain.topic.repositories import TopicRepository


class DashboardService:
    def __init__(self, session: AsyncSession):
        self._s = session
        self._projects = ProjectRepository(session)
        self._topics = TopicRepository(session)
        self._milestones = MilestoneRepository(session)
        self._notifs = NotificationRepository(session)
        self._members = MemberRepository(session)
        self._spaces = SpaceRepository(session)
        self._templates = TaskTemplateRepository(session)
        self._tasks = TaskRepository(session)

    async def _project_card(self, project_id: uuid.UUID) -> dict | None:
        project = await self._projects.get(project_id)
        if project is None:
            return None
        topics = await self._topics.list_for_project(project_id)
        by_status = {s.value: 0 for s in TopicStatus}
        for t in topics:
            by_status[t.status.value] += 1
        upcoming = await self._milestones.list_calendar(project_id)
        return {
            "project_id": str(project.id),
            "name": project.name,
            "ai_mode": project.ai_mode.value,
            "owner_handle": project.owner_handle,
            "summary": project.summary,
            "topic_count": len(topics),
            "topics_by_status": by_status,
            "upcoming_milestones": [
                {
                    "title": m.title,
                    "due_date": m.due_date.isoformat() if m.due_date else None,
                }
                for m in upcoming
            ],
            "next_milestone": (
                {
                    "title": upcoming[0].title,
                    "due_date": (
                        upcoming[0].due_date.isoformat()
                        if upcoming[0].due_date
                        else None
                    ),
                }
                if upcoming
                else None
            ),
        }

    async def project_overview(self, project_id: uuid.UUID) -> dict:
        """事维度总览 (eval G2): milestones, topics×status, 等你处理的事."""
        card = await self._project_card(project_id)
        if card is None:
            raise NotFoundError("Project not found")
        members = await self._members.list_for_project(project_id)
        # 等你处理的事: decision/accept requests still unread, grouped by person.
        inbox = await self._notifs.list_inbox(project_id, target_handle=None)
        todo_by_person: dict[str, list[dict]] = {}
        for n in inbox:
            handle = n.target_handle or "未分派"
            todo_by_person.setdefault(handle, []).append(
                {"id": str(n.id), "title": n.title, "kind": n.kind.value}
            )
        return {
            **card,
            "members": [
                {"handle": m.user_handle, "role": m.role.value} for m in members
            ],
            "waiting_on_you": todo_by_person,
        }

    async def member_summary(self, project_id: uuid.UUID, user_handle: str) -> dict:
        """成员页 (spec §7.2): one member's slice — topics they started, what's
        waiting on them, their role. Doubles as the portfolio source."""
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        members = await self._members.list_for_project(project_id)
        member = next((m for m in members if m.user_handle == user_handle), None)
        topics = await self._topics.list_for_project(project_id)
        started = [
            {"id": str(t.id), "title": t.title, "status": t.status.value}
            for t in topics
            if t.created_by == user_handle
        ]
        inbox = await self._notifs.list_inbox(project_id, target_handle=user_handle)
        return {
            "handle": user_handle,
            "role": member.role.value if member else None,
            "topics_started": started,
            "waiting_on_you": [
                {"id": str(n.id), "title": n.title, "kind": n.kind.value} for n in inbox
            ],
        }

    async def user_profile(self, handle: str) -> dict:
        """个人主页 (spec §7.2, LinkedIn/GitHub profile): cross-project — who
        they are, what they're on across projects, and 芝士's understanding of
        them (个人记忆, §8.4). This is the "项目过程即简历" view."""
        from sqlalchemy import func, select

        from app.domain.block.models import Block
        from app.domain.memory.models import MemoryScope
        from app.domain.memory.store import DbMemoryStore
        from app.domain.project.models import Project, ProjectMember
        from app.domain.topic.models import Topic
        from app.domain.user.repositories import UserRepository

        user = await UserRepository(self._s).get_by_handle(handle)

        # Cross-project memberships + role + how much they started/contributed.
        rows = (
            await self._s.execute(
                select(ProjectMember, Project)
                .join(Project, Project.id == ProjectMember.project_id)
                .where(ProjectMember.user_handle == handle)
            )
        ).all()
        projects = []
        for member, project in rows:
            started = (
                await self._s.scalar(
                    select(func.count())
                    .select_from(Topic)
                    .where(
                        Topic.project_id == project.id,
                        Topic.created_by == handle,
                    )
                )
            ) or 0
            blocks = (
                await self._s.scalar(
                    select(func.count())
                    .select_from(Block)
                    .where(Block.project_id == project.id, Block.author == handle)
                )
            ) or 0
            projects.append(
                {
                    "project_id": str(project.id),
                    "name": project.name,
                    "role": member.role.value,
                    "topics_started": int(started),
                    "contributions": int(blocks),
                }
            )

        understanding = await DbMemoryStore(self._s).recall(MemoryScope.user, handle)
        return {
            "handle": handle,
            "name": user.name if user else handle,
            "bio": user.bio if user else "",
            "interests": user.interests if user else [],
            "skills": user.skills if user else [],
            "projects": projects,
            "understanding": understanding,  # 芝士 对 TA 的理解 (§8.4)
        }

    async def contributions(self, project_id: uuid.UUID) -> dict:
        """贡献统计 (spec §10.1): human vs AI, and per author. Source for the
        contribution graph + the trust signal that 人 directed the AI."""
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        from sqlalchemy import func, select

        from app.domain.block.models import Block

        rows = (
            await self._s.execute(
                select(Block.author_type, Block.author, func.count())
                .where(Block.project_id == project_id)
                .group_by(Block.author_type, Block.author)
            )
        ).all()
        by_type: dict[str, int] = {"human": 0, "ai": 0, "system": 0}
        by_author: dict[str, int] = {}
        for author_type, author, count in rows:
            by_type[author_type.value] = by_type.get(author_type.value, 0) + count
            by_author[author] = by_author.get(author, 0) + count
        return {"by_author_type": by_type, "by_author": by_author}

    async def space_board(self, space_id: uuid.UUID) -> dict:
        """机构看板 (eval F3): every team that linked a Task under this Space."""
        space = await self._spaces.get(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        # Space → templates → tasks → linked projects (deduped).
        project_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for template in await self._templates.list_for_space(space_id):
            for task in await self._tasks.list_for_template(template.id):
                for link in await self._projects.list_projects_for_task(task.id):
                    if link.project_id not in seen:
                        seen.add(link.project_id)
                        project_ids.append(link.project_id)
        cards = []
        for pid in project_ids:
            card = await self._project_card(pid)
            if card is not None:
                cards.append(card)
        return {
            "space_id": str(space_id),
            "name": space.name,
            "teams": cards,
            "total": len(cards),
        }
