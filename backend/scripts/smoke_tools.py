"""Live smoke test for 芝士's platform tools (spec §9.1).

Verifies the model actually invokes the in-process MCP tools and that they
mutate platform state. Needs Postgres up + ANTHROPIC_* env (Zhipu GLM).

Run: cd backend && PYTHONPATH=. uv run python scripts/smoke_tools.py
"""

import asyncio
import sys
import tempfile

from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.agent.service import AgentService, AgentToolUse
from app.domain.agent.skills import DEFAULT_CHAT_SKILLS, load_skills
from app.domain.agent.tools import build_cheese_server, tool_names
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService


async def main() -> int:
    # Fresh project + a work topic to act in.
    async with async_session_factory() as s:
        project = await ProjectService(s).create(
            name="工具冒烟项目", owner_handle="user-1"
        )
        topic = await TopicService(s).create(
            project_id=project.id, title="推进算法原型"
        )
        await s.commit()
        project_id, topic_id = project.id, topic.id

    agent = AgentService(model=settings.agent_model, env=settings.agent_env())
    server = build_cheese_server(
        session_factory=async_session_factory,
        project_id=project_id,
        topic_id=topic_id,
    )
    system_prompt = (
        settings.agent_system_prompt + "\n\n" + load_skills(DEFAULT_CHAT_SKILLS)
    )
    prompt = (
        "请做两件事：1) 把『实现离线评测脚本』拆成一个子话题；"
        "2) 把这条事实记进项目记忆：『评测指标用 Recall@10』。"
        "用你的工具来做，别只是口头说。"
    )

    tool_calls: list[str] = []
    async for event in agent.stream_reply(
        prompt=prompt,
        system_prompt=system_prompt,
        cwd=tempfile.mkdtemp(prefix="cheesex-tools-"),
        resume_session_id=None,
        mcp_servers={"cheese": server},
        allowed_tools=tool_names(),
    ):
        if isinstance(event, AgentToolUse):
            tool_calls.append(event.name)
            print(f"  [tool] {event.name} {event.input}")

    print(f"\nTool calls observed: {tool_calls}")

    # Verify platform state actually changed.
    async with async_session_factory() as s:
        children = await TopicRepository(s).list_children(topic_id)
        memories = await DbMemoryStore(s).recall(MemoryScope.project, str(project_id))

    print(f"Sub-topics created: {[c.title for c in children]}")
    print(f"Project memories: {memories}")

    failures = []
    if not children:
        failures.append("no sub-topic was created via create_subtopic tool")
    if not any("Recall" in m for m in memories):
        failures.append("the Recall@10 fact was not written via remember tool")

    print("\n" + "=" * 40)
    if failures:
        print("TOOLS SMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("TOOLS SMOKE PASSED ✅  (芝士 invoked tools and mutated platform state)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
