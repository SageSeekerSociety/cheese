"""`converse`: start the session this turn's context asks for, and stream it.

`prepare` hands this half a `TurnContext` and nothing is read again: the system
prompt, the session opening and this backend's real ceiling are all built from
what was read once, in the transaction that ended before any of this runs.

The body, with its reasoning, moved here verbatim from `preparation`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.notices import blocked_report_reminder
from app.domain.agent.harness.prompt import (
    UNTITLED_TASK,
    build_session_opening,
    platform_prompt,
    publication_prompt,
)
from app.domain.agent.mcp_notice import unconnected_mcp
from app.domain.agent.prompt import (
    _resume_notice,
    _sandbox_limits,
    _session_opening_lines,
)
from app.domain.agent.room.system_prompt import session_system_prompt
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.turn.intake.context import TurnBail
from app.domain.agent.turn.steps.prepared import PreparedSend
from app.domain.agent.turn.steps.send import send_prepared
from app.domain.agent_session.services import AgentSessionService
from app.domain.user.services import language_of

if TYPE_CHECKING:
    from app.domain.agent.turn.intake import preparation as pkg

# The whole turn's intake logs under the facade's name, split or not: whoever
# sets a level or greps for it sees one chain, not one forwarded line.
logger = logging.getLogger("app.domain.agent.turn.intake.preparation")


async def converse(
    prep: pkg.TurnPreparation,
    *,
    topic_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID,
    author: str,
    user_block_id: uuid.UUID | None,
    is_resume: bool = False,
    continuation_id: uuid.UUID | None = None,
    platform_turn: bool = False,
    delivery_id: uuid.UUID | None = None,
    recipient_instance_id: uuid.UUID | None = None,
) -> AsyncIterator[dict]:
    preparation_started = time.monotonic()
    prepared = await prep.prepare(
        topic_id=topic_id,
        content=content,
        turn_id=turn_id,
        user_block_id=user_block_id,
        platform_turn=platform_turn,
        # 周期任务那一轮从这一笔投递上认主人（`turn_speakers`）。
        delivery_id=delivery_id,
        recipient_instance_id=recipient_instance_id,
    )
    logger.info(
        "chat_preparation_timing topic=%s turn=%s phase=assembled "
        "elapsed_ms=%.3f unix_ms=%.3f",
        topic_id,
        turn_id,
        (time.monotonic() - preparation_started) * 1000,
        time.time() * 1000,
    )
    if isinstance(prepared, TurnBail):
        for frame in prepared.frames:
            yield frame
        return
    # Unpacked into the names the rest of this function already used, rather
    # than read through `prepared.` throughout: what follows is unchanged,
    # and a move that also rewrote seven hundred lines of references would
    # not be reviewable as the inert one it is.
    acting_agent = prepared.acting_agent
    doc_text = prepared.doc_text
    overview_doc_text = prepared.overview_doc_text
    memory = prepared.memory
    needs_place = prepared.needs_place
    prior_progress = prepared.prior_progress
    project_id = prepared.project_id
    prompt_text = prepared.prompt_text
    provider = prepared.provider
    resume_session_id = prepared.resume_session_id
    role = prepared.role
    roster = prepared.roster
    topic_refs_for_prompt = prepared.topic_refs_for_prompt
    artifact_refs = prepared.artifacts
    teaching = prepared.teaching
    untitled = prepared.untitled

    # Tells AgentWorkRunner's outer wall-clock wrap (runtime.py) to reschedule
    # to this backend's real ceiling instead of the generic
    # `agent_turn_timeout_s` (turn 活跃度检测). It is the HARNESS's number:
    # how long a silence may last before it means something is wrong depends
    # on what is producing the output, not on the machine underneath it.
    runtime = provider
    yield {"type": "turn_ceiling", "seconds": runtime.hard_ceiling_s}
    # 队友自己带的技能正文接在平台那份指引后面（`context.carried_skills`）：
    # 空串时 system prompt 与只有平台指引时逐字节相同。
    skills = (
        f"{prep.skills}\n\n---\n\n{prepared.skill_bodies}"
        if prepared.skill_bodies
        else prep.skills
    )
    system_prompt = session_system_prompt(
        prep.base_prompt,
        skills,
        needs_place=needs_place,
        has_doc=doc_text is not None,
        role=role,
        harness=runtime.harness,
        name=prepared.agent.display_name,
    )
    opening = build_session_opening(
        thread=prepared.thread_context,
        tasks=prepared.thread_tasks,
        task=prepared.task_rules,
        doc=doc_text,
        memory=memory,
        # 已停用的队友不进这份名单：这一段教的是「要让某人去做事，在他名字前
        # 加 @」，而一个停用了的实例没有人在驱动它——@ 它等于把活扔进一个没人
        # 接的地方。@ 解析和通知那几路照旧走全量的 `roster`：老房间里已经在的
        # 它仍要 @ 得到，停用挡的是新的活，不是已经接手的。
        roster=[m for m in roster if m["active"]],
        topics=topic_refs_for_prompt,
        artifacts=artifact_refs,
        overview_doc=overview_doc_text,
        overview_doc_id=prepared.overview_doc_id,
        teaching=teaching,
        stages=prepared.stages,
        package=prepared.task_package,
        environment=_session_opening_lines(
            unconnected_mcp=(
                await unconnected_mcp(
                    prep.sessions,
                    project_id,
                    prepared.room_id,
                    acting_agent,
                    inner_id=prepared.inner_id,
                )
                if needs_place
                else ()
            ),
            progress=prior_progress,
            sandbox=_sandbox_limits(provider),
            # A resumed conversation already holds what was said in it.
            earlier_messages=(
                prepared.earlier_messages if resume_session_id is None else 0
            ),
        ),
        keeps_memory=keeps_memory(runtime.harness),
    )
    async with prep.sessions() as session:
        told = await AgentSessionService(session).told(
            topic_id, prepared.agent.handle, harness=prepared.harness
        )
        # 平台自己说给模型的那几句按提问的人的语言走（`user.language`）：一次
        # 掉线重启的会话先在中文房间里读到英文的两句，接着就把每一步的说明都写成了
        # 英文。查不到这个人时是 None，那就说存下来的中文。
        language = await language_of(session, author)
    if untitled:
        # 每一轮都提醒，直到起了名：哪一轮才弄清要做什么，事先不知道。
        prompt_text = f"{platform_prompt(UNTITLED_TASK)}\n\n{prompt_text}"
    if is_resume:
        prompt_text = f"{platform_prompt(_resume_notice())}\n\n{prompt_text}"
    # 人说的话叫起来的那一轮（和 `send.py` 里 `summoned` 同一个判据）：重发、平台
    # 自己叫的都不算，那时没有人回过它，问它「还卡不卡」问的不是这回事。
    summoned = user_block_id is not None and not is_resume and not platform_turn
    if prepared.blocked_report and summoned:
        # 「卡住了」通常是它在轮次末尾报的；有人回话开的那一轮正是它该重新报一次
        # 的时候。只问，不替它改：人回话不等于解除了卡住。
        prompt_text = (
            f"{platform_prompt(blocked_report_reminder(language))}\n\n{prompt_text}"
        )
    prompt_text = publication_prompt(prompt_text, language)
    logger.info(
        "chat_preparation_timing topic=%s turn=%s phase=prompt_built "
        "elapsed_ms=%.3f unix_ms=%.3f",
        topic_id,
        turn_id,
        (time.monotonic() - preparation_started) * 1000,
        time.time() * 1000,
    )

    # Compute: a provider owns the per-topic sandbox + execution (spec §9.1).
    # In a private chat the turn's memory is the owner's own, so the sandbox
    # comes up in the personal scope (spec §8.4). The provider runs a plain
    # model turn when no Docker (tests).
    model_kwargs, route = await prep.model_options(
        project_id,
        provider,
        prepared.room_id,
        agent=prepared.agent,
        acting_agent=prepared.acting_agent,
    )
    logger.info(
        "chat_preparation_timing topic=%s turn=%s phase=model_ready "
        "elapsed_ms=%.3f unix_ms=%.3f",
        topic_id,
        turn_id,
        (time.monotonic() - preparation_started) * 1000,
        time.time() * 1000,
    )

    async def activate(ref: SessionRef) -> None:
        await prep.compute.activate(ref, runtime)

    async for frame in send_prepared(
        prep.sessions,
        prep.live,
        prep.send_effects,
        PreparedSend(
            project_id=prepared.project_id,
            room_id=prepared.room_id,
            inner_id=prepared.inner_id,
            acting_agent=prepared.acting_agent,
            agent_handle=prepared.agent.handle,
            agent_pool=prepared.agent_pool,
            roster=prepared.roster,
            topic_refs=prepared.topic_refs,
            consumed_ids=prepared.pending_ids + prepared.notice_ids,
            read_through=prepared.read_through,
            turn_images=prepared.turn_images,
            replay_notice=prepared.replay_notice,
            resume_session_id=prepared.resume_session_id,
            harness=prepared.harness,
            needs_place=prepared.needs_place,
            keeps_nothing=prepared.keeps_nothing,
            sender=runtime,
            activate=activate,
        ),
        topic_id=topic_id,
        turn_id=turn_id,
        author=author,
        content=content,
        user_block_id=user_block_id,
        is_resume=is_resume,
        platform_turn=platform_turn,
        continuation_id=continuation_id,
        delivery_id=delivery_id,
        model_kwargs=model_kwargs,
        route=route,
        prompt_text=prompt_text,
        system_prompt=system_prompt,
        opening=opening,
        told=told,
    ):
        yield frame
