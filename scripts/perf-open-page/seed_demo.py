"""Seed a "realistic enough" demo dataset into the local 知是 database so the
open-page timing harness can measure pages that actually draw content (not their
empty states).

Run it from the backend so `import app` resolves, the way the repo's own scripts
are run::

    cd backend && PYTHONPATH=. uv run python ../scripts/perf-open-page/seed_demo.py

Everything goes through the app's own service layer (ProjectService,
TopicService, BlockRepository, Documents, library records, project artifacts,
teams, devices, notifications, integrations), so the state matches what the
product itself would write. What lands:

  * a demo 小队 (team) and its members;
  * three projects, each with its 综合 root channel, ordinary channels, tasks,
    支线 (threads) with replies, and 20+ messages in the busy channel;
  * project documents (资料库 文档) and uploaded library files;
  * 交付物 (project_artifacts);
  * project-inbox notifications for alice;
  * connected compute devices (device + hosted_device);
  * integrations ("我的连接": a mailbox and a Feishu account).

Idempotent: keyed on names, so re-running replaces the demo projects rather than
stacking a second copy.

The forge (code host) is NOT configured on this box, so the real
`provision_repository` / `default_branch` would raise GatewayUnavailableError;
the script stubs both (see `_stub_provisioner`) so no forge call is made — a
task's branch still gets a base from an inert local ProjectForge row.
"""

from __future__ import annotations

import asyncio
import json
import random
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401 — register every table so FKs resolve
from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.agent_instance.adapters import (
    default_teammate,
    project_team,
    seat_project,
)
from app.domain.block.models import BlockKind
from app.domain.event_author import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.deliverables import artifacts as project_artifacts
from app.domain.device.service import device_service_for_session
from app.domain.device.supply import Supply
from app.domain.integration.models import Integration
from app.domain.integration.service import seal as seal_integration
from app.domain.library import records as library_records
from app.domain.library.shelf import Shelf
from app.domain.living_doc.models import Document
from app.domain.living_doc.overview import seed_overview
from app.domain.living_doc.services import Documents
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.project import forge as project_forge
from app.domain.project.models import Project, ProjectArtifact, ProjectForge
from app.domain.project.services import ProjectService
from app.domain.project_skill.service import ProjectSkillService
from app.domain.team.models import Team
from app.domain.team.protocol import TeamMemberRole
from app.domain.team.services import team_service
from app.domain.thread.services import open_thread
from app.domain.thread.models import Thread
from app.domain.room_task import dispatch as task_dispatch
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.user.repositories import UserRepository

OWNER = "alice"
DEMO_TEAM_NAME = "性能演示小队"
DEMO_TEAM_HANDLE = "perf-demo"

# The demo users alembic's seed migration created (219831eb75a3): username == handle.
DEMO_MEMBERS = ["alice", "bobby", "carol", "david", "evelyn"]

# (project name, team id, owner). Teams 1/2/3 exist from the demo migration and
# the owner is a member of theirs, so the project shows on that person's rail.
DEMO_PROJECTS = [
    ("性能演示 · 数据管线", 1, "alice"),
    ("性能演示 · 前端重构", 2, "bobby"),
    ("性能演示 · 推理服务", 3, "carol"),
]

# Ordinary channels opened under each project's 综合 root.
EXTRA_CHANNELS = ["需求讨论", "设计评审", "每日站会"]

TASK_TITLES = [
    "补齐登录页的错误提示",
    "把接口响应时间打进日志",
    "修正移动端的表格滚动",
    "给预览加一个空态",
    "梳理一下慢查询",
    "把列表换成虚拟滚动",
]

MESSAGE_TEXTS = [
    "今天这个页面的打开有点慢，先量一遍基线。",
    "我把 profiling 打开看了一下，主要卡在首屏那个大列表。",
    "@芝士 帮我把慢的那几个接口按耗时排个序。",
    "这个问题的复现路径我记在资料库里了。",
    "刚推了一版，缓存命中率上来了。",
    "调度那块再看一眼，别把长任务排在一起。",
    "确认一下：这个改动只影响工作台，不影响后台。",
    "好，那我先按这个方向做。",
    "这版我们先把端到端跑通，细节后面补。",
    "有个边界情况：空项目也要能画出东西来。",
    "文档我更新到 资料库/设计说明.md 了。",
    "站会同步一下：接口那部分今天能收尾。",
    "收到，我这边并行把测试补上。",
    "@芝士 把这条拆成一个任务。",
    "接口的 p95 从 480ms 降到了 210ms。",
    "先合这个最小改动，剩下的分两步。",
    "这个列表要虚拟滚动，不然一千条会卡。",
    "记得给新接口加上限流。",
    "我把会议纪要贴在这里，谁有空认领一下。",
    "看起来没问题，可以进入评审了。",
    "评审意见我写在卡上了，就两条。",
    "这条我改成异步了，主线程不再阻塞。",
    "缓存那层加个失效策略，别读到旧的。",
    "好，那就这么定了。",
]

DOC_BODIES = [
    "# 项目目标\n\n"
    "把页面打开耗时降到 1 秒以内。\n\n"
    "## 这一阶段\n\n"
    "- 量出基线\n- 找出瓶颈\n- 逐个消除\n",
    "# 设计说明\n\n"
    "工作台按「导航 → 页面 → 数据」三层拆。\n\n"
    "## 约束\n\n"
    "首屏不加载第二页的数据；列表超过 100 条走虚拟滚动。\n",
]

LIBRARY_FILES = [
    ("设计说明.md", "# 设计说明\n\n这是一份放在项目资料库里的说明文档。\n"),
    ("接口清单.csv", "method,path,owner\nGET,/api/topics,alice\nPOST,/api/tasks,david\n"),
    ("会议纪要.txt", "站会 2026-10-12\n- 接口收尾\n- 测试补齐\n"),
    ("预算表.csv", "item,amount\ncompute,1200\nstorage,300\n"),
]

ARTIFACTS = [
    ("性能基线报告", "首屏打开耗时的基线与目标，给项目成员和评审看。"),
    ("重构方案", "工作台拆分的方案文档，给实现的人照着做。"),
    ("演示地址", "演示环境的入口，给要看效果的人打开。"),
]

# (name, title, description, body) — confirmed project skills, so the skills
# page has rows to draw instead of its empty state.
SKILLS = [
    (
        "weekly-report",
        "周报",
        "每周把项目进展整理成一页给人看。",
        "## 需要的输入\n\n用户给的这一周的进展和数字。\n\n"
        "## 步骤\n\n先写结论，再写依据。\n",
    ),
    (
        "page-open-budget",
        "页面打开耗时预算",
        "新页面按这个预算做，超了就说明理由。",
        "## 预算\n\n点开不超过 500ms，冷开不超过 1.5s。\n",
    ),
    (
        "review-reply",
        "评审回复",
        "收到评审意见后怎么逐条回。",
        "## 步骤\n\n每条先写处理了没有，再写怎么改的。\n",
    ),
]

# Projects shown on the demo team's own page.
TEAM_PROJECTS = [
    ("性能演示 · 小队主页一号", "alice"),
    ("性能演示 · 小队主页二号", "bobby"),
]

THREAD_REPLIES = [
    "这条支线的结论：先按最小改动合。",
    "同意，剩下的分两步。",
    "我来跟进第二步。",
]


def _stub_provisioner() -> None:
    """Keep project creation and task branches off the (unconfigured) code host.

    The local box has no forge, so the real ``provision_repository`` and
    ``default_branch`` would raise GatewayUnavailableError. Both names are
    imported from ``app.domain.project.forge`` at call time, so replacing the
    module attributes is enough: provisioning writes an inert ProjectForge row
    (no network), and ``default_branch`` reads it back off the row.
    """

    async def provision_repository(project_id, session, **kwargs):
        existing = await session.scalar(
            select(ProjectForge).where(ProjectForge.project_id == project_id)
        )
        if existing is not None:
            return existing
        binding = ProjectForge(
            project_id=project_id,
            kind="forgejo",
            repo=f"perf-demo/{project_id.hex[:8]}",
            url=f"https://forge.invalid/perf-demo/{project_id.hex[:8]}.git",
            api_url="https://forge.invalid/api/v1",
            default_branch="main",
        )
        session.add(binding)
        await session.flush()
        return binding

    async def default_branch(project_id, session, **kwargs):
        binding = await project_forge.binding_for_project(project_id, session)
        return binding.default_branch if binding is not None else "main"

    project_forge.provision_repository = provision_repository  # type: ignore[assignment]
    project_forge.default_branch = default_branch  # type: ignore[assignment]


async def _users_by_handle(session) -> dict[str, int]:
    repo = UserRepository(session)
    out: dict[str, int] = {}
    for handle in DEMO_MEMBERS + ["cheese"]:
        user = await repo.get_by_handle(handle)
        if user is not None:
            out[handle] = user.id
    return out


async def _ensure_team(session, users: dict[str, int]) -> int:
    """Create the demo team (idempotent by name); return its id."""
    found = await session.scalar(select(Team).where(Team.name == DEMO_TEAM_NAME))
    if found is not None:
        return found.id
    team = await team_service(session).create_team(
        name=DEMO_TEAM_NAME,
        intro="用于页面打开耗时测量的演示小队",
        description="一支用来演示「已有一批真实数据」的小队。",
        avatar_id=3,
        owner_id=users[OWNER],
        handle=DEMO_TEAM_HANDLE,
    )
    for handle in ("bobby", "carol", "david"):
        if handle in users:
            await team_service(session).add_member(
                team.id, users[handle], TeamMemberRole.MEMBER
            )
    return team.id


async def _purge_projects(session, names: list[str]) -> None:
    """Drop earlier demo projects by name (cascades to their whole subtree)."""
    before = 0
    existing = list(
        (await session.scalars(select(Project).where(Project.name.in_(names)))).all()
    )
    for project in existing:
        await session.delete(project)
        before += 1
    await session.flush()
    if before:
        print(f"removed {before} earlier demo project(s)")


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
FORBIDDEN_DBS = {"postgres", "template0", "template1", "prod", "production"}


def assert_safe_demo_db(db_url: str) -> str:
    """Refuse to purge/seed demo projects on a non-local or production database."""
    parsed = urllib.parse.urlsplit(db_url)
    host = (parsed.hostname or "").lower()
    dbname = parsed.path.lstrip("/")
    if host not in ALLOWED_HOSTS:
        raise RuntimeError(
            f"Refusing to seed non-local database host {host!r} (allowed: {sorted(ALLOWED_HOSTS)})"
        )
    if not dbname or dbname.lower() in FORBIDDEN_DBS:
        raise RuntimeError(f"Refusing to seed database {dbname!r}")
    return dbname


async def _make_project(session, name: str, team_id: int, owner: str) -> Project:
    # forge_kind stays the default ("forgejo") so the stubbed provisioner runs
    # and writes the inert ProjectForge row task branches read their base from.
    return await ProjectService(
        session,
        default_teammate=default_teammate,
        overview=seed_overview,
    ).create(
        name=name,
        owner_handle=owner,
        team_id=team_id,
        intent=DOC_BODIES[0],
    )


async def _say(
    session,
    project: Project,
    conversation_id: uuid.UUID,
    author: str,
    content: str,
    *,
    created_at: datetime | None = None,
):
    return await BlockRepository(session).add(
        project_id=project.id,
        conversation_id=conversation_id,
        author=author,
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content=content,
        created_at=created_at,
    )


async def _seed_project(session, project: Project, owner: str, authors: list[str]) -> dict:
    """Fill one project and return its subtotals."""
    counts = dict(messages=0, channels=0, tasks=0, threads=0, documents=0,
                  library_files=0, artifacts=0, notifications=0)

    topics = TopicService(session)
    channels = []
    for title in EXTRA_CHANNELS:
        channels.append(
            await topics.create(
                project_id=project.id,
                title=title,
                created_by=owner,
                description=f"{title} —— 演示频道",
            )
        )
    counts["channels"] = len(channels)
    busiest = channels[0]

    # 20+ messages in the busy channel, spread over the last day.
    now = datetime.now(UTC)
    for i in range(22):
        await _say(
            session, project, busiest.id, authors[i % len(authors)],
            MESSAGE_TEXTS[i % len(MESSAGE_TEXTS)],
            created_at=now - timedelta(hours=22 - i),
        )
        counts["messages"] += 1
    # A handful in 综合 too, so the root has content of its own.
    if project.root_topic_id is not None:
        for i in range(4):
            await _say(
                session, project, project.root_topic_id, authors[i % len(authors)],
                MESSAGE_TEXTS[(i + 5) % len(MESSAGE_TEXTS)],
                created_at=now - timedelta(hours=i),
            )
            counts["messages"] += 1

    # Two 支线 under messages in the busy channel, each with replies.
    for i in range(2):
        root = await _say(
            session, project, busiest.id, authors[0],
            "这一条下面开个支线，单独讨论。",
            created_at=now - timedelta(minutes=30 - i),
        )
        thread = await open_thread(session, root.id, by=authors[0])
        counts["threads"] += 1
        for j, text in enumerate(THREAD_REPLIES):
            await _say(
                session, project, thread.id, authors[(j + 1) % len(authors)], text,
                created_at=now - timedelta(minutes=20 - i * 5 - j),
            )
            counts["messages"] += 1

    # Tasks in the busy channel. 2026-10-16：开一条活的那批函数从 `TopicService`
    # 搬去了 `room_task.dispatch`，两个口子在这里按名字递进去（和路由同一个写法）。
    first_task: Task | None = None
    for i, title in enumerate(TASK_TITLES):
        t_row = await task_dispatch.create_task(
            session,
            room_id=busiest.id,
            created_by=owner,
            title=title,
            owner_handle=DEMO_MEMBERS[i % len(DEMO_MEMBERS)],
            seat_project=seat_project,
            team=project_team,
        )
        if first_task is None:
            first_task = t_row
        counts["tasks"] += 1

    # Seed messages in the first task so workspace-task renders real conversation rows.
    if first_task is not None:
        for k in range(4):
            await _say(
                session,
                project,
                first_task.id,
                authors[k % len(authors)],
                MESSAGE_TEXTS[(k + 2) % len(MESSAGE_TEXTS)],
                created_at=now - timedelta(minutes=15 - k),
            )
            counts["messages"] += 1

    # 资料库: documents and uploaded files.
    docs = Documents(session)
    for body in DOC_BODIES:
        title = body.splitlines()[0].lstrip("# ").strip()
        doc = await docs.create(project_id=project.id, title=title, author=owner)
        await docs.write(doc, body, author=owner, expected_version=doc.version)
        counts["documents"] += 1
    for name, text in LIBRARY_FILES:
        await library_records.add(
            session,
            Shelf.of_project(project.id, with_task=False),
            name,
            text.encode("utf-8"),
            added_by=owner,
            room_id=busiest.id,
        )
        counts["library_files"] += 1

    # 交付物.
    for name, about in ARTIFACTS:
        await project_artifacts.claim(
            session, project_id=project.id, name=name, about=about
        )
        counts["artifacts"] += 1

    # Inbox.
    notifications = ProjectNotificationService(session)
    for kind, level, title, body in [
        (NotificationType.CHANGE_ALERT, NotificationLevel.light,
         "缓存策略有变更", "首屏请求的缓存策略已经调整，请留意指标。"),
        (NotificationType.DECISION_REQUEST, NotificationLevel.strong,
         "请确认下一步方向", "是先做虚拟滚动，还是先把接口串行改掉？"),
        (NotificationType.ACCEPT_REQUEST, NotificationLevel.strong,
         "有一张验收卡等你验收", "「补齐登录页的错误提示」已经交付，请验收。"),
        (NotificationType.MENTION, NotificationLevel.light,
         "有人 @ 了你", "在「需求讨论」里点名了 alice。"),
    ]:
        await notifications.create(
            project_id=project.id, level=level, kind=kind, title=title, body=body,
            target_handle=owner,
        )
        counts["notifications"] += 1

    return counts


async def _seed_skills(session, project: Project) -> int:
    """Confirmed project skills, so the skills page draws rows and not an empty state."""
    service = ProjectSkillService(session)
    have = {row.name for row in await service.list(project.id)}
    made = 0
    for name, title, description, body in SKILLS:
        if name in have:
            continue
        await service.create(
            project_id=project.id,
            topic_id=None,
            by=OWNER,
            by_agent=False,
            name=name,
            fields={"title": title, "description": description, "body": body},
        )
        made += 1
    return made


async def _seed_team_projects(session, team_id: int, users: dict[str, int]) -> int:
    """Projects under the demo team, so the team's own page has cards."""
    made = 0
    for name, owner in TEAM_PROJECTS:
        found = await session.scalar(select(Project).where(Project.name == name))
        if found is not None:
            continue
        await _make_project(session, name, team_id, owner)
        made += 1
    return made


async def _seed_devices(session, users: dict[str, int], team_id: int) -> int:
    owner_id = users[OWNER]
    service = device_service_for_session(session)
    wanted = ["演示工作站", "演示笔记本"]
    devices = await service.list_devices_for_team(team_id)
    if any(device.name in wanted for device in devices):
        return 0
    made = 0
    for name in wanted:
        code = await service.start(name)
        await service.approve(
            code,
            owner_user_id=owner_id,
            team_id=team_id,
            supply=Supply.self_hosted,
            name=name,
        )
        made += 1
    return made


async def _seed_integrations(session, users: dict[str, int]) -> int:
    """Two connections on alice's account: a mailbox and a Feishu account.

    Written straight to the model with the domain's own ``seal`` for the secret,
    because ``IntegrationService.connect`` runs a live network check against the
    mail server (``_check``), which there is none of here.
    """
    owner_id = users[OWNER]
    existing = list(
        (await session.scalars(
            select(Integration).where(Integration.owner_user_id == owner_id)
        )).all()
    )
    wanted = {"alice@demo.test", "飞书（演示）"}
    if any(row.label in wanted for row in existing):
        return 0
    made = 0
    mail = Integration(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        owner_handle=OWNER,
        provider="mail",
        label="alice@demo.test",
        config={
            "imap_host": "imap.demo.test",
            "imap_port": 993,
            "smtp_host": "smtp.demo.test",
            "smtp_port": 465,
            "username": "alice@demo.test",
            "security": "ssl",
        },
        grants=[],
        status="ok",
    )
    seal_integration(mail, {"password": "demo-password"})
    session.add(mail)
    made += 1

    feishu = Integration(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        owner_handle=OWNER,
        provider="feishu",
        label="飞书（演示）",
        config={"folders": ["收件箱"]},
        grants=[],
        status="ok",
    )
    seal_integration(
        feishu,
        {"user_access_token": "demo-token", "refresh_token": "demo-refresh"},
    )
    session.add(feishu)
    made += 1
    await session.flush()
    return made


async def seed() -> None:
    assert_safe_demo_db(settings.database_url)
    _stub_provisioner()
    random.seed(20261012)
    totals = dict(projects=0, channels=0, messages=0, tasks=0, threads=0,
                  documents=0, library_files=0, artifacts=0, notifications=0,
                  devices=0, integrations=0, skills=0, team_projects=0)

    async with async_session_factory() as session:
        users = await _users_by_handle(session)
        if OWNER not in users:
            raise SystemExit(
                f"demo user {OWNER!r} is missing — run `alembic upgrade head` first"
            )
        authors = [h for h in DEMO_MEMBERS if h in users]

        team_id = await _ensure_team(session, users)
        await _purge_projects(
            session,
            [name for name, _, _ in DEMO_PROJECTS] + [name for name, _ in TEAM_PROJECTS],
        )

        first_project: Project | None = None
        for name, project_team_id, owner in DEMO_PROJECTS:
            project = await _make_project(session, name, project_team_id, owner)
            first_project = first_project or project
            counts = await _seed_project(session, project, owner, authors)
            totals["projects"] += 1
            for key, value in counts.items():
                totals[key] += value
            print(f"seeded project {name!r} (team {project_team_id}, owner {owner})")

        assert first_project is not None
        totals["skills"] = await _seed_skills(session, first_project)
        totals["team_projects"] = await _seed_team_projects(session, team_id, users)
        totals["devices"] = await _seed_devices(session, users, team_id)
        totals["integrations"] = await _seed_integrations(session, users)

        await session.commit()
        await _write_params(session, first_project)

    print("\nTotals:")
    for key, value in totals.items():
        print(f"  {key:16s} {value}")


async def _write_params(session, project: Project) -> None:
    """Point params.json at the rows that were just written.

    The ids change on every re-seed (the projects are dropped and rebuilt), so
    the harness would silently measure a deleted project if this were left for a
    person to update by hand.
    """
    room = await session.scalar(
        select(Topic).where(Topic.project_id == project.id, Topic.title == EXTRA_CHANNELS[0])
    )
    assert room is not None, f"missing channel {EXTRA_CHANNELS[0]!r}"
    empty_room = await session.scalar(
        select(Topic).where(Topic.project_id == project.id, Topic.title == EXTRA_CHANNELS[2])
    )
    thread = await session.scalar(
        select(Thread).where(Thread.room_id == room.id).order_by(Thread.created_at).limit(1)
    )
    task = await session.scalar(
        select(Task).where(Task.room_id == room.id).order_by(Task.created_at).limit(1)
    )
    doc = await session.scalar(
        select(Document)
        .where(Document.project_id == project.id, Document.title.is_not(None))
        .order_by(Document.created_at)
        .limit(1)
    )
    artifact = await session.scalar(
        select(ProjectArtifact).where(ProjectArtifact.project_id == project.id).limit(1)
    )
    params = {
        "handle": OWNER,
        "projectId": str(project.id),
        "projectSlug": project.slug,
        "topicId": str(room.id),
        "emptyTopicId": str(empty_room.id) if empty_room else "",
        "threadId": str(thread.id) if thread else "",
        "taskId": str(task.id) if task else "",
        "docId": str(doc.id) if doc else "",
        "artifactId": str(artifact.id) if artifact else "",
        "teamHandle": DEMO_TEAM_HANDLE,
    }
    out = Path(__file__).with_name("params.json")
    out.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(seed())
