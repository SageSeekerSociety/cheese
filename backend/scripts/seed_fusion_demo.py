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

from app.core.db import async_session_factory
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectMember,
    ProjectRole,
)
from app.domain.team.models import Team  # noqa: F401 — register `team` for Project.team_id FK
from app.domain.topic.models import Topic, TopicKind, TopicStatus

PROJECT_NAME = "知是 2.0 融合演示"
OWNER = "alice"  # username == handle (fusion A2)
TEAM_ID = 1      # 知是 Team 「深度学习研究组」 (P4 native link)
CHEESE = "cheese"


async def seed() -> None:
    async with async_session_factory() as s:
        # Idempotent: drop any prior instance (cascades to topics/members).
        existing = (
            await s.execute(select(Project).where(Project.name == PROJECT_NAME))
        ).scalars().all()
        for p in existing:
            await s.execute(delete(Topic).where(Topic.project_id == p.id))
            await s.execute(
                delete(ProjectMember).where(ProjectMember.project_id == p.id)
            )
            await s.delete(p)
        await s.flush()

        project = Project(
            name=PROJECT_NAME,
            owner_handle=OWNER,
            team_id=TEAM_ID,
            ai_mode=AiMode.collaborative,
            summary="演示：把原版知是（空间/小队/任务）与芝士的话题/群聊/文档合到一处。",
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

        s.add(
            Topic(
                project_id=project.id,
                title="搭建第一个原型",
                kind=TopicKind.topic,
                status=TopicStatus.active,
                created_by=OWNER,
            )
        )
        s.add_all(
            [
                ProjectMember(
                    project_id=project.id, user_handle=OWNER, role=ProjectRole.lead
                ),
                ProjectMember(
                    project_id=project.id, user_handle=CHEESE, role=ProjectRole.member
                ),
            ]
        )
        await s.commit()
        print(f"seeded project {project.id} '{PROJECT_NAME}' owner={OWNER}")


if __name__ == "__main__":
    asyncio.run(seed())
