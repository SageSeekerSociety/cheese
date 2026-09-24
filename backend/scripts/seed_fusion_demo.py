"""Minimal fusion-demo seed: one CheeseX project so the rail shows a project
tile you can click into.

The canonical scripts/seed_demo.py is written against cheesex's pre-merge User
model (flat handle/name); the merged app splits identity into main's User
(username) + UserProfile. Rather than port that whole script, this creates just
what the rail/workspace demo needs, reusing the users that already exist after a
fresh `alembic upgrade head` (alice from the seed migration, 芝士 from boot).

Idempotent: keyed on the project name, so re-running never duplicates.
"""

import asyncio

from sqlalchemy import delete, select

import app.models  # noqa: F401 — every table, so any FK on the ones below resolves
from app.core.db import async_session_factory
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.forge import provision_repository
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectMember,
)
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

OWNER = "alice"  # username == handle (fusion A2)
CHEESE = "cheese"

# A few demo projects so the rail shows multiple Discord-style tiles (⌘2/⌘3/…),
# each linked to a real 知是 Team (P4). (name, team_id, summary, first_topic)
DEMO_PROJECTS = [
    (
        "知是 2.0 融合演示",
        1,  # 深度学习研究组
        "演示：把原版知是（空间/小队/任务）与芝士的话题/群聊/文档合到一处。",
        "搭建第一个原型",
    ),
    (
        "AI 系统实验室",
        2,  # 全栈开发小队
        "AI 系统方向的课题工作台：推理服务、评测、Agent 编排。",
        "设计推理服务架构",
    ),
    (
        "数据分析平台",
        3,  # 数据分析兴趣组
        "数据分析课题：数据管线、可视化看板、指标体系。",
        "梳理数据管线",
    ),
]


async def seed() -> None:
    async with async_session_factory() as s:
        for name, team_id, summary, first_topic in DEMO_PROJECTS:
            # Idempotent: drop any prior instance (cascades to topics/members).
            existing = (
                (await s.execute(select(Project).where(Project.name == name)))
                .scalars()
                .all()
            )
            for p in existing:
                await s.execute(delete(Topic).where(Topic.project_id == p.id))
                await s.execute(
                    delete(ProjectMember).where(ProjectMember.project_id == p.id)
                )
                await s.delete(p)
            await s.flush()

            project = Project(
                name=name,
                owner_handle=OWNER,
                team_id=team_id,
                ai_mode=AiMode.collaborative,
                summary=summary,
            )
            s.add(project)
            await s.flush()

            root = Topic(
                project_id=project.id,
                title="项目总览 · 芝士本体",
                kind=TopicKind.root,
                status=TopicStatus.active,
                created_by=OWNER,
            )
            s.add(root)
            await s.flush()
            project.root_topic_id = root.id
            # 这个项目的芝士：实例行、它的身份、以及它在总览里的席位。走的是
            # `ProjectService.create` 用的同一个播种函数——这个脚本绕开了
            # ProjectService，不在这里播的话 demo 项目建出来就是「没有实例行、
            # 指针为 NULL、总览上一个 agent 席位都没有」，正是这条路要消灭的状态。
            await AgentInstanceService(s).materialize_default(project)

            work = Topic(
                project_id=project.id,
                title=first_topic,
                kind=TopicKind.topic,
                status=TopicStatus.active,
                created_by=OWNER,
            )
            s.add(work)
            # 芝士's seat on the roster. The owner needs no row: owner_handle puts
            # them on it, and the project's team brings everyone else.
            s.add(ProjectMember(project_id=project.id, user_handle=CHEESE))
            await s.flush()

            # Seed topic rosters (这些 Topic 是直接建的，绕过了 TopicService，
            # 所以名册要在这里补种). 总览 = 项目本体 → 全体项目成员；
            # 工作话题 → 创建者(owner) + 芝士. Idempotent via _ensure_member.
            members = TopicMemberService(s)
            await members.seed_root(
                root.id, owner_handle=OWNER, member_handles=[OWNER, CHEESE]
            )
            await members.seed(work.id, owner_handle=OWNER)
            await provision_repository(project.id, s)
            print(f"seeded project '{name}' (team {team_id})")
        await s.commit()


if __name__ == "__main__":
    asyncio.run(seed())
