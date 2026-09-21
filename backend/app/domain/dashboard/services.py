"""Aggregation services — the attention-management layer (spec §7.2/§7.3).

These read across domains to produce the "one level up" summaries:
- project overview (事维度): milestones, topics×status, 等你处理的事, risks (eval G2)
- Space board (机构): every linked team in one table (eval F3)

Read-only; the data is owned by the per-domain tables and stays the source of
truth.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.block.authorship import is_participant, participant_blocks
from app.domain.block.models import Block
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.membership.repositories import MemberRepository
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.notification.models import NotificationType
from app.domain.notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.space.repositories import SpaceRepository
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

    async def _project_card(self, project_id: uuid.UUID) -> dict | None:
        project = await self._projects.get(project_id)
        if project is None:
            return None
        topics = await self._topics.list_for_project(project_id)
        by_status = {s.value: 0 for s in TopicStatus}
        for t in topics:
            by_status[t.status.value] += 1
        upcoming = await self._milestones.list_calendar(project_id)
        # 活跃度 (spec §7.2): last activity + the human/AI contribution mix.
        last_activity = await self._s.scalar(
            select(func.max(Block.created_at)).where(Block.project_id == project_id)
        )
        # 和 `contributions()` 同一个读法：「人写了多少、AI 写了多少」读署名。事件
        # 行的档位只分得出参与者和平台，而这张活跃度问的正是参与者里的哪一种 ——
        # 按档位分组的话，平台事件被滤掉之后剩下的全是同一个 participant 档，两个
        # 数字都归零。
        mix = {"human": 0, "ai": 0}
        mix_rows = (
            await self._s.execute(
                select(Block.author, func.count())
                .where(Block.project_id == project_id, participant_blocks())
                .group_by(Block.author)
            )
        ).all()
        for author, count in mix_rows:
            mix["ai" if looks_like_agent_handle(author) else "human"] += count
        return {
            "project_id": str(project.id),
            "name": project.name,
            "ai_mode": project.ai_mode.value,
            "owner_handle": project.owner_handle,
            "summary": project.summary,
            "topic_count": len(topics),
            "topics_by_status": by_status,
            "last_activity_at": (last_activity.isoformat() if last_activity else None),
            "contributions": mix,
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

    async def member_summary(
        self, project_id: uuid.UUID, user_handle: str, *, viewer: str
    ) -> dict:
        """成员页 (spec §7.2): one member's slice — topics they started, what's
        waiting on them, their role. Doubles as the portfolio source.

        The public half (topics, contributions, role) is the same for everyone;
        ``waiting_on_you`` is one person's mailbox, so only that person gets it
        — on anybody else's page it is empty."""
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
        # 在忙哪些话题 (spec §7.2): active topics the member has contributed to.
        worked_topic_ids = set(
            (
                await self._s.execute(
                    select(Block.topic_id)
                    .where(
                        Block.project_id == project_id,
                        Block.author == user_handle,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        topics_active = [
            {"id": str(t.id), "title": t.title, "status": t.status.value}
            for t in topics
            if t.status == TopicStatus.active and t.id in worked_topic_ids
        ]
        # 本周贡献 (spec §7.2/§10.1): 这个人自己写下的块，最近 7 天。
        # `author` 已经把人挑出来了，这一条挡的是顶着他 handle 的平台事件。
        week_ago = datetime.now(UTC) - timedelta(days=7)
        weekly = (
            await self._s.scalar(
                select(func.count())
                .select_from(Block)
                .where(
                    Block.project_id == project_id,
                    Block.author == user_handle,
                    participant_blocks(),
                    Block.created_at >= week_ago,
                )
            )
        ) or 0
        # 收件箱是**这个成员自己**的，所以只有他本人打得开；别人的页面上是空的。
        # 以前广播是一行谁都看得见的记录，于是别人的页面上还剩「也在等他」的那一
        # 档可以交集；广播现在在写入时就展开成一人一行，没有可交的东西了。
        inbox = (
            await self._notifs.list_inbox(project_id, recipient_handle=user_handle)
            if viewer == user_handle
            else []
        )
        return {
            "handle": user_handle,
            "role": member.role.value if member else None,
            "topics_started": started,
            "topics_active": topics_active,
            "weekly_contributions": int(weekly),
            "waiting_on_you": [
                {
                    "id": str(n.id),
                    "title": n.title,
                    "kind": NotificationType(n.type).value,
                }
                for n in inbox
            ],
        }

    async def user_profile(self, handle: str) -> dict:
        """个人主页 (spec §7.2, LinkedIn/GitHub profile): cross-project — who
        they are, what they're on across projects, and 芝士's understanding of
        them (个人记忆, §8.4). This is the "项目过程即简历" view."""
        from app.domain.memory.models import (
            MemoryEntry,
            MemoryScope,
            user_scope_about,
        )
        from app.domain.memory.store import live_entries
        from app.domain.project.models import Project, ProjectMember
        from app.domain.topic.models import Topic
        from app.domain.user.repositories import UserProfileRepository, UserRepository

        user = await UserRepository(self._s).get_by_handle(handle)
        profile = (
            await UserProfileRepository(self._s).get_profile_by_user_id(user.id)
            if user
            else None
        )

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
                        # Don't count the private 1:1 chat as a started topic.
                        Topic.is_private.is_(False),
                    )
                )
            ) or 0
            # Contributions = the member's own blocks — not the platform's
            # lifecycle/event blocks that happen to carry their handle.
            blocks = (
                await self._s.scalar(
                    select(func.count())
                    .select_from(Block)
                    .where(
                        Block.project_id == project.id,
                        Block.author == handle,
                        participant_blocks(),
                    )
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

        # 关于他的记忆已经不是一个跨项目的池了：每个项目里的每位芝士各有一份自己
        # 的看法（结论 8），键是 `<项目>:<agent>:<他>`。这一页问的却正好是那个没有
        # 项目的问题——「大家对我的认识」——所以按后缀把每一份都收进来，而不是拼
        # 一个不存在的全局键。收进来的是哪一位芝士记的，`scope_id` 自己说得出。
        understanding = [
            row.content
            for row in (
                await self._s.scalars(
                    select(MemoryEntry)
                    .where(
                        MemoryEntry.scope == MemoryScope.user,
                        MemoryEntry.scope_id.endswith(
                            user_scope_about(handle), autoescape=True
                        ),
                        live_entries(),
                    )
                    .order_by(MemoryEntry.created_at.desc())
                    .limit(50)
                )
            ).all()
        ][::-1]
        return {
            "handle": handle,
            # Merged schema: display name is UserProfile.nickname, bio is
            # UserProfile.intro. There are no interests/skills columns.
            "name": profile.nickname if profile else handle,
            "bio": profile.intro if profile else "",
            "interests": [],
            "skills": [],
            "projects": projects,
            "understanding": understanding,  # 芝士 对 TA 的理解 (§8.4)
        }

    async def contributions(self, project_id: uuid.UUID) -> dict:
        """贡献统计 (spec §10.1): human vs AI, and per author. Source for the
        contribution graph + the trust signal that 人 directed the AI."""
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        rows = (
            await self._s.execute(
                select(Block.author_type, Block.author, func.count())
                .where(Block.project_id == project_id)
                .group_by(Block.author_type, Block.author)
            )
        ).all()
        # 「人写了多少、AI 写了多少」读署名，不读事件行的档位：档位只分得出参与者
        # 和平台，而这张图问的正是参与者里的哪一种。
        by_type: dict[str, int] = {"human": 0, "ai": 0, "system": 0}
        by_author: dict[str, int] = {}
        for author_type, author, count in rows:
            # by_author = real contributors; platform lifecycle blocks don't count.
            if not is_participant(author_type):
                by_type["system"] += count
                continue
            by_type["ai" if looks_like_agent_handle(author) else "human"] += count
            by_author[author] = by_author.get(author, 0) + count
        return {"by_author_type": by_type, "by_author": by_author}

    async def space_board(self, space_id: int) -> dict:
        """机构看板 (eval F3): every team that linked a Task under this Space."""
        space = await self._spaces.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        # Space → its 赛题 → the projects created from them (#370). This used to
        # walk Space → cheesex templates → cheesex tasks → project_task_links, a
        # hierarchy parallel to the one the 赛题 already form.
        cards = []
        for pid in await self._projects.list_ids_for_space_tasks(space_id):
            card = await self._project_card(pid)
            if card is not None:
                cards.append(card)
        return {
            "space_id": str(space_id),
            "name": space.name,
            "teams": cards,
            "total": len(cards),
        }
