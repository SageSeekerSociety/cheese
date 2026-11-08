"""造两件任务：一件让它的队友会话被 runner 宣告为 running（模拟在跑），一件闲着。
只写本机隔离开发栈的库，不碰任何线上数据。"""
import asyncio, json, os, sys, uuid
import httpx

BASE = sys.argv[1]
c = httpx.Client(base_url=BASE, timeout=30)
r = c.post("/users/auth/login", json={"username": "alice", "password": "demo12345"})
r.raise_for_status()
tok = r.json()["data"]["accessToken"]
c.headers["Authorization"] = f"Bearer {tok}"
projects = c.get("/projects").json()["data"]
projects = projects.get("data", projects) if isinstance(projects, dict) else projects
p = projects[0]
room = p["root_topic_id"]
seats = [m["member_handle"] for m in c.get(f"/topics/{room}/members").json()["data"]["data"] if m["agent"]]
existing = {t["title"]: t for t in c.get(f"/topics/{room}/tasks", params={"limit": 0}).json()["data"]["data"]}
def task(title):
    if title in existing: return existing[title]
    r = c.post(f"/topics/{room}/tasks", json={"title": title}); r.raise_for_status(); return r.json()["data"]
working = task("首屏标记·会话在跑（模拟）")
quiet = task("首屏标记·没有会话")

async def run_it():
    from app.core.db import async_session_factory as factory  # noqa
    from app.domain.agent.harness.driven.lifecycle import RUNNING
    from app.domain.agent.session_state import SeatKey, declare, register
    key = SeatKey(uuid.UUID(working["id"]), seats[0], "claude-code")
    await register(factory, key)
    async with factory() as s:
        await declare(s, key, RUNNING); await s.commit()
asyncio.run(run_it())
print(json.dumps({"project": p["id"], "room": room, "working": working["id"], "quiet": quiet["id"], "seat": seats[0], "token": tok}))
