"""``prepare``: one turn's facts, read in one transaction.

``TurnPreparation.prepare`` reads the room, the recipient, memory and the
machine and hands the result on as ``TurnContext``; that read IS this module.
The two questions that stop a turn before its prompt exists — nothing on this
machine runs it, or the tier policy answers with a proposal — are `gates`, and
the text the turn is handed is built by `prompt.build_turn_prompt`.

`held_blocks` comes in through `prep._held_blocks` rather than imported here:
the facade is the one place a test double for it is seen, and a submodule
importing its own facade at runtime is the cycle this shape exists to avoid.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from app.auth.audience import seats_only
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.carried_bodies import carried_bodies
from app.domain.agent.harness.prompt import task_section
from app.domain.agent.prompt import _topic_ref_lists, offered_attachments
from app.domain.agent.queries import _acting_handle, _agent_at, require_pinned_seat
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.room.thread_context import thread_context as _thread_context
from app.domain.agent.room.thread_context import thread_tasks as _thread_tasks
from app.domain.agent.turn.intake.context import (
    TurnBail,
    TurnContext,
    carried_skills,
    human_private_owner,
    living_doc_text,
)
from app.domain.agent.turn.intake.preparation.gates import (
    check_model_policy,
    no_provider_bail,
)
from app.domain.agent.turn.intake.preparation.prompt import build_turn_prompt
from app.domain.agent.turn.intake.rooms import _is_dm, room_roster
from app.domain.agent.turn.state.inputs import _addressed_to
from app.domain.agent.turn_speakers import is_routine_run, private_memory_for
from app.domain.agent_instance.own import current_role
from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import Block
from app.domain.block.queries import (
    block_by_id,
    earlier_message_count,
    inputs_after,
    notices_after,
)
from app.domain.deliverables import artifacts as project_artifacts
from app.domain.living_doc.overview import overview_document
from app.domain.memory.files_store import memory_index
from app.domain.project.overview import project_brief, render_overview
from app.domain.project.reads import load_project
from app.domain.room_task import naming
from app.domain.room_task.models import TaskProgress, TaskStatus, TaskTitleSource
from app.domain.room_task.place import PlaceResolver
from app.domain.task import package
from app.domain.task import teaching as teaching_context
from app.domain.task.claims import stages_for_project
from app.domain.thread.services import thread_opening
from app.domain.topic.models import TopicStatus
from app.domain.topic.reads import conversation_progress, project_topics
from app.domain.usage.alerts import agent_note

if TYPE_CHECKING:
    from app.domain.agent.turn.intake import preparation as pkg

logger = logging.getLogger("app.domain.agent.turn.intake.preparation")


async def prepare(
    prep: pkg.TurnPreparation,
    *,
    topic_id: uuid.UUID,
    content: str,
    turn_id: uuid.UUID,
    user_block_id: uuid.UUID | None,
    platform_turn: bool = False,
    delivery_id: uuid.UUID | None = None,
    recipient_instance_id: uuid.UUID | None = None,
) -> TurnContext | TurnBail:
    """Read the recipient, history, memory, machine and prompt in one transaction.

    Return a ``TurnBail`` with room frames if no answer is pending or the
    machine is still being built; neither outcome starts an executor.
    """
    started = time.monotonic()
    phases_ms: dict[str, float] = {}
    # --- tx1: load topic + history, load memory ---
    async with prep.sessions() as session:
        # WHERE this turn runs: a room's own line, or a task's conversation.
        place = await PlaceResolver(session).conversation(topic_id)
        if place is None:
            raise NotFoundError(say("topicNotFound"))
        topic, task = place.room, place.task
        if topic.status == TopicStatus.archived:
            raise prep.refuse(say("roomArchivedUnarchiveFirst"))

        # 这一轮读什么：这条对话里、这个座位上次读到之后的所有参与者消息，
        # 谁说的都算，说给谁的也算。每个座位各有一份游标（`agent_reads`），
        # 所以别的队友说过的话对它也是「别人说的」，和人在房间里看到的一样；
        # 一条消息不再只有一个全局的「谁读过」标记。
        #
        # This conversation's OWN line: a room's history leaves out its
        # tasks' conversations, and a task's is only its own. A 支线's root
        # message and files stay in the main line; `_read_window` reads them in
        # on the seat's first turn, and the 支线 section below is built from it.
        opening = await thread_opening(session, place.conversation_id)
        root = opening[0] if opening else None
        addressed = (
            await block_by_id(session, user_block_id)
            if user_block_id is not None
            else None
        )
        recipient = (addressed.meta or {}).get("agent_recipient") if addressed else None
        agents = AgentInstanceService(session)
        pinned_seat = None
        if recipient_instance_id is not None:
            recipient = {"instance_id": str(recipient_instance_id)}
            # A seat on the room's roster. A task's agent is seated when the
            # task is given to it, and `_acting_handle` below refuses one
            # that is not.
            if task is None:
                pinned_seat = await require_pinned_seat(
                    session, place.room_id, recipient_instance_id
                )
        # 收件人是消息落库时记下来的。记的时候还没有实例行的那些旧消息，
        # 「收件人是项目的芝士」和今天的解析是同一个答案。
        if recipient is None or recipient.get("instance_id") is None:
            agent = await _agent_at(session, place)
        else:
            agent = agents.resolved(
                await agents.get_in_project(
                    project_id=topic.project_id,
                    instance_id=uuid.UUID(recipient["instance_id"]),
                )
            )
        acting_agent = pinned_seat or await _acting_handle(session, topic.id, agent)
        cursor = await AgentSessionService(session).read_cursor(
            place.conversation_id, acting_agent
        )
        pending = await _read_window(
            session, place, acting_agent, cursor, opening=opening
        )
        phases_ms["history"] = (time.monotonic() - started) * 1000
        # Kept apart from `pending` on purpose: that list answers 「谁说话了」
        # for the recipient routing, the replay counter and the 「没人在等」
        # bail, and a platform notice is an answer to none of those. It only
        # rides into the prompt and gets stamped.
        notices = await notices_after(session, place.conversation_id, after=cursor)
        # Holds and waiting answers are kept per conversation, as inputs
        # are registered: a task's or a 支线's own, not its room's.
        held = await prep._held_blocks(
            session,
            project_id=topic.project_id,
            topic_id=place.conversation_id,
            recipient_handle=acting_agent,
        )
        pending = [block for block in pending if block.id not in held]
        notices = [block for block in notices if block.id not in held]
        # 这一轮要答的是点它的名、或者谁也没点的那些。别的座位的消息只是
        # 背景：它们不是这一轮的事，盖了「读过」还会让房间里以为别人被问的
        # 那句话已经有人答了。
        own_pending = [b for b in pending if _addressed_to(b, agent.handle)]
        prompt_pending_ids = [b.id for b in own_pending]
        if not own_pending and user_block_id is not None:
            # 有人召唤，但他那条消息已经被前一轮读进 prompt 了（两个人几乎同时
            # @，第一轮在锁上把两条合并答掉）。再跑一轮就是白烧一轮算力，还会
            # 走下面的 platform_prompt 兜底、把已经答过的话当成平台指令重投一
            # 遍。这里直接收工 —— 只是不跑这一轮，不碰任何排队/锁的逻辑。
            return TurnBail([{"type": "done"}])
        pending_ids = list(prompt_pending_ids)
        read_through = _newest([*pending, *notices])
        # 图片输入: every pending image is offered to the provider; whether it
        # reaches the model as a native block depends on `embeds_images`, so
        # the prompt is built below, after the provider is picked. A file
        # that is gone, or that looks like a picture and does not decode,
        # is not offered (`offered_attachments`).
        turn_images, gone_files, undecodable_files = await offered_attachments(
            session, pending, topic.project_id, place.room_id
        )

        # 私聊是名册两席的房间（结论 19）。这一轮凡是「私聊要不一样」的地
        # 方，问的都是下面两个答案之一，不再各自问一遍那个布尔。
        #
        # 一、名册上那两席，人是哪一位（席位不齐时 None）。
        private_owner = await human_private_owner(session, topic)
        # 二、这一轮要不要一双手？见 `_is_dm`：不租地点的一轮桌上只有对话、
        # 记忆和平台工具，加上会话自己那块 64 MiB 草稿区。
        needs_place = not _is_dm(topic)
        acting_agent = pinned_seat or await _acting_handle(session, topic.id, agent)
        doc_text = await living_doc_text(session, place, needs_place=needs_place)
        phases_ms["identity"] = (time.monotonic() - started) * 1000
        # private 只在一个人和芝士的私聊里带，那个人的那一份（`private_memory_for`）；
        # 别的房间只有 team。这一位同时也是这一轮对账要点名的那一位
        # （`MemoryLedger.remember_turn`），所以「告诉它去读的」和「铺到会话目录里
        # 的」是同一份。
        private_for = await private_memory_for(
            session, agent.instance_id, private_owner=private_owner
        )
        memory = await memory_index(
            session,
            topic.project_id,
            speaker=private_for,
            shared_read_only=seats_only(topic),
        )
        prep.memory.remember_turn(
            topic.id,
            (place.conversation_id, agent.handle),
            acting=acting_agent,
            speaker=private_for,
            pointed=any(section.prefix != "team" for section in memory.sections),
        )
        phases_ms["memory"] = (time.monotonic() - started) * 1000
        project = await load_project(session, topic.project_id)
        # 本周教学范围 (#8d772257), for a project that came from a course's
        # 赛题. Resolved here — in the transaction everything else the prompt
        # is built from is read in, and fresh on every turn — so a 项目集 that
        # moved on to 第 4 周 is what the NEXT session starts with. (What a
        # session already running sees is `harness.prompt`'s 生效语义.)
        #
        # A project that is not a course pays nothing for this line: no
        # 赛题, or no `teaching` on the 项目集, is no query and no prompt
        # text — not a section that renders empty.
        teaching = await teaching_context.for_project(session=session, project=project)
        # 题目的阶段与这个项目在各阶段上的状态：每一轮现读，交了、批了就跟着变。
        stages = (
            await stages_for_project(
                session, task_id=project.external_task_id, team_id=project.team_id
            )
            if project is not None and project.external_task_id is not None
            else []
        )
        task_package = await package.section_for_project(session, project)
        # 人和 agent 共同看的那一份（结论 7）：项目目标。它不是记忆，所以不走
        # 召回那条路——写它的人（或 agent）留了痕，每段对话读到的是同一份，而
        # 这正是共享记忆池做不到的两件事。
        overview_doc = (
            await overview_document(session, project.id)
            if project is not None
            else None
        )
        overview_doc_id = overview_doc.id if overview_doc is not None else None
        overview_doc_text = (
            render_overview(brief=project_brief(overview_doc.content))
            if overview_doc is not None
            else None
        )
        # Read the selected agent once so this turn's role and model agree.
        role = await current_role(session, agent.instance_id) or (
            await agents.system_prompt(agent)
        )
        # 骨架是这个项目跑的那一个（结论 28），不是这个参与者的属
        # 性。这一轮只解析这一次，往下每一处都读它：会话行的键里有骨架，两处
        # 各自解析一次就够把一条会话拆成两条。
        wanted_harness, provider = await prep._backend_for(session, agent, project)
        agent_pool = memory_pool(topic.project_id, agent)
        # Roster so 芝士 can @ real teammates (not just name them in prose).
        # 私聊里没有第三个人可点名，名册也就不进提示词——`[]` 和「没有名册这
        # 回事」在下游是两种情况（见 `HookWorkState.roster`）。问的是这间房
        # 是不是私聊，不是它此刻坐了几个人：名册还要往下走进 `announce_mentions`。
        roster = await room_roster(session, topic.project_id, topic)
        # Topic list so 芝士 can cross-reference topics with <#id> tokens.
        # 两份，故意的：`topic_refs` 是 `@标题` 的**解析表**（全量，含已归档
        # ——用户自己打 @某个归档话题也必须还能变成链接）；
        # `topic_refs_for_prompt` 只是**渲染**进 system prompt 的子集。
        # 私密频道不进别的房间的提示词：它的名字只给在里面的人看。
        listed_topics = await project_topics(session, topic.project_id)
        all_topics = [t for t in listed_topics if not t.members_only]
        topic_refs, topic_refs_for_prompt = _topic_ref_lists(
            all_topics, exclude_id=topic.id
        )
        # 产物清单：交付时点名用的那几个名字 (#1085 结论三)。不租地点的一轮里
        # 没有交付，那里连这一段都不该有；空清单和「没有清单这回事」是两种情况，
        # 前者要说话（第一次交付只能新建），后者一个字都不说，所以给的是 None。
        # 别的私密频道里交付的不进这份清单，理由同上面的话题列表
        # （`artifacts.hidden_from_room`）。
        artifact_refs = (
            [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "version": a.version,
                    "about": a.about,
                }
                for a in await project_artifacts.list_for_project(
                    session,
                    topic.project_id,
                    hidden={
                        t.id
                        for t in listed_topics
                        if t.members_only and t.id != topic.id
                    },
                )
            ]
            if needs_place
            else None
        )
        project_id = topic.project_id
        # This agent's conversation here, not just any: a room may host
        # several agents and each resumes its own (agent_session/models.py).
        # Looked up under the same key the turn that stores it writes under
        # (`_agent_at`) — reading under one key and writing under another
        # does not fail, it hands back None and starts a brand-new
        # conversation, which is the failure this whole path prevents.
        session_agent = agent
        resume_session_id = await AgentSessionService(session).resume_token(
            place.conversation_id,
            session_agent.handle,
            harness=wanted_harness,
        )
        # The platform names tasks itself (room_task/naming.py). Only where
        # it cannot — no gateway to call — is the task's own session asked
        # to, and never in a project that chose to name its tasks by hand.
        untitled = (
            task is not None
            and task.title_source == TaskTitleSource.placeholder
            and not naming.available()
            and naming.naming_mode(project.settings if project else None) == "auto"
        )
        # 进度层 (#187): the checklist the last turn left behind. Read inside
        # tx1 with everything else the prompt is built from, so no extra
        # round trip; empty list when this topic has never had one.
        progress_row = await conversation_progress(session, place.conversation_id)
        prior_progress = [
            dict(item) for item in (progress_row.items if progress_row else [])
        ]
        earlier_messages = await earlier_message_count(
            session, place.conversation_id, excluding=prompt_pending_ids
        )
        routine_run = await is_routine_run(session, delivery_id)
        thread_context = (
            await _thread_context(session, topic, root, routine_run=routine_run)
            if root is not None
            else None
        )
        thread_tasks = (
            await _thread_tasks(session, topic, root) if root is not None else None
        )
        if place.thread is not None:
            # So the main line hears when an AI teammate starts and stops
            # answering in this 支线 (`InProcessBroker.publish`).

            get_broker().activity.note_thread(place.thread.id, place.room_id)
        # Resolve the room choice, then the explicit project default.
        phases_ms["metadata"] = (time.monotonic() - started) * 1000

        # 先问这台机器上有没有可用的骨架，再过档位策略：策略那一步要解析模型，
        # 而一个没挂上的骨架一个模型都指不到，先问它就会以「没有默认模型」收场，
        # 房间读到的不是真正的原因。
        if provider is None:
            return await no_provider_bail(
                session,
                project_id=topic.project_id,
                room_id=place.room_id,
                turn_id=turn_id,
                harness=wanted_harness,
            )
        # 这一轮要占的模型在这里过项目的档位策略（结论 3 后半、结论 40 后半）。
        bail = await check_model_policy(
            session,
            provider=provider,
            project=project,
            needs_place=needs_place,
            place=place,
            agent=agent,
            harness=wanted_harness,
            acting_agent=acting_agent,
        )
        if bail is not None:
            return bail
        phases_ms["provider"] = (time.monotonic() - started) * 1000
        # 额度快用完了就告诉这一轮一句（`usage.alerts.agent_note`）。一个越界
        # 在一条对话里只说一次——这条活第一次开工的那一轮。读不出来不是这一轮
        # 的事：算额度的错不该让一轮活开不了。
        try:
            credits_note = await agent_note(
                session, topic.project_id, conversation_id=place.conversation_id
            )
        except Exception:
            logger.exception(
                "could not read the credit warning for %s", topic.project_id
            )
            credits_note = None
        prompt_text, replay_notice = await build_turn_prompt(
            session,
            place=place,
            turn_id=turn_id,
            content=content,
            platform_turn=platform_turn,
            pending=pending,
            prompt_pending_ids=prompt_pending_ids,
            notices=notices,
            provider=provider,
            recipient_handle=agent.handle,
            recipient_instance_id=recipient_instance_id,
            gone_files=gone_files,
            undecodable_files=undecodable_files,
            credits_note=credits_note,
        )
        phases_ms["committed"] = (time.monotonic() - started) * 1000
    logger.info(
        "chat_assembly_timing topic=%s turn=%s elapsed_ms=%.3f phases_ms=%s",
        topic_id,
        turn_id,
        (time.monotonic() - started) * 1000,
        phases_ms,
    )
    return TurnContext(
        room_id=place.room_id,
        inner_id=place.inner_id,
        # Except the turn that is a routine's run in a 支线: a rule its
        # owner confirmed, which keeps what it produces.
        keeps_nothing=not place.keeps_work
        and not (place.thread is not None and routine_run),
        task_rules=None
        if task is None
        else task_section(
            owner=task.owner_handle,
            contributors=task.contributor_handles or (),
            closed=task.status == TaskStatus.closed,
        ),
        # 只看还开着的活：关闭不清这条上报（`closing.close_task` 不碰它），而关掉的
        # 活还能被人叫起来说话；那时看板显示的是关闭，再叫它报状态只会被 422 拒掉。
        blocked_report=(
            task is not None
            and task.status is TaskStatus.open
            and task.progress_state is TaskProgress.blocked
        ),
        acting_agent=acting_agent,
        agent=agent,
        agent_pool=agent_pool,
        doc_text=doc_text,
        overview_doc_text=overview_doc_text,
        overview_doc_id=overview_doc_id,
        memory=memory,
        pending_ids=pending_ids,
        notice_ids=[b.id for b in notices],
        read_through=read_through,
        prior_progress=prior_progress,
        earlier_messages=earlier_messages,
        thread_context=thread_context,
        thread_tasks=thread_tasks,
        project_id=project_id,
        prompt_text=prompt_text,
        provider=provider,
        harness=wanted_harness,
        needs_place=needs_place,
        replay_notice=replay_notice,
        resume_session_id=resume_session_id,
        role=role,
        roster=roster,
        topic_refs=topic_refs,
        topic_refs_for_prompt=topic_refs_for_prompt,
        artifacts=artifact_refs,
        teaching=teaching,
        stages=stages,
        task_package=task_package,
        skill_bodies=await carried_bodies(
            session,
            topic,
            carried_skills(agent, topic, teaching.teaching.skills if teaching else ()),
        ),
        turn_images=turn_images,
        untitled=untitled,
    )


async def _read_window(
    session,
    place,
    seat: str,
    cursor: tuple[datetime, uuid.UUID] | None,
    *,
    opening: list[Block],
) -> list[Block]:
    """What this seat has not read: every participant's messages, oldest first.

    Only the seat's own TURN OUTPUT is left out — it is already in its
    transcript, and handing it back is how an agent ends up answering itself.
    Something the same seat said outside any turn (a `chat_send` made on its
    own, no turn to attribute it to) is a room message like any other and is
    read once (`test_what_an_agent_says_in_the_room_is_read_once`). A 支线's
    message and files are read in on the seat's first turn, because they live
    in the main line and its cursor (in the 支线's conversation) cannot name
    them.
    """
    window = await inputs_after(
        session,
        place.conversation_id,
        after=cursor,
        limit=settings.agent_input_window_messages,
    )
    pending = [
        block
        for block in window
        if not (block.author == seat and block.turn_id is not None)
    ]
    pending = _trim_to_chars(pending, settings.agent_input_window_chars)
    if cursor is None:
        pending = [*opening, *pending]
    return pending


def _trim_to_chars(blocks: list[Block], budget: int) -> list[Block]:
    """Keep the newest blocks that fit ``budget`` characters.

    The count cap bounds the query; this bounds the tokens. Newest wins because
    a message said after another is the one an answer can build on.
    """
    kept: list[Block] = []
    total = 0
    for block in reversed(blocks):
        size = len(block.content or "")
        if kept and total + size > budget:
            break
        kept.append(block)
        total += size
    kept.reverse()
    return kept


def _newest(blocks: list[Block]) -> tuple[datetime, uuid.UUID] | None:
    """The timeline position to record as read, or None when nothing was read."""
    if not blocks:
        return None
    block = max(blocks, key=lambda b: (b.created_at, b.id))
    return (block.created_at, block.id)
