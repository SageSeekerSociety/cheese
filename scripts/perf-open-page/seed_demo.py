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
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.models  # noqa: F401 — register every table so FKs resolve
from app.core.db import async_session_factory
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.device.service import device_service_for_session
from app.domain.device.supply import Supply
from app.domain.integration.models import Integration
from app.domain.integration.service import seal as seal_integration
from app.domain.library import records as library_records
from app.domain.living_doc.services import Documents
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.project import artifacts as project_artifacts
from app.domain.project import forge as project_forge
from app.domain.project.models import Project, ProjectForge
from app.domain.project.services import ProjectService
from app.domain.team.models import Team
from app.domain.team.protocol import TeamMemberRole
from app.domain.team.services import team_service
from app.domain.thread.services import open_thread
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


async def _make_project(session, name: str, team_id: int, owner: str) -> Project:
    # forge_kind stays the default ("forgejo") so the stubbed provisioner runs
    # and writes the inert ProjectForge row task branches read their base from.
    return await ProjectService(session).create(
        name=name,
        owner_handle=owner,
        team_id=team_id,
        intent="用于页面打开耗时测量的演示项目。",
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

    # Tasks in the busy channel.
    for i, title in enumerate(TASK_TITLES):
        await topics.create_task(
            room_id=busiest.id,
            created_by=owner,
            title=title,
            owner_handle=DEMO_MEMBERS[i % len(DEMO_MEMBERS)],
        )
        counts["tasks"] += 1

    # 资料库: documents and uploaded files.
    docs = Documents(session)
    for body in DOC_BODIES:
        title = body.splitlines()[0].lstrip("# ").strip()
        doc = await docs.create(project_id=project.id, title=title, author=owner)
        await docs.write(doc, body, author=owner, expected_version=doc.version)
        counts["documents"] += 1
    for name, text in LIBRARY_FILES:
        await library_records.add(
            session, project.id, name, text.encode("utf-8"),
            added_by=owner, room_id=busiest.id,
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
        (NotificationType.DEADLINE_REMIND, NotificationLevel.silent,
         "任务即将到期", "「梳理一下慢查询」今天到期。"),
    ]:
        await notifications.create(
            project_id=project.id, level=level, kind=kind, title=title, body=body,
            target_handle=owner,
        )
        counts["notifications"] += 1

    return counts


async def _seed_devices(session, users: dict[str, int], team_id: int, project: Project) -> int:
    owner_id = users[OWNER]
    service = device_service_for_session(session)
    wanted = ["演示工作站", "演示笔记本"]
    owned = await service.list_owned(owner_id)
    if any(device.name in wanted for device in owned):
        return 0
    made = 0
    for name in wanted:
        code = await service.start(name)
        device = await service.approve(
            code, owner_user_id=owner_id, supply=Supply.self_hosted, name=name
        )
        await service.assign_to_team(device.device_id, team_id, actor_user_id=owner_id)
        await service.assign_to_project(device.device_id, project.id, actor_user_id=owner_id)
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
    _stub_provisioner()
    random.seed(20261012)
    totals = dict(projects=0, channels=0, messages=0, tasks=0, threads=0,
                  documents=0, library_files=0, artifacts=0, notifications=0,
                  devices=0, integrations=0)

    async with async_session_factory() as session:
        users = await _users_by_handle(session)
        if OWNER not in users:
            raise SystemExit(
                f"demo user {OWNER!r} is missing — run `alembic upgrade head` first"
            )
        authors = [h for h in DEMO_MEMBERS if h in users]

        team_id = await _ensure_team(session, users)
        await _purge_projects(session, [name for name, _, _ in DEMO_PROJECTS])

        first_project: Project | None = None
        for name, project_team, owner in DEMO_PROJECTS:
            project = await _make_project(session, name, project_team, owner)
            first_project = first_project or project
            counts = await _seed_project(session, project, owner, authors)
            totals["projects"] += 1
            for key, value in counts.items():
                totals[key] += value
            print(f"seeded project {name!r} (team {project_team}, owner {owner})")

        assert first_project is not None
        totals["devices"] = await _seed_devices(session, users, team_id, first_project)
        totals["integrations"] = await _seed_integrations(session, users)

        await session.commit()

    print("\nTotals:")
    for key, value in totals.items():
        print(f"  {key:16s} {value}")


if __name__ == "__main__":
    asyncio.run(seed())
