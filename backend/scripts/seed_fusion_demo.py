"""Minimal fusion-demo seed: one CheeseX project so the rail shows a project
tile you can click into.

It creates just what the rail/workspace demo needs, reusing the users that
already exist after a fresh `alembic upgrade head` (alice from the seed
migration, 芝士 from boot).

Idempotent: keyed on the project name, so re-running never duplicates.
"""

import asyncio

from sqlalchemy import delete, select

import app.models  # noqa: F401 — every table, so any FK on the ones below resolves
from app.core.db import async_session_factory
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.address import Numbered, take_number
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
# each linked to a real 知是 Team (P4). (name, team_id, first_topic)
DEMO_PROJECTS = [
    (
        "知是 2.0 融合演示",
        1,  # 深度学习研究组
        "搭建第一个原型",
    ),
    (
        "AI 系统实验室",
        2,  # 全栈开发小队
        "设计推理服务架构",
    ),
    (
        "数据分析平台",
        3,  # 数据分析兴趣组
        "梳理数据管线",
    ),
]


async def seed() -> None:
    async with async_session_factory() as s:
        for name, team_id, first_topic in DEMO_PROJECTS:
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
            )
            s.add(project)
            await s.flush()

            # Made directly, past TopicRepository.add, so each takes its
            # channel number here.
            root = Topic(
                project_id=project.id,
                number=await take_number(s, project.id, Numbered.channel),
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
                number=await take_number(s, project.id, Numbered.channel),
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

            # The channel was made directly, past TopicService, so its seats are
            # seeded here: its creator and 芝士. 综合 seats nobody by name —
            # everyone in the project is in it.
            await TopicMemberService(s).seed(work.id, owner_handle=OWNER)
            await provision_repository(project.id, s)
            print(f"seeded project '{name}' (team {team_id})")
        await s.commit()


if __name__ == "__main__":
    asyncio.run(seed())
