"""Drive the REAL app end-to-end via Playwright + the real GLM model.

Unlike scripts/seed_demo.py (which hand-writes fake conversation/doc/decisions),
this script produces a GENUINELY real project state:

  - structural scaffold (users / space / task / members / foundational memory /
    milestones) is seeded in the DB — the stuff a human/onboarding sets up;
  - the actual work content (conversation, 施工现场, living docs, decisions,
    sub-topics, accept cards) is produced by 芝士 (real model) acting through the
    real UI via its platform tools.

The DB is LEFT POPULATED on exit (no truncate) so the result can be inspected
live at http://localhost:5173 .

Run (PG + backend:8099 + frontend:5173 must be up):
    cd backend && PYTHONPATH=. uv run --with playwright python scripts/sim_real.py
"""

import asyncio
import json
import sys
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

from playwright.async_api import async_playwright

from app.core.db import async_session_factory

# Import every model whose table is referenced by a FK so SQLAlchemy can resolve
# the mapper registry when we flush (milestones→topics, topics→blocks, etc.).
from app.domain.block.models import Block  # noqa: F401
from app.domain.memory.models import MemoryEntry, MemoryScope
from app.domain.notification.models import Notification  # noqa: F401
from app.domain.review.models import AcceptCard  # noqa: F401
from app.domain.topic.models import Topic  # noqa: F401
from app.domain.milestone.models import Milestone, MilestoneStatus
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectTaskLink,
)
from app.domain.space.models import Space, SpaceKind
from app.domain.task.models import Task, TaskTemplate
from app.domain.user.models import User

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def api_get(path: str):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.load(r)["data"]


def blocks_of(topic_id: str) -> list[dict]:
    d = api_get(f"/api/topics/{topic_id}/blocks")
    return d["data"] if isinstance(d, dict) and "data" in d else d


def count_blocks(topic_id: str, author_type: str | None, kind: str | None) -> int:
    n = 0
    for b in blocks_of(topic_id):
        if author_type is not None and b.get("author_type") != author_type:
            continue
        if kind is not None and b.get("kind") != kind:
            continue
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Phase A — structural scaffold (people / space / task)                         #
# --------------------------------------------------------------------------- #
async def seed_scaffold() -> uuid.UUID:
    """Create users + space + task template + task. Returns the task id."""
    async with async_session_factory() as s:
        s.add_all(
            [
                User(
                    handle="user-1",
                    name="林知行",
                    bio="计算机系大三，做后端和推荐算法，喜欢把事情拆清楚再动手。",
                    interests=["推荐系统", "后端工程", "可解释性"],
                    skills=["Python", "FastAPI", "PostgreSQL"],
                ),
                User(
                    handle="user-2",
                    name="王清越",
                    bio="设计 + 前端，关注交互细节和数据可视化。",
                    interests=["前端", "交互设计", "数据可视化"],
                    skills=["Vue", "TypeScript", "Figma"],
                ),
                User(
                    handle="mentor-1",
                    name="张衡",
                    bio="信息学院导师，研究推荐系统与教育数据挖掘。",
                    interests=["教育数据挖掘", "推荐系统"],
                    skills=["机器学习", "论文指导"],
                ),
            ]
        )
        space = Space(
            name="明理书院",
            kind=SpaceKind.college,
            description="书院创新项目入驻，定期里程碑汇报。",
        )
        s.add(space)
        await s.flush()
        template = TaskTemplate(
            space_id=space.id,
            name="创新项目入驻 2026 秋",
            description="书院创新项目，需中期汇报与结题答辩，由导师验收。",
            resource_pack={"compute": "1 GPU", "credits": 5000},
            conditions=[
                {"required_topic": "中期汇报", "reviewer_role": "mentor"},
                {"required_topic": "结题答辩", "reviewer_role": "mentor"},
            ],
            default_role="academic-research",
        )
        s.add(template)
        await s.flush()
        task = Task(
            template_id=template.id,
            title="用 AI 做课程推荐系统",
            description="为校内学生做一个个性化选课推荐系统。",
        )
        s.add(task)
        await s.flush()
        task_id = task.id
        await s.commit()
    _log(f"scaffold seeded (space + template + task={task_id})")
    return task_id


# --------------------------------------------------------------------------- #
# Phase C — enrich the UI-created project with members / memory / milestones   #
# --------------------------------------------------------------------------- #
async def enrich_project(project_id: str, task_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    async with async_session_factory() as s:
        proj = await s.get(Project, uuid.UUID(project_id))
        if proj is None:
            raise RuntimeError("project not found for enrich")
        proj.owner_handle = "user-1"
        proj.ai_mode = AiMode.collaborative
        proj.expert_role = "academic-research"

        s.add(ProjectTaskLink(project_id=proj.id, task_id=task_id))
        s.add_all(
            [
                ProjectMember(
                    project_id=proj.id, user_handle="user-1", role=ProjectRole.lead
                ),
                ProjectMember(
                    project_id=proj.id, user_handle="user-2", role=ProjectRole.member
                ),
                ProjectMember(
                    project_id=proj.id, user_handle="mentor-1", role=ProjectRole.mentor
                ),
            ]
        )
        # Foundational project memory (= earlier onboarding). 芝士 adds more live.
        for fact in [
            "本项目技术栈：后端 FastAPI + PostgreSQL，前端 Vue 3 + TypeScript。",
            "分工：林知行负责后端与算法，王清越负责前端与交互，张衡老师是导师。",
            "数据来源：教务处脱敏的历史选课数据，已签数据使用协议，仅用于本项目。",
            "中期汇报定在 2026-06-20，需要导师张衡验收。",
        ]:
            s.add(
                MemoryEntry(
                    scope=MemoryScope.project, scope_id=str(proj.id), content=fact
                )
            )
        s.add_all(
            [
                Milestone(
                    project_id=proj.id,
                    title="立项评审通过",
                    due_date=now - timedelta(days=12),
                    status=MilestoneStatus.done,
                    auto_pinned=False,
                ),
                Milestone(
                    project_id=proj.id,
                    title="结题答辩",
                    due_date=now + timedelta(days=45),
                    status=MilestoneStatus.upcoming,
                    auto_pinned=False,
                ),
            ]
        )
        await s.commit()
    _log("project enriched (owner / members / memory / milestones)")


# --------------------------------------------------------------------------- #
# Playwright helpers                                                            #
# --------------------------------------------------------------------------- #
async def wait_ai_turn(topic_id: str, label: str, timeout: float = 300.0) -> None:
    """Block until an AI message block appears for this topic (= turn done)."""
    before = count_blocks(topic_id, "ai", "message")
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(3)
        now = count_blocks(topic_id, "ai", "message")
        if now > before:
            events = count_blocks(topic_id, "ai", "event")
            _log(f"  ✓ {label}: AI replied (现场事件 {events} 个)")
            return
    _log(f"  ⚠ {label}: timed out after {timeout:.0f}s (continuing)")


async def wait_human(topic_id: str, label: str, timeout: float = 20.0) -> None:
    before = count_blocks(topic_id, "human", "message")
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1)
        if count_blocks(topic_id, "human", "message") > before:
            _log(f"  ✓ {label}: human message stored")
            return
    _log(f"  ⚠ {label}: human message not seen (continuing)")


async def ensure_summon_on(page) -> None:
    chip = page.locator(".summon-chip").first
    await chip.wait_for(state="visible", timeout=10000)
    cls = await chip.get_attribute("class") or ""
    if "summon-chip--on" not in cls:
        await chip.click()
    await page.wait_for_timeout(200)


async def ensure_summon_off(page) -> None:
    chip = page.locator(".summon-chip").first
    await chip.wait_for(state="visible", timeout=10000)
    cls = await chip.get_attribute("class") or ""
    if "summon-chip--on" in cls:
        await chip.click()
    await page.wait_for_timeout(200)


async def send_composer(page, text: str) -> None:
    box = page.locator(".composer-input textarea").first
    await box.wait_for(state="visible", timeout=10000)
    # Wait until WS connected (textarea no longer disabled).
    for _ in range(40):
        if not await box.is_disabled():
            break
        await page.wait_for_timeout(500)
    await box.click()
    await box.fill(text)
    await box.press("Enter")


# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #
async def main() -> None:
    task_id = await seed_scaffold()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: None)

        # ---- 1. Create the project through the real UI ----
        _log("opening app, creating project via UI…")
        await page.goto(BASE, wait_until="networkidle")
        proj_input = page.get_by_placeholder("新建项目")
        await proj_input.wait_for(state="visible", timeout=15000)
        await proj_input.fill("AI 课程推荐系统")
        await proj_input.press("Enter")
        await page.wait_for_url("**/project/**", timeout=20000)
        project_id = page.url.rstrip("/").split("/project/")[-1].split("/")[0]
        _log(f"project created: {project_id}")

        # ---- 2. Enrich with scaffold (members / memory / milestones) ----
        await enrich_project(project_id, task_id)
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(1500)

        topics = api_get(f"/api/topics?project_id={project_id}")["data"]
        root_id = next(t["id"] for t in topics if t["kind"] == "root")
        _log(f"root topic: {root_id}")

        # ---- 3. 本体 (root topic) opening turn — coordinate the whole project ----
        _log("【本体】root-topic opening turn (@芝士)…")
        await ensure_summon_on(page)
        await send_composer(
            page,
            "我是组长林知行。本项目叫『AI 课程推荐系统』——给校内学生做个性化选课推荐"
            "（注意：项目本身就是这个推荐系统，不是别的平台）。请作为芝士本体给项目开个头："
            "1) 用一段话说清楚我们要做什么、分成哪几个工作块、谁负责；"
            "2) 把这份开工概览写进本话题的活文档；"
            "3) 把『中期汇报』钉成里程碑（due 2026-06-20，需导师张衡验收）。",
        )
        await wait_ai_turn(root_id, "本体开工")

        # ---- 4. Work topic (created untitled via the + button) ----
        # Titles are AI-generated now: + creates a "新话题" and 芝士 names it via
        # `cheese title` on its first turn. We no longer pre-set the title.
        _log("creating work topic via + (untitled; 芝士 will name it)…")
        await page.get_by_title("新建话题").first.click()
        await page.wait_for_timeout(2500)
        topics = api_get(f"/api/topics?project_id={project_id}")["data"]
        work_id = next(
            t["id"]
            for t in topics
            if t["kind"] == "topic" and t["title"] == "新话题"
        )
        _log(f"work topic: {work_id} (untitled, awaiting 芝士 title)")
        # handleCreateTopic already selected the new topic in the UI.
        await page.wait_for_timeout(800)

        # 4a. a human-only message (no @芝士) so the 群聊 has a real human turn
        await ensure_summon_off(page)
        await send_composer(
            page,
            "数据这块我来负责：教务处脱敏的选课数据已经拿到，使用协议也签好了，"
            "可以开始做原型了。",
        )
        await wait_human(work_id, "工作话题·人类发言")

        # 4b. startup, split into single-tool turns (GLM hangs on many-tool turns;
        # one tool per turn = each gets its own reply bubble + 🔧 event, more
        # natural 群聊 too).
        await ensure_summon_on(page)
        await send_composer(
            page,
            "请根据项目记忆，说明我们定的技术栈和算法方向，"
            "并把本话题的活文档建好（目标 / 约束 / 计划 / 当前进展）。",
        )
        await wait_ai_turn(work_id, "工作话题·建文档")

        await ensure_summon_on(page)
        await send_composer(
            page,
            "把这条决策记进项目的决策记录："
            "『先用 item-based 协同过滤做 MVP，评测指标用 Recall@10』。",
        )
        await wait_ai_turn(work_id, "工作话题·记决策")

        await ensure_summon_on(page)
        await send_composer(
            page,
            "把『数据清洗与特征工程』拆成一个子话题，交给你的分身去专门做。",
        )
        await wait_ai_turn(work_id, "工作话题·拆子话题")

        # 4c. follow-up — progress + hand the accept card to the mentor
        await ensure_summon_on(page)
        await send_composer(
            page,
            "原型我已经跑通离线评测，Recall@10 到了 0.18，比随机基线好很多。"
            "请把文档进展更新成『原型完成，等待中期验收』，"
            "并把成果验收卡递给导师张衡(mentor-1)，理由写清楚为什么找他。",
        )
        await wait_ai_turn(work_id, "工作话题·收尾验收")

        # ---- 5. 私聊 (1:1 with 芝士) — recall + personal memory ----
        _log("【私聊】opening private chat with 芝士…")
        await page.get_by_text("与芝士私聊").first.click()
        await page.wait_for_timeout(1500)
        priv = api_get(f"/api/projects/{project_id}/private-chat?user_handle=user-1")
        priv_id = priv["id"]
        _log(f"private topic: {priv_id}")
        # ChatPanel's own composer already defaults to @芝士 ON.
        await send_composer(
            page,
            "我个人有点担心数据隐私合规这块，咱们项目在数据使用上是怎么保证的？",
        )
        await wait_ai_turn(priv_id, "私聊·隐私问答")

        await send_composer(
            page,
            "帮我记一下：我对推荐系统的可解释性特别感兴趣，以后想往这个方向深入。",
        )
        await wait_ai_turn(priv_id, "私聊·记个人偏好")

        await browser.close()

    # ---- summary (no truncate; state is left for inspection) ----
    print("\n" + "=" * 60)
    _log("DONE. Real project left in DB for inspection:")
    print(f"  project_id : {project_id}")
    print(f"  open       : {BASE}/project/{project_id}")
    work_events = count_blocks(work_id, "ai", "event")
    decisions = api_get(f"/api/projects/{project_id}/decisions")
    n_dec = decisions.get("total") if isinstance(decisions, dict) else len(decisions)
    print(f"  工作话题 现场事件 : {work_events}")
    print(f"  决策记录 条数      : {n_dec}")
    topics = api_get(f"/api/topics?project_id={project_id}")["data"]
    print(f"  话题总数（含子话题）: {len(topics)}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
