"""E2E: per-topic scroll position is remembered.

Scroll up inside a topic, go elsewhere (another topic, or another view that
unmounts the chat), come back — you land where you left off, not at the bottom.

Seeds a project + two work topics (topic A filled with enough messages to be
scrollable) via API + DB, then drives the real UI with Playwright and asserts the
chat's scrollTop is restored. Pure UI behavior, so it lives as an E2E, not in the
pytest suite.

Run (PG + backend:8099 + frontend:5173 up):
    cd backend && PYTHONPATH=. uv run --with playwright \
        python scripts/e2e_scroll_memory.py
"""

import asyncio
import json
import sys
import urllib.request
import uuid

from playwright.async_api import async_playwright

import app.models  # noqa: F401  (registers all tables so FK mappers resolve)
from app.core.db import async_session_factory
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
SCROLL = '[data-testid="chat-scroll"]'
TARGET = 240  # the scroll position (px from top) we expect to be restored


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def seed() -> tuple[str, str, str]:
    """Project + two work topics; topic A gets many messages (scrollable)."""
    pid = _post("/api/projects", {"name": "滚动记忆测试"})["data"]["id"]
    a = _post("/api/topics", {"project_id": pid, "title": "滚动话题甲"})["data"]
    b = _post("/api/topics", {"project_id": pid, "title": "滚动话题乙"})["data"]
    async with async_session_factory() as s:
        blocks = BlockRepository(s)
        for i in range(60):
            await blocks.add(
                project_id=uuid.UUID(pid),
                topic_id=uuid.UUID(a["id"]),
                author="user-1",
                author_type=AuthorType.human,
                content=f"历史消息 #{i:02d} —— 用来把对话撑高，方便测试滚动位置记忆。",
                kind=BlockKind.message,
            )
        await s.commit()
    return pid, a["title"], b["title"]


async def cleanup(pid: str) -> None:
    """Remove the seeded project so the test is repeatable and leaves no trace."""
    from sqlalchemy import text

    stmts = [
        "DELETE FROM blocks WHERE project_id=:p",
        "DELETE FROM milestones WHERE project_id=:p",
        "DELETE FROM accept_cards WHERE topic_id IN "
        "(SELECT id FROM topics WHERE project_id=:p)",
        "DELETE FROM notifications WHERE project_id=:p",
        "DELETE FROM project_members WHERE project_id=:p",
        "DELETE FROM project_task_links WHERE project_id=:p",
        "DELETE FROM memory_entries WHERE scope='project' AND scope_id=:p",
        "UPDATE projects SET root_topic_id=NULL WHERE id=:p",
        "DELETE FROM topics WHERE project_id=:p",
        "DELETE FROM projects WHERE id=:p",
    ]
    async with async_session_factory() as s:
        for stmt in stmts:
            await s.execute(text(stmt), {"p": pid})
        await s.commit()


async def select_topic(page, title: str) -> None:
    await page.get_by_text(title, exact=True).first.click()
    await page.wait_for_timeout(900)  # listBlocks + render + restore (nextTick)


async def scroll_top(page) -> float:
    return await page.locator(SCROLL).first.evaluate("e => e.scrollTop")


async def main() -> int:
    pid, title_a, title_b = await seed()
    print(f"seeded project {pid} (topics: {title_a}, {title_b})", flush=True)
    failures: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.goto(f"{BASE}/project/{pid}", wait_until="networkidle")
        await page.wait_for_timeout(1200)

        # Open topic A and scroll up to a known position.
        await select_topic(page, title_a)
        await page.locator(SCROLL).first.evaluate(f"e => e.scrollTop = {TARGET}")
        await page.wait_for_timeout(300)
        start = await scroll_top(page)
        print(f"scrolled topic A to {start:.0f}", flush=True)
        if abs(start - TARGET) > 5:
            failures.append(f"could not set scroll (got {start})")

        # Case 1: switch to another topic and back.
        await select_topic(page, title_b)
        await select_topic(page, title_a)
        after_switch = await scroll_top(page)
        print(f"after topic switch + back: {after_switch:.0f}", flush=True)
        if abs(after_switch - start) > 5:
            failures.append(
                f"topic switch did not restore scroll: {after_switch} != {start}"
            )

        # Case 2: navigate to another view (unmounts the chat) and back.
        await page.get_by_role("tab", name="项目总览").click()
        await page.wait_for_timeout(800)
        await page.get_by_role("tab", name="工作台").click()
        await page.wait_for_timeout(800)
        await select_topic(page, title_a)
        after_view = await scroll_top(page)
        print(f"after view away + back: {after_view:.0f}", flush=True)
        if abs(after_view - start) > 5:
            failures.append(
                f"view navigation did not restore scroll: {after_view} != {start}"
            )

        await browser.close()

    await cleanup(pid)
    print("cleaned up seeded project", flush=True)

    if failures:
        print("\nFAIL:", flush=True)
        for f in failures:
            print("  -", f, flush=True)
        return 1
    print(
        "\nPASS: scroll position restored after topic switch and view nav", flush=True
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
