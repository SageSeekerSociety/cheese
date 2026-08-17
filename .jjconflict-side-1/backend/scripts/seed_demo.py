"""Seed realistic demo scenarios into the database (造场景).

Maps to docs/evals.md so the running app has content to click through:
- a 书院 Space + Task Template + Task (§4, eval F)
- a memory-rich Project (so @芝士 can answer with memory, eval C1)
- members (组长/成员/导师), topics with conversation blocks + a doc, milestones,
  a notification (decision request) and an accept card (eval C5/G2).

Run (Postgres must be up): cd backend && PYTHONPATH=. uv run python scripts/seed_demo.py
It TRUNCATEs all tables first (dev/demo DB only) then inserts a fresh scenario.


⚠️ This script does not currently run, and not because of anything below: it
imports ``app.domain.cx_space``, a module the fusion merge retired (one Space =
知是's int Space). ``scripts/seed_fusion_demo.py`` says the same thing from the
other side — "the canonical scripts/seed_demo.py is written against cheesex's
pre-merge User". It is kept because ``scripts/preview_sqlite.py`` imports it and
someone may yet port it. The 赛题 half WAS updated (#370) so it stops naming a
domain that no longer exists — fixing one breakage while leaving the older one
loudly labelled, rather than quietly leaving two.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.domain.cx_space.models import Space, SpaceKind
from sqlalchemy import text

from app.core.db import async_session_factory
from app.domain.alert.models import Alert, AlertKind, AlertLevel
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.memory.models import MemoryEntry, MemoryScope
from app.domain.milestone.models import Milestone, MilestoneStatus
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectMember,
    ProjectRole,
)
from app.domain.review.models import AcceptCard
from app.domain.space.models import SpaceCategory
from app.domain.task.models import Task
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.user.models import User

_TABLES = [
    "accept_cards",
    "notifications",
    "milestones",
    "blocks",
    "topics",
    "project_members",
    "project_task_links",
    "projects",
    "tasks",
    "task_templates",
    "spaces",
    "memory_entries",
    "expert_roles",
    "users",
]


async def _reset(session) -> None:
    await session.execute(
        text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
    )


async def seed() -> None:
    now = datetime.now(UTC)
    async with async_session_factory() as s:
        await _reset(s)

        # --- People ---
        lead = User(
            handle="user-1",
            name="林组长",
            bio="计算机系大三，擅长后端",
            interests=["推荐系统", "后端"],
            skills=["Python", "FastAPI"],
        )
        member = User(
            handle="user-2",
            name="王同学",
            bio="设计与前端",
            interests=["前端", "交互"],
            skills=["Vue", "Figma"],
        )
        mentor = User(handle="mentor-1", name="张老师", bio="信院导师")
        s.add_all([lead, member, mentor])

        # --- Space + Task Template + Task (§4) ---
        space = Space(
            name="明理书院",
            kind=SpaceKind.college,
            description="书院创新项目入驻",
        )
        s.add(space)
        await s.flush()

        # 项目集 carries the 机构协议 (#370): one set of terms for every 赛题
        # under it, rather than a copy per 赛题.
        category = SpaceCategory(
            space_id=space.id,
            name="创新项目入驻 2026 秋",
            description="书院创新项目，定期里程碑汇报",
            display_order=0,
            resource_pack={"compute": "1 GPU", "compute_credits": 5000},
            conditions=[
                {"required_topic": "中期汇报", "reviewer_role": "mentor"},
                {"required_topic": "结题答辩", "reviewer_role": "mentor"},
            ],
            default_role="academic-research",
            created_at=now,
            updated_at=now,
        )
        s.add(category)
        await s.flush()

        task = Task(
            name="用 AI 做课程推荐系统",
            intro="为校内学生做一个选课推荐系统",
            description="为校内学生做一个选课推荐系统",
            creator_id=1,
            space_id=space.id,
            category_id=category.id,
            submitter_type=0,
            approved=0,
            default_deadline=0,
            created_at=now,
            updated_at=now,
        )
        s.add(task)
        await s.flush()

        # --- Project (= root topic = repo), collaborative mode ---
        # Created FROM the 赛题, which is how a project accepts its 项目集's
        # protocol and shows up on the Space board (#370).
        project = Project(
            name="AI 课程推荐系统",
            owner_handle="user-1",
            ai_mode=AiMode.collaborative,
            expert_role="academic-research",
            external_task_id=task.id,
        )
        s.add(project)
        await s.flush()

        s.add_all(
            [
                ProjectMember(
                    project_id=project.id,
                    user_handle="user-1",
                    role=ProjectRole.lead,
                ),
                ProjectMember(
                    project_id=project.id,
                    user_handle="user-2",
                    role=ProjectRole.member,
                ),
                ProjectMember(
                    project_id=project.id,
                    user_handle="mentor-1",
                    role=ProjectRole.mentor,
                ),
            ]
        )

        # --- Project memory (so @芝士 answers with memory, eval C1) ---
        facts = [
            "本项目技术栈：后端 FastAPI + PostgreSQL，前端 Vue 3。",
            "决策：推荐算法先用协同过滤（item-based）做 MVP，下一阶段再试深度模型。",
            "分工：林组长负责后端与算法，王同学负责前端与交互，张老师是导师。",
            "中期汇报定在 2026-06-20，需导师验收。",
            "数据来源：教务处脱敏的历史选课数据，已签数据使用协议。",
        ]
        for f in facts:
            s.add(
                MemoryEntry(
                    scope=MemoryScope.project,
                    scope_id=str(project.id),
                    content=f,
                )
            )

        # --- Root topic (总览) ---
        root = Topic(
            project_id=project.id,
            title="AI 课程推荐系统 · 项目总览",
            kind=TopicKind.root,
            status=TopicStatus.active,
            created_by="user-1",
        )
        s.add(root)
        await s.flush()
        project.root_topic_id = root.id

        # --- Work topic with a conversation + a doc ---
        topic = Topic(
            project_id=project.id,
            parent_id=root.id,
            title="搭建推荐算法原型",
            kind=TopicKind.topic,
            status=TopicStatus.active,
            created_by="user-1",
        )
        s.add(topic)
        await s.flush()

        # Conversation (append-only history) + a doc block (state).
        opening = Block(
            project_id=project.id,
            topic_id=topic.id,
            kind=BlockKind.message,
            author_type=AuthorType.ai,
            author="cheese",
            content=(
                "收到。这个话题我来搭一个 item-based 协同过滤的推荐原型，"
                "先用教务处脱敏数据跑通离线评测。下一步：准备数据 → 实现算法 → 出评测。"
            ),
        )
        s.add(opening)
        await s.flush()
        q = Block(
            project_id=project.id,
            topic_id=topic.id,
            kind=BlockKind.message,
            author_type=AuthorType.human,
            author="user-1",
            content="先用最简单的协同过滤，别上深度模型。评测指标用 Recall@10。",
        )
        s.add(q)
        await s.flush()
        s.add(
            Block(
                project_id=project.id,
                topic_id=topic.id,
                kind=BlockKind.message,
                author_type=AuthorType.ai,
                author="cheese",
                content="明白，按 item-based CF + Recall@10 来做。",
                reply_to=q.id,
            )
        )
        # A doc block (struct tree) capturing current state.
        s.add(
            Block(
                project_id=project.id,
                topic_id=topic.id,
                kind=BlockKind.doc,
                author_type=AuthorType.ai,
                author="cheese",
                content=(
                    "## 目标\n搭建课程推荐原型。\n\n## 约束\n"
                    "- 算法：item-based 协同过滤\n- 指标：Recall@10\n\n"
                    "## 进展\n数据准备中。"
                ),
            )
        )

        # --- Milestones (日历) ---
        s.add_all(
            [
                Milestone(
                    project_id=project.id,
                    title="中期汇报",
                    description="向导师汇报进展，需导师验收",
                    due_date=now + timedelta(days=5),
                    status=MilestoneStatus.upcoming,
                    auto_pinned=False,
                ),
                Milestone(
                    project_id=project.id,
                    title="结题答辩",
                    due_date=now + timedelta(days=40),
                    status=MilestoneStatus.upcoming,
                ),
                Milestone(
                    project_id=project.id,
                    title="立项评审通过",
                    due_date=now - timedelta(days=10),
                    status=MilestoneStatus.done,
                    auto_pinned=True,
                ),
            ]
        )

        # --- A decision-request notification + an accept card (待处理) ---
        s.add(
            Alert(
                project_id=project.id,
                topic_id=topic.id,
                level=AlertLevel.light,
                kind=AlertKind.decision_request,
                target_handle="user-1",
                title="评测集怎么切分？",
                body="按时间切分还是随机切分训练/测试集？",
                payload={"options": ["按时间切分", "随机切分"]},
            )
        )
        s.add(
            AcceptCard(
                topic_id=topic.id,
                reviewer_handle="user-1",
                routing_reason="你是话题创建者，且最懂算法这块",
            )
        )

        await s.commit()

        print("✅ Seeded demo scenario")
        print(f"  Space:   {space.name} ({space.id})")
        print(f"  Project: {project.name} ({project.id})")
        print(f"  Root topic:  {root.id}")
        print(f"  Work topic:  {topic.id}")
        print(f"  Memory facts: {len(facts)}")


if __name__ == "__main__":
    asyncio.run(seed())
