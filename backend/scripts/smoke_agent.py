"""Live smoke test for the Claude Agent SDK integration (Phase 0).

Exercises the REAL model (needs ANTHROPIC_API_KEY + the `claude` CLI). Verifies:
  1. streaming deltas arrive,
  2. memory injected into the system prompt is actually used,
  3. a captured session id resumes the conversation.

Run: uv run python scripts/smoke_agent.py
"""

import asyncio
import sys
import tempfile

from app.core.config import settings
from app.domain.agent.service import AgentDelta, AgentResult, AgentService


async def _run_turn(
    agent: AgentService,
    *,
    prompt: str,
    system_prompt: str,
    cwd: str,
    resume: str | None,
) -> tuple[str, str | None, int]:
    full = ""
    session_id = resume
    delta_count = 0
    async for event in agent.stream_reply(
        prompt=prompt,
        system_prompt=system_prompt,
        cwd=cwd,
        resume_session_id=resume,
    ):
        if isinstance(event, AgentDelta):
            delta_count += 1
            print(event.text, end="", flush=True)
            full += event.text
        elif isinstance(event, AgentResult):
            session_id = event.session_id
            if not full:
                full = event.text
    print()
    return full, session_id, delta_count


async def main() -> int:
    agent = AgentService(model=settings.agent_model, env=settings.agent_env())
    cwd = tempfile.mkdtemp(prefix="cheesex-smoke-")
    base = settings.agent_system_prompt
    failures: list[str] = []

    print(f"== model: {settings.agent_model} ==")

    # 1. Streaming + memory injection.
    print("\n[turn 1] memory-backed answer (代号 = Tomato)")
    sys_with_mem = (
        f"{base}\n\n## 项目记忆\n- 这个项目的内部代号是 Tomato。"
    )
    text1, session1, deltas1 = await _run_turn(
        agent,
        prompt="这个项目的内部代号是什么？只回代号。",
        system_prompt=sys_with_mem,
        cwd=cwd,
        resume=None,
    )
    if deltas1 == 0:
        failures.append("no streaming deltas received in turn 1")
    if "Tomato" not in text1:
        failures.append(f"memory not used: 'Tomato' missing from: {text1!r}")
    if not session1:
        failures.append("no session_id captured in turn 1")

    # 2. Resume the session and reference the earlier turn.
    if session1:
        print("\n[turn 2] resume session, recall previous question")
        text2, _, _ = await _run_turn(
            agent,
            prompt="我上一条消息问你的是什么？复述一下。",
            system_prompt=base,
            cwd=cwd,
            resume=session1,
        )
        if "代号" not in text2 and "Tomato" not in text2:
            failures.append(
                f"resume did not recall prior turn; got: {text2!r}"
            )

    print("\n" + "=" * 40)
    if failures:
        print("SMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SMOKE PASSED ✅  (streaming + memory + resume all work)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
