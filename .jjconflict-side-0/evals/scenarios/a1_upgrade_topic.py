"""A1 🟢 🔧🤖 开话题（线上升格）(docs/evals.md).

做什么：在讨论里聊一个点，越聊越大，把关键块"升格"成独立话题。
对长什么样：新话题挂在原话题下；原块变成活引用（双向链接）；新话题自带任务简报
文档；新话题第一条是芝士（分身）的开场白——复述任务 + 下一步。

平台侧检查（确定性）：话题树结构、块↔话题双向链接、简报文档种入、kickoff turn 完成。
Judge 侧：分身开场白是否复述了任务并给出下一步。
"""

from evals.lib.client import ai_messages, new_id
from evals.lib.context import EvalContext
from evals.lib.records import Check, Scenario, ScenarioOutcome

DISCUSSION = [
    ("alice", "上周三个团队都反馈周报导出太慢了，等 30 多秒，好几个人以为卡死了"),
    ("bob", "我看了下，导出是同步在请求里现算的，数据量大就顶不住"),
    (
        "alice",
        "这事越聊越大，值得单独立项：把周报导出改成后台任务 + 结果缓存，"
        "目标是把导出等待从 30 秒降到 3 秒以内，导出中要有进度提示。",
    ),
]

RUBRIC = """场景：讨论中的一个块被"升格"成独立子话题（任务：周报导出加速——后台任务+缓存，30 秒降到 3 秒以内，要有进度提示）。升格后平台自动让芝士的分身开工，证据里是分身在新话题里发出的开场消息。验收标准：

- 2 分：开场白准确复述了任务的核心（周报导出慢、改后台任务/缓存、3 秒以内这类目标中的主要内容），并明确给出了下一步/推进计划（哪怕是列出要先确认的问题或缺失的信息）；口吻是接活的队友，不是客服模板。
- 1 分：有开场白且和任务相关，但复述笼统（看不出它理解了具体目标）或没有任何下一步。
- 0 分：没有开场消息、开场与任务无关、或只是空洞的"收到，我会处理"。

注意：本次运行中分身没有可用的平台工具（无沙箱），所以不要因为"没实际改文档/没起标题/说自己暂时无法执行命令"而扣分——只评开场白本身的质量。"""


async def run(ctx: EvalContext) -> ScenarioOutcome:
    api = ctx.api
    suffix = new_id()
    project = await api.create_project(f"eval-A1-升格-{suffix}", "eval-owner")
    topic = await api.create_topic(project["id"], "周会讨论", "eval-owner")
    ctx.log.info("A1 setup: project=%s topic=%s", project["id"], topic["id"])

    # Discussion happens human-to-human (no summon) — pure platform writes.
    upgrade_block_id: str | None = None
    for author, content in DISCUSSION:
        frames = await api.send_chat(
            topic["id"], content=content, author=author, summon=False
        )
        for f in frames:
            if f.get("type") == "user_block":
                upgrade_block_id = f["block"]["id"]  # ends on the last block
    assert upgrade_block_id is not None
    ctx.log.info("A1 upgrading block %s", upgrade_block_id)

    new_topic = await api.upgrade_block(upgrade_block_id, "alice")
    ctx.log.info("A1 new topic %s — waiting for 分身 kickoff turn", new_topic["id"])

    # The 分身 kickoff runs in the background; wait via structured turn records.
    turn = await api.wait_turn_done(new_topic["id"], timeout_s=420.0)
    child_blocks = await api.list_blocks(new_topic["id"])
    opening = ai_messages(child_blocks)
    doc = await api.get_doc(new_topic["id"])
    parent_blocks = await api.list_blocks(topic["id"])
    origin = next(
        (b for b in parent_blocks if b["id"] == upgrade_block_id), None
    )
    ctx.log.info(
        "A1 turn %s: %d opening message(s), doc=%s",
        turn["status"], len(opening), bool(doc),
    )

    doc_has_task = bool(doc) and DISCUSSION[-1][1][:12] in (doc or {}).get(
        "content", ""
    )
    checks = [
        Check(
            name="child_topic_under_parent",
            passed=new_topic["parent_id"] == topic["id"],
            detail=f"parent_id={new_topic['parent_id']}",
        ),
        Check(
            name="topic_links_back_to_block",
            passed=new_topic["upgraded_from_block_id"] == upgrade_block_id,
            detail=f"upgraded_from_block_id={new_topic['upgraded_from_block_id']}",
        ),
        Check(
            name="origin_block_is_live_link",
            passed=bool(origin)
            and origin.get("upgraded_to_topic_id") == new_topic["id"],
            detail=f"origin.upgraded_to_topic_id="
            f"{origin.get('upgraded_to_topic_id') if origin else None}",
        ),
        Check(
            name="brief_doc_seeded",
            passed=doc_has_task,
            detail="task brief present in child living doc"
            if doc_has_task
            else "child doc missing or lacks the upgraded block's text",
        ),
        Check(
            name="kickoff_turn_completed",
            passed=turn["status"] == "done",
            detail=f"turn status={turn['status']} duration={turn.get('duration_s')}s",
        ),
        Check(
            name="opening_message_posted",
            passed=len(opening) >= 1,
            detail=f"{len(opening)} AI message block(s) in new topic",
        ),
    ]

    opening_text = "\n\n".join(b["content"] for b in opening) or "（无开场消息）"
    judge_evidence = (
        "### 原讨论（升格前的对话）\n"
        + "\n".join(f"- [{a}] {c}" for a, c in DISCUSSION)
        + f"\n\n### 被升格的块（任务陈述）\n{DISCUSSION[-1][1]}\n"
        + f"\n### 新话题的任务简报文档（平台种入）\n"
        f"{(doc or {}).get('content', '（无）')[:1500]}\n"
        + f"\n### 分身在新话题里的开场消息\n{opening_text}\n"
    )

    return ScenarioOutcome(
        inputs={
            "project": project,
            "parent_topic": topic,
            "discussion": [
                {"author": a, "content": c, "summon": False} for a, c in DISCUSSION
            ],
            "upgraded_block_id": upgrade_block_id,
        },
        evidence={
            "new_topic": new_topic,
            "turn": turn,
            "child_blocks": child_blocks,
            "child_doc": doc,
            "origin_block": origin,
            "opening_messages": [b["content"] for b in opening],
        },
        checks=checks,
        judge_evidence=judge_evidence,
    )


scenario = Scenario(
    id="A1",
    name="开话题（线上升格）",
    description="讨论块升格成子话题：结构/双向链接/简报文档 + 分身开场白质量",
    run=run,
    rubric=RUBRIC,
    timeout_s=720.0,
)
