"""C1 🟢 🤖 @芝士带记忆回答 (docs/evals.md).

做什么：种入几条项目记忆，然后以新成员身份 @芝士 问"项目什么状态、我能干什么"。
对长什么样：回答准确反映项目当前状态（引用真实的决策/进展），并点明依据。

平台侧检查（确定性）：记忆确实种入、turn 正常完成、有 AI 回复。
Judge 侧：回答是否实质性用上了种入的记忆、是否点明依据。
"""

from evals.lib.client import ai_messages, new_id
from evals.lib.context import EvalContext
from evals.lib.records import Check, Scenario, ScenarioOutcome

# Distinctive, non-guessable facts: if the answer reflects them, it can only
# have come from injected memory.
MEMORIES = [
    "技术选型决策：数据库用 PostgreSQL 16 + pgvector，理由是要做课程内容的"
    "相似度检索；测试环境用 SQLite。",
    "分工：博文负责推荐算法原型，笑笑负责前端（已拍板用 Vue 3），"
    "数据清洗脚本还没人认领。",
    "下一个里程碑：11 月 15 日中期答辩，要求现场演示一个能跑的推荐 demo。",
]

QUESTION = (
    "我是刚加入项目的新成员，之前的讨论都没参与。现在项目到什么状态了？"
    "技术上已经定了哪些事？我这周能帮上什么忙？"
)

RUBRIC = """场景：新成员在项目话题里 @芝士 问「项目什么状态、我能干什么」。
系统事先给芝士种入了三条项目记忆（见证据「种入的记忆」）。验收标准（对照证据里芝士的实际回答）：

- 2 分：回答实质性地用上了记忆中的事实——准确说出关键决策（如数据库选 PostgreSQL+pgvector 及其理由、前端定了 Vue 3、11 月 15 日中期答辩/演示 demo 中的至少两项），没有和记忆矛盾的编造；并且对"我能干什么"给出了基于记忆的具体建议（如认领数据清洗脚本）；回答自然点明了信息依据（如"项目记忆/之前定的"之类的口吻，不要求特定措辞）。
- 1 分：用上了部分记忆（至少一项事实准确出现），但遗漏明显、建议空泛、或依据感不足。
- 0 分：回答没有体现任何种入的记忆（泛泛而谈或让用户自己去看记录），或编造了与记忆矛盾的"事实"。"""


async def run(ctx: EvalContext) -> ScenarioOutcome:
    api = ctx.api
    suffix = new_id()
    project = await api.create_project(f"eval-C1-推荐系统-{suffix}", "eval-owner")
    topic = await api.create_topic(project["id"], "新成员答疑", "eval-owner")
    ctx.log.info("C1 setup: project=%s topic=%s", project["id"], topic["id"])

    for fact in MEMORIES:
        await api.seed_memory(project["id"], fact)
    seeded = await api.list_memory(project["id"])
    ctx.log.info("C1 seeded %d memories", len(seeded))

    frames = await api.send_chat(
        topic["id"],
        content=QUESTION,
        author="xinyu",
        summon=True,
        turn_timeout_s=420.0,
    )
    turn = await api.wait_turn_done(topic["id"], timeout_s=30.0)
    blocks = await api.list_blocks(topic["id"])
    replies = ai_messages(blocks)
    ctx.log.info(
        "C1 turn %s: %d frames, %d AI replies", turn["status"], len(frames), len(replies)
    )

    checks = [
        Check(
            name="memory_seeded",
            passed=len(seeded) == len(MEMORIES),
            detail=f"{len(seeded)}/{len(MEMORIES)} entries visible via /api/memory",
        ),
        Check(
            name="turn_completed",
            passed=turn["status"] == "done",
            detail=f"turn status={turn['status']} duration={turn.get('duration_s')}s",
        ),
        Check(
            name="ai_replied",
            passed=len(replies) >= 1,
            detail=f"{len(replies)} AI message block(s)",
        ),
        Check(
            name="summon_acked",  # 收到请求先 ✅ (spec §14.2, platform-side)
            passed=any(f.get("type") == "reaction" for f in frames),
            detail="✅ reaction frame observed" if any(
                f.get("type") == "reaction" for f in frames
            ) else "no reaction frame",
        ),
    ]

    reply_text = "\n\n".join(b["content"] for b in replies) or "（无 AI 回复）"
    judge_evidence = (
        "### 种入的记忆（芝士应当知道的事实）\n"
        + "\n".join(f"- {m}" for m in MEMORIES)
        + f"\n\n### 新成员的提问（@芝士）\n{QUESTION}\n"
        + f"\n### 芝士的实际回答\n{reply_text}\n"
    )

    return ScenarioOutcome(
        inputs={
            "project": project,
            "topic": topic,
            "seeded_memories": MEMORIES,
            "question": {"author": "xinyu", "content": QUESTION, "summon": True},
        },
        evidence={
            "turn": turn,
            "ws_frames": frames,
            "blocks": blocks,
            "ai_replies": [b["content"] for b in replies],
        },
        checks=checks,
        judge_evidence=judge_evidence,
    )


scenario = Scenario(
    id="C1",
    name="@芝士带记忆回答",
    description="种入项目记忆后 @芝士 提问，判回答是否用上记忆并点明依据",
    run=run,
    rubric=RUBRIC,
    timeout_s=600.0,
)
