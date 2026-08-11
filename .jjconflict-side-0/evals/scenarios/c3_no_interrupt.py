"""C3 🟢 🤖 默认不接话（注意力分离）(docs/evals.md).

做什么：两个人在话题里闲聊，都不 @芝士。
对长什么样：芝士不插话——没有 AI 回复、没有 agent turn、没有 ✅ ack。

纯平台侧场景：全部确定性断言，不需要 judge。
"""

import asyncio

from evals.lib.client import ai_messages, human_messages, new_id
from evals.lib.context import EvalContext
from evals.lib.records import Check, Scenario, ScenarioOutcome

CHITCHAT = [
    ("alice", "中午吃什么？我想去吃食堂新开的麻辣香锅"),
    ("bob", "走啊，顺便帮我把上次借你的充电宝带上"),
]

# How long we give the system to (incorrectly) start a turn before asserting
# silence — generous vs. the instant fire-and-forget submit path.
SILENCE_WINDOW_S = 10.0


async def run(ctx: EvalContext) -> ScenarioOutcome:
    api = ctx.api
    suffix = new_id()
    project = await api.create_project(f"eval-C3-闲聊-{suffix}", "eval-owner")
    topic = await api.create_topic(project["id"], "日常闲聊", "eval-owner")
    ctx.log.info("C3 setup: project=%s topic=%s", project["id"], topic["id"])

    all_frames: list[list[dict]] = []
    for author, content in CHITCHAT:
        frames = await api.send_chat(
            topic["id"], content=content, author=author, summon=False
        )
        all_frames.append(frames)
        ctx.log.info("C3 posted (%s): %d frames", author, len(frames))

    await asyncio.sleep(SILENCE_WINDOW_S)
    active = await api.active_turns()
    blocks = await api.list_blocks(topic["id"])
    ai = ai_messages(blocks)
    humans = human_messages(blocks)
    flat_frames = [f for fs in all_frames for f in fs]
    agent_frames = [
        f
        for f in flat_frames
        if f.get("type") in ("turn_active", "assistant_block", "tool", "reaction")
    ]
    ai_blocks_any = [b for b in blocks if b["author_type"] == "ai"]

    checks = [
        Check(
            name="human_messages_landed",
            passed=len(humans) == len(CHITCHAT),
            detail=f"{len(humans)}/{len(CHITCHAT)} human messages visible",
        ),
        Check(
            name="no_ai_reply",
            passed=len(ai) == 0,
            detail=f"{len(ai)} AI message block(s)",
        ),
        Check(
            name="no_ai_activity_at_all",  # no tool events / 现场 traces either
            passed=len(ai_blocks_any) == 0,
            detail=f"{len(ai_blocks_any)} AI-authored block(s) of any kind",
        ),
        Check(
            name="no_agent_frames_on_ws",  # no turn_active / ✅ ack / tool frames
            passed=len(agent_frames) == 0,
            detail=f"{len(agent_frames)} agent-side frame(s): "
            f"{[f.get('type') for f in agent_frames]}",
        ),
        Check(
            name="no_turn_in_flight",
            passed=active == 0,
            detail=f"active_turns={active} after {SILENCE_WINDOW_S}s",
        ),
    ]

    return ScenarioOutcome(
        inputs={
            "project": project,
            "topic": topic,
            "messages": [
                {"author": a, "content": c, "summon": False} for a, c in CHITCHAT
            ],
            "silence_window_s": SILENCE_WINDOW_S,
        },
        evidence={
            "ws_frames": flat_frames,
            "blocks": blocks,
            "active_turns_after_wait": active,
        },
        checks=checks,
        judge_evidence=None,  # deterministic scenario — no judge
    )


scenario = Scenario(
    id="C3",
    name="默认不接话（注意力分离）",
    description="不 @芝士 的闲聊，断言零 AI 活动（纯平台侧，无 judge）",
    run=run,
    rubric=None,
    timeout_s=180.0,
)
