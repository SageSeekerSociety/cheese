"""The example project the docs' screenshots are taken in.

Run against a local stack (a migrated, `seed_fusion_demo`-seeded database and a
backend on BACKEND, default http://127.0.0.1:8081), from backend/:

    .venv/bin/python ../docs/site/shots/fixture.py

It builds one project through the API the way a person would — rooms, living
docs, split-out tasks — and fills in what only a running agent could produce
(芝士's replies, an accept card) straight in the database, so the pictures show
a room mid-work without a model. Every name in it is made up.

Re-running replaces the project.
"""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path.cwd()))

import app.models  # noqa: E402,F401 — every table
from sqlalchemy import delete, select, update  # noqa: E402

from app.core.db import async_session_factory  # noqa: E402
from app.domain.block.models import AuthorType, Block, BlockKind  # noqa: E402
from app.domain.library.service import library_root  # noqa: E402
from app.domain.project.models import Project  # noqa: E402
from app.domain.review.models import AcceptCard, AcceptStatus  # noqa: E402

BACKEND = os.environ.get("BACKEND", "http://127.0.0.1:8081")
PROJECT = "校园活动报名小程序"
NICKNAMES = {"alice": "林晓", "bobby": "陈默", "carol": "王珊", "david": "周然", "evelyn": "李想"}


def login(name: str) -> dict[str, str]:
    r = httpx.post(
        f"{BACKEND}/users/auth/login", json={"username": name, "password": "demo12345"}
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['accessToken']}"}


def api(method: str, path: str, headers: dict, **kw) -> dict:
    r = httpx.request(method, f"{BACKEND}{path}", headers=headers, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {r.status_code} {r.text}")
    return r.json().get("data") or {}


async def names() -> None:
    from sqlalchemy import text

    async with async_session_factory() as s:
        for handle, nick in NICKNAMES.items():
            await s.execute(
                text(
                    'UPDATE user_profile SET nickname = :n WHERE user_id = '
                    '(SELECT id FROM "user" WHERE username = :h)'
                ),
                {"n": nick, "h": handle},
            )
        old = (await s.scalars(select(Project.id).where(Project.name == PROJECT))).all()
        if old:
            await s.execute(delete(Project).where(Project.id.in_(old)))
        await s.commit()


async def say(room: str, lines: list[tuple[str, str, int]]) -> None:
    """(author, text, minutes ago) — people and 芝士 alike."""
    async with async_session_factory() as s:
        from app.domain.topic.models import Topic

        topic = await s.get(Topic, uuid.UUID(room))
        now = datetime.now(UTC)
        for author, text, ago in lines:
            s.add(
                Block(
                    project_id=topic.project_id,
                    topic_id=topic.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=author,
                    content=text,
                    created_at=now - timedelta(minutes=ago),
                    meta={"consumed_turn": None} if not author.startswith("cheese") else {},
                )
            )
        await s.commit()


async def card(room: str, task: str, reviewer: str) -> None:
    async with async_session_factory() as s:
        s.add(
            AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task),
                delivered_task_ids=[task],
                reviewer_handle=reviewer,
                routing_reason="报名表单是林晓提的需求，由她验收。",
                change_subject="feat(form): cut the sign-up form to four required fields",
                change_body=(
                    "必填项从 9 项减到 4 项：姓名、学号、手机号、场次；"
                    "院系和年级由学号自动带出。手机 375 宽一屏填完。"
                ),
                status=AcceptStatus.pending,
            )
        )
        await s.commit()


def library(project_id: str) -> None:
    root = library_root(uuid.UUID(project_id))
    (root / "活动方案.md").write_text(
        "# 迎新周活动方案\n\n- 时间：9 月 28 日—10 月 3 日\n- 场次：每天两场\n", encoding="utf-8"
    )
    (root / "报名数据-9月.csv").write_text(
        "学号,姓名,场次,报名时间\n2026010101,张同学,9/28 上午,2026-09-20 10:02\n",
        encoding="utf-8",
    )


def feedback(alice: dict) -> None:
    """A few public feedback items, so the feedback center is not empty."""
    existing = api("GET", "/feedback", alice) or {}
    rows = existing.get("data", existing.get("items", [])) if isinstance(existing, dict) else []
    if rows:
        return
    bobby = login("bobby")
    for who, body in (
        (alice, {
            "kind": "bug", "title": "手机上验收卡的「采纳」按钮被输入框挡住",
            "problem": "在 iPhone 上打开话题，验收卡滑到底时「采纳」按钮在输入框下面，点不到。",
            "expectation": "按钮在输入框上方，或者输入框收起时能点到。",
            "repro": "手机打开有验收卡的话题 → 滑到验收卡底部。",
            "visibility": "public", "tags": ["移动端", "验收"],
        }),
        (bobby, {
            "kind": "suggestion", "title": "看板能按负责人筛选",
            "problem": "项目任务多了之后，想只看自己负责的那几条。",
            "why": "每天早上要先找自己的活。", "visibility": "public", "tags": ["看板"],
        }),
        (bobby, {
            "kind": "other", "title": "资料库能不能支持文件夹",
            "problem": "资料多了以后都平铺在一起，找起来慢。", "visibility": "public",
        }),
    ):
        api("POST", "/feedback", who, json=body)


async def main() -> None:
    await names()
    alice = login("alice")
    project = api(
        "POST",
        "/projects",
        alice,
        json={"name": PROJECT, "owner_handle": "alice", "team_id": 1},
    )
    pid = project["id"]
    rooms = {}
    for title in ("报名表单改版", "迎新海报设计", "周会纪要 9/22"):
        rooms[title] = api(
            "POST", "/topics", alice, json={"project_id": pid, "title": title}
        )["id"]
    form = rooms["报名表单改版"]
    agent = next(
        m["member_handle"]
        for m in api("GET", f"/topics/{form}/members", alice)["data"]
        if m["agent"]
    )

    doc = api("GET", f"/topics/{form}/doc", alice) or {}
    api(
        "PUT",
        f"/topics/{form}/doc",
        alice,
        json={
            "content": (
                "## 目标\n\n把报名表单的必填项压到 4 项以内，手机上一屏能填完。\n\n"
                "## 已定\n\n- 必填：姓名、学号、手机号、场次\n"
                "- 院系、年级由学号自动带出\n- 场次默认选报名还开放的最近一场\n\n"
                "## 进展\n\n- 表单字段精简：已完成，等林晓验收\n"
                "- 提交校验与错误提示：进行中\n"
            ),
            "expected_version": doc.get("doc_version", 0),
        },
    )
    tasks = [
        api(
            "POST",
            f"/topics/{form}/split",
            alice,
            json={"title": title, "brief": brief, "reviewer_handle": "alice"},
        )["id"]
        for title, brief in (
            ("表单字段精简", "必填项减到 4 项，院系年级由学号带出。"),
            ("提交校验与错误提示", "手机号、学号格式校验，错误提示写在输入框下方。"),
        )
    ]
    await (
        say(
            form,
            [
                (
                    "alice",
                    f"<@{agent}> 报名表单现在要填 9 项，很多同学填到一半就走了。帮我把必填项"
                    "压到 4 项以内，手机上一屏能填完，改完开 PR 给我看。",
                    95,
                ),
                (
                    agent,
                    "明白：把报名表单的必填项压到 4 项以内、手机一屏填完，改完开 PR 请你验收。"
                    "我先看现在的表单和提交记录，确认哪些字段能改成选填或自动带出，"
                    "然后拆成两条活并行：表单字段精简、提交校验与错误提示。",
                    94,
                ),
                ("bobby", "场次能不能默认选最近的那一场？现在每次都要点开下拉。", 60),
                (
                    agent,
                    "可以。场次默认选报名还开放的最近一场，已经加进「表单字段精简」这条活里。",
                    59,
                ),
                (
                    agent,
                    "「表单字段精简」做完了：必填项从 9 项减到 4 项（姓名、学号、手机号、场次），"
                    "院系和年级由学号自动带出，手机 375 宽一屏填完。验收卡已经递给 "
                    "<@alice>，改动和截图都在卡上。",
                    12,
                ),
            ],
        )
    )
    await card(form, tasks[0], "alice")
    await (
        say(
            rooms["周会纪要 9/22"],
            [
                ("alice", "本周目标：迎新周报名上线；海报周三前定稿。", 2000),
                ("carol", "海报我来跟，周二给两版。", 1990),
                ("bobby", "报名数据导出我已经放到资料库了。", 1980),
            ],
        )
    )
    library(pid)
    feedback(alice)
    print(f"project {pid}\nrooms {rooms}\ntasks {tasks}\nagent {agent}")


if __name__ == "__main__":
    asyncio.run(main())
