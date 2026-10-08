"""这一间房的记忆账：每轮对一次账，以及平台自己过一遍（整理）。

从 `chat.py` 搬出来的那一簇——`_remember_memory_turn` / `_memory_scopes` /
`_sync_memory*` / `sweep_memory_dreams` / `run_memory_dream` 与那两条说进总览的话，
连同它们按房间记的四份状态。`ChatService` 上留 `sweep_memory_dreams` /
`run_memory_dream` 两行委托，调用点（`core/background.py` 的周期任务、测试）一格
没动；`_output_tokens_since` 照旧从 `chat.py` 导得出来（有一条测试在那里导它），
其余名字都从本模块取。

**四份状态只在这里读写**，它们原先是 `ChatService` 构造函数里的四个字段：
`_turns`（每个座位这一轮署谁的名、算谁的 private）、`_dreams`（正在跑整理的房间）、
`_refusals`（对账时挡下来的删除）、`_syncs`（每间房的对账锁）。谁在读它们，只由
这一簇自己回答，所以它们跟着这一簇走。

对账走的是会话那条通道（`ComputePool.report_to`），所以这个对象由 `ChatService`
自己装上去，不是外面传进来的。库里要用的几件协作者（会话工厂、算力池、网关、网关
锁、基础提示词）走构造入参，房间锁、模型配置与原生输入登记仍由
`ChatService` 提供——见 `_MemoryHost`。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.compute import ComputePool
from app.domain.agent.dream_usage import drain_dream_spend, record_dream_usage
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.prompt import build_session_opening, build_system_prompt
from app.domain.agent.live_work import LiveWork
from app.domain.agent.platform_notices import (
    EVENT_MEMORY_CHANGED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.queries import (
    _agent_handle,
    _dream_refusal_phrase,
    _say_memory_change,
)
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.service import AgentResult, AgentUsage
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.work_policy import resolve_compute_id
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import of_room, room_column
from app.domain.delivery.input_identity import InputEffects, InputRegistrar
from app.domain.memory import dream
from app.domain.memory.dream_prompt import dream_prompt
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import memory_index, private_owners
from app.domain.memory.models import MemoryDreamRunStatus
from app.domain.memory.session import apply_tree, read_tree
from app.domain.project.forge import binding_for_project
from app.domain.project.repositories import ProjectRepository
from app.domain.run_record.service import record as keep_record
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.models import ResourceUsage

logger = logging.getLogger(__name__)

#: How many rooms keep the memory-turn bookkeeping below. It is a cache of the
#: last turn's speakers, nothing reads an entry older than the turn it belongs
#: to, and a bounded map is what keeps a long-lived process from growing one
#: entry per room it has ever seen.
MEMORY_TURNS_KEPT = 512


class _MemoryHost(Protocol):
    """这条路的收件人：``ChatService`` 上留在原地的协作者。

    跑整理是**一轮真会话**（`runtime.send` 后读 `runtime.reading`），会话那套机件
    里还有一件跟着这一簇走不了：``_model_kwargs`` 内部要问项目的档位与队友配置。
    它还有别的调用方，方法在 ``ChatService`` 上原样留着。原生输入登记也由 host
    提供，空效果仍须登记输入身份。房间锁是纯状态，收在 ``live_work.LiveWork`` 里
    （那里的 ``lock_for``），按显式协作者传进来。
    本模块声明自己会问哪些，类型在调用点核对；这里只列签名，不写实现。

    库里其余几件（会话工厂、算力池、网关、网关锁、基础提示词、``live``）不在这里：
    它们是注入进来的协作者，按 ``gateway_usage._model_kwargs`` 的口径走显式入参。
    """

    def _input_registrar(self, effects: InputEffects) -> InputRegistrar: ...

    async def _model_kwargs(
        self,
        project_id: uuid.UUID,
        provider: RoomSessions | None,
        topic_id: uuid.UUID | None = None,
        *,
        platform: bool = False,
    ) -> tuple[dict, str]: ...


@dataclass(frozen=True)
class _Turn:
    """一个座位最近那一轮的记忆账。"""

    room_id: uuid.UUID
    acting: str
    speakers: tuple[str, ...]


class MemoryLedger:
    """记忆的对账与整理。见模块开头。"""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker,
        compute: ComputePool,
        gateway: LlmGateway | None,
        gateway_lock: asyncio.Lock,
        base_prompt: str,
        host: _MemoryHost,
        live: LiveWork,
    ) -> None:
        self._sessions = sessions
        self._compute = compute
        self._gateway = gateway
        self._gateway_lock = gateway_lock
        self._base_prompt = base_prompt
        self._host = host
        self._live = live
        # 每一个座位（对话, agent）这一轮的记忆账：在哪间房、署谁的名、算谁的
        # private（组装那一轮时记下，见 `remember_turn`）。两个对账时刻手上只有
        # 那个座位的会话，所以这份点名只能从别的时刻留下来。没记过的座位按「只
        # 有 team」对、署 system。
        self._turns: dict[tuple[uuid.UUID, str], _Turn] = {}
        # 正在跑整理的那几间房（一场整理一次，跑完就撤）。它只改一件事：这一轮
        # 结束时的对账多问一句「这次是不是要删掉一大半」——见 `sync_once`。
        # 会话自己有一条同样的兜底（`tree._too_many_to_delete`），但那条只会把
        # 删除放回去，不会说出「这一次不算数」；整理要的是后者。
        self._dreams: set[uuid.UUID] = set()
        # 上面那几间房里，对账时挡下来的删除（topic → 那句话）。跑整理的那个函数
        # 读走它，写进 `memory_dream_runs`，然后清掉。
        self._refusals: dict[uuid.UUID, str] = {}
        # 同一间房的对账一次只跑一场（`sync`）。
        self._syncs: dict[uuid.UUID, asyncio.Lock] = {}

    def remember_turn(
        self,
        room_id: uuid.UUID,
        seat: tuple[uuid.UUID, str],
        *,
        acting: str,
        speakers: tuple[str, ...],
    ) -> None:
        """记下一个座位（对话, agent）这一轮的记忆账：署谁的名、算谁的 private。

        组装一轮的时候才知道这两件事（`_assemble_turn`），而对账的两个时刻（输入
        之前、这一轮结束之后）手上只有那个座位的会话。记的是刚才 `memory_index`
        读过的那几个人，所以「注入里看得见的」和「铺到会话目录里的」是同一批。
        """
        self._turns.pop(seat, None)
        self._turns[seat] = _Turn(room_id, acting, speakers)
        while len(self._turns) > MEMORY_TURNS_KEPT:
            del self._turns[next(iter(self._turns))]

    def _scopes(self, room_id: uuid.UUID) -> list[tuple[MemoryFileScope, str | None]]:
        """这一次对账要点名的那几棵树：team 一份，这间房里每个座位最近一轮的发
        言人一人一份 private。

        点过名的作用域才是这一次对账的范围（`memory.session` 的开头那一段）：
        没点到的 private 既不铺也不收，别人的偏好不会被这一间房的一次对账碰掉。
        按房间合起来，因为记忆树是一间房一棵（会话的家是那间房的）：只点这个座
        位的发言人，会把另一个座位正在用的那个人的 private 从同一棵树上收走。
        """
        speakers: dict[str, None] = {}
        for turn in self._turns.values():
            if turn.room_id == room_id:
                speakers.update(dict.fromkeys(turn.speakers))
        return [
            (MemoryFileScope.team, None),
            *((MemoryFileScope.private, handle) for handle in speakers),
        ]

    async def sync(self, session: SessionRef) -> None:
        """对一遍这个座位的会话的记忆账；同一间房的两场对账排队，不交错。

        一轮结束时，钩子要对一次账，整理那一轮收尾时自己也要当场对一次；一间房里
        几个座位的会话用的是同一棵树。两场交错时，后一场读到的是前一场提交之前的
        数据库：它把平台的旧版铺回会话，整理拿它算出的「改了哪些」也是空的，而那
        些改动其实已经落库了。
        """
        async with self._syncs.setdefault(session.topic_id, asyncio.Lock()):
            await self.sync_once(session)

    async def sync_once(self, session: SessionRef) -> None:
        """对一遍这个座位的会话的记忆账：平台这一份铺下去，会话改过的收回来。

        两个时刻问它：输入之前（让 agent 一睁眼读到的就是平台现在这一份，别人刚
        改的也在里面）和这一轮结束之后（它是在这一轮里写的，写的时候这一轮还没
        结束）。两个时刻做的是同一件事，因为对账是幂等的 —— 「上一次对过什么」在
        会话那边的基线里，不在这里的记忆里。

        一次对账是跨机的一次往返，所以中间不持有事务：先把数据库这一份读出来、
        放开，再问会话，最后在一个短事务里写回、把改动说进房间。
        """
        topic_id = session.topic_id
        scopes = self._scopes(topic_id)
        turn = self._turns.get((session.conversation_id, session.agent_handle))
        updated_by = (turn.acting if turn else "") or "system"
        async with self._sessions() as db:
            topic = await TopicRepository(db).get(topic_id)
            if topic is None:
                return
            project_id = topic.project_id
            stored = await read_tree(db, project_id, scopes)
        answer = await self._compute.memory(session, {"scopes": stored.scopes})
        if answer is None:
            # 这间房现在没有能对账的会话：没有活着的会话，或者这个 harness 的会话
            # 不落记忆文件。两种都只是「这里没有这件事」，不是失败。
            return
        if topic_id in self._dreams and (
            refusal := _dream_refusal_phrase(stored.contents, answer)
        ):
            # 整理那一轮：这次要删掉的某一个作用域超过一半、且超过 3 条，整轮作废，
            # 平台上一条都不少。会话自己那条兜底（`tree._too_many_to_delete`）已经
            # 把删除放回去了（`held`），所以这里多半只是把它说出来；两处都判，
            # 是因为会话那一侧判不了「树是新的、基线还没有」的情况。写在
            # `apply_tree` 之前，是因为它一旦返回，那些删除已经落库了。
            # 说进总览的那一句在整轮收尾时说（`run_dream`），这里只记下来。
            logger.warning("memory dream refused a bulk delete: %s", refusal)
            self._refusals[topic_id] = refusal
            return
        async with self._sessions() as db:
            change = await apply_tree(
                db,
                project_id,
                stored,
                answer,
                scopes=scopes,
                updated_by=updated_by,
            )
            if change.is_empty() and not change.refused and not change.rejected:
                # 一次对账大部分时候答的是这个。什么都没变就什么都不说：这条事
                # 件是给人扫一眼的，而每一轮都发一条「没变」等于把它淹没。
                return
            # 说给写它的那段对话：一间房里的任务是它自己的一段对话。
            await _say_memory_change(
                db, project_id, change, scopes, writer_room=session.conversation_id
            )
            await db.commit()

    # --- dream：平台自己过一遍这个项目的记忆 --------------------------------

    async def sweep(self) -> dict:
        """巡检一圈：哪些项目该整理记忆了，逐个跑（`periodic_jobs` 里的一个）。

        判据是「这个项目最近花了多少」和「距上次整理多久」（`memory.dream` 模块
        开头那一段），所以先挨个项目问一句「该不该」，再动手。不该整理的项目在
        `run_dream` 的第一段就返回了，代价是一次花销求和。

        **串行**：一次整理是一轮真会话，可能跑几分钟，而同时起两个只会互相抢机
        器。这一条钟慢一拍不要紧——下一次巡检还会来，而整理晚一小时没有代价。
        """
        done: list[str] = []
        failed: list[str] = []
        async with self._sessions() as session:
            # An archived project runs nothing, a dream (a real turn) included.
            project_ids = [
                p.id
                for p in await ProjectRepository(session).list_all()
                if p.archived_at is None
            ]
        for project_id in project_ids:
            try:
                answer = await self.run_dream(project_id=project_id)
            except Exception:
                # 一个项目的整理失败不该停掉别的项目：这条 job 是它们唯一的入口。
                logger.exception("memory dream failed project=%s", project_id)
                failed.append(str(project_id))
                continue
            status = str(answer.get("status") or "")
            if status in ("completed", "refused"):
                done.append(f"{project_id}:{status}")
        return {"dreams": len(done), "failed": len(failed)}

    async def run_dream(self, *, project_id: uuid.UUID) -> dict:
        """跑一次记忆整理（dream）；不该跑就什么都不做。

        判据、锁、拒绝执行的判据和那两行账都在 `app.domain.memory.dream` 里。这里
        做的是它做不了的那一半：读这个项目的花销和房间记录、把这一轮派到项目默认
        芝士的会话上、把结果收回来。

        **派法**是 `platform_work` + `send`（结论 28）：整理是平台自己起的活，
        跑在这个项目默认芝士的会话上，用它自己的模型。
        """
        now = datetime.now(UTC)
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            if project.root_topic_id is None:
                raise NotFoundError("Project has no root topic")
            root_topic_id = project.root_topic_id
            config = dream.dream_settings(project.settings)
            state = await dream.state_of(session, project_id)
            since = dream.since_of(state)
            tokens = await _output_tokens_since(session, project_id, since)
            decision = dream.due_decision(
                config,
                tokens=tokens,
                last_dream_at=None if state is None else state.last_dream_at,
                now=now,
            )
            if not decision.due:
                return {
                    "status": "not_due",
                    "reason": decision.reason,
                    "tokens": tokens,
                }
            if await dream.claim(session, project_id, now=now) is None:
                return {"status": "locked"}
            run = await dream.open_run(
                session, project_id, tokens_at_start=tokens, now=now
            )
            run_id = run.id
            compute_id = resolve_compute_id(project.settings)
            agent_handle = await _agent_handle(session, root_topic_id)
            await session.commit()
        logger.info(
            "memory dream starting project=%s tokens=%s run=%s",
            project_id,
            tokens,
            run_id,
        )

        # 铺给它的那一份：team 加**这个项目全部**的 private。不是「本轮在场的几
        # 个人」——整理是唯一一个把整棵树放在一起看的时刻，漏掉一个没说过话的人，
        # 等于他的记忆没有人整理（`private_owners` 的开头那一段）。
        async with self._sessions() as session:
            owners = await private_owners(session, project_id)
            scopes: list[tuple[MemoryFileScope, str | None]] = [
                (MemoryFileScope.team, None),
                *[(MemoryFileScope.private, owner) for owner in owners],
            ]
            stored = await read_tree(session, project_id, scopes)
            rooms = await _dream_rooms(session, project_id, since=since)
            index = await memory_index(session, project_id, speaker_handles=[])
            binding = await binding_for_project(project_id, session)
            code_project = binding is not None
            await session.commit()
        prompt = dream_prompt(
            dream.briefing(stored.scopes, rooms, code_project=code_project)
        )

        self._dreams.add(root_topic_id)
        runtime = self._compute.platform_work(compute_id)
        system_prompt = build_system_prompt(
            self._base_prompt,
            "",  # 聊天说明不带：整理这件事的规矩在 prompt 里。
            # 记忆那一段照常注入：整理就是在这个会话里写记忆文件，而「一条记忆写
            # 成什么样」只有那一段说得全（文件名、frontmatter、索引行）。
            keeps_memory=keeps_memory(runtime.harness),
        )
        # 每次整理都是一条新会话，索引直接放在这一轮的消息前面。
        opening = build_session_opening(
            memory=index, keeps_memory=keeps_memory(runtime.harness)
        )
        if opening.text:
            prompt = f"{opening.text}\n\n{prompt}"
        final_text = ""
        usage: AgentUsage | None = None
        failed = False
        try:
            async with self._live.lock_for(root_topic_id):
                model_kwargs = (
                    await self._host._model_kwargs(
                        project_id, runtime, root_topic_id, platform=True
                    )
                )[0]
                dreamer = SessionRef(
                    project_id,
                    root_topic_id,
                    model_kwargs["session_agent"],
                    harness=runtime.harness,
                )
                # 这一轮点名的作用域也在这里定下来：`send` 会在输入之前按它铺一遍
                # （记忆那时才落到会话的磁盘上），一轮结束时又按它收回来。
                self.remember_turn(
                    root_topic_id,
                    (root_topic_id, dreamer.agent_handle),
                    acting=agent_handle,
                    speakers=tuple(owners),
                )
                # Read here rather than heard by the room: nobody waits on it
                # in a timeline, and the run is worth exactly as much as this
                # reading of it.
                async with runtime.reading(run_id) as events:
                    await runtime.send(
                        dreamer,
                        prompt,
                        system_prompt=system_prompt,
                        model=model_kwargs.get("model"),
                        env=model_kwargs.get("env"),
                        acting=model_kwargs.get("agent_handle"),
                        work_id=run_id,
                        on_mark=lambda _: None,
                        # A platform dream holds no blocks, but its native
                        # receipt still needs a registered identity.
                        register_input=self._host._input_registrar(InputEffects()),
                    )
                    async for event in events:
                        if isinstance(event, AgentUsage):
                            usage = event
                        elif isinstance(event, AgentResult):
                            final_text = event.text
                            failed = event.is_error
                            if event.usage is not None:
                                usage = event.usage
                # 收回来。自己再对一次账，不等那一侧的回调：这一轮的结果就在眼前，
                # 而「整理到底成了没有」要一个当场的答案（对账幂等，回调先跑过也
                # 只会是一次空账）。
                await self.sync(dreamer)
        finally:
            # 撤掉整理这一轮的标记：它只在这一轮里有效，留着会把下一轮普通对话也
            # 按「删多了就整轮作废」处理。
            self._dreams.discard(root_topic_id)
        refusal = self._refusals.pop(root_topic_id, None)

        async with self._sessions() as session:
            after = await read_tree(session, project_id, scopes)
            # 「改了哪些文件」由前后两份树比出来，不用另一条回执：`stored` 是铺下
            # 去之前的，`after` 是收回来之后的，中间那些就是这一轮的成果。
            changed = sorted(
                path
                for path in set(stored.contents) | set(after.contents)
                if stored.contents.get(path) != after.contents.get(path)
            )
            state = await dream.state_of(session, project_id)
            if state is not None:
                if failed:
                    await dream.release(session, state)
                else:
                    await dream.finish(session, state, now=datetime.now(UTC))
            status = (
                MemoryDreamRunStatus.failed
                if failed
                else (
                    MemoryDreamRunStatus.refused
                    if refusal
                    else MemoryDreamRunStatus.completed
                )
            )
            if refusal:
                summary = f"拒绝执行：{refusal}"
            elif failed:
                summary = "这一轮没跑成"
            else:
                summary = dream.clip(final_text, 1000)
            run = await dream.run_of(session, run_id)
            if run is not None:
                await dream.close_run(
                    session,
                    run,
                    status=status,
                    summary=summary,
                    files=[] if refusal else changed,
                    now=datetime.now(UTC),
                )
            await record_dream_usage(
                session,
                project_id=project_id,
                root_topic_id=root_topic_id,
                usage=usage,
                turn_id=run_id,
            )
            await session.commit()
        logger.info(
            "memory dream %s project=%s changed=%s",
            status.value,
            project_id,
            len(changed),
        )
        await drain_dream_spend(
            self._sessions,
            self._gateway,
            self._gateway_lock,
            project_id,
            root_topic_id,
            run_id,
        )
        team_changed = [path for path in changed if path.startswith("team/")]
        if status is MemoryDreamRunStatus.completed and team_changed:
            await self._say_dream(project_id, team_changed)
        if refusal:
            await self._say_dream_refused(project_id, run_id)
        return {
            "status": status.value,
            "tokens": tokens,
            "files": changed,
            "summary": final_text,
        }

    async def _say_dream(self, project_id: uuid.UUID, changed: list[str]) -> None:
        """整理跑完了：在项目总览的现场里记一条 team 改了哪几条（运行记录）。

        谁的名都不点：一条记忆是 agent 写下的一份观察，没有人在等它（`who`
        是 platform，投递那一层因此发不出收件人）。改动的 diff 由对账那条路自己说
        （`_say_memory_change`，team 的进总览、某个人的 private 只进他的私聊），这
        一条说的是**这一次整理本身**动了 team 的哪些文件。

        总览是全项目都看得见的房间，所以只说 team：某个人 private 里的文件名也是
        他的内容。整理的人自己写的那段交代不进来——它是看着所有人的 private 写的，
        留在 `memory_dream_runs.summary` 里。
        """
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or project.root_topic_id is None:
                return
            await keep_record(
                session,
                conversation_id=project.root_topic_id,
                content=say("memoryDreamChanged", count=len(changed)),
                meta=notice(
                    EVENT_MEMORY_CHANGED,
                    severity=SEVERITY_INFO,
                    who=WHO_PLATFORM,
                    detail="\n".join(f"- `{path}`" for path in changed),
                    detail_label=say("labelWhichChanged"),
                ),
            )
            await session.commit()

    async def _say_dream_refused(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        """整理要删掉一大半，整轮作废：说进总览。

        这条必须说话，因为它说的是一次**什么都没发生**：记忆一条都没少，而人会
        以为整理跑过了。说给谁听也是这次的一部分——没人被点名（`who=platform`），
        要动手的是人：去看那棵树到底怎么了。

        拦下的是哪个作用域、哪几条不在这里说：那可能是某个人 private 里的文件，而
        总览全项目都看得见。它们记在 `memory_dream_runs` 这一条的 summary 里。
        """
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or project.root_topic_id is None:
                return
            await announce(
                session,
                place_id=project.root_topic_id,
                content=say("memoryDreamRefused"),
                meta=notice(
                    EVENT_MEMORY_CHANGED,
                    severity=SEVERITY_WARN,
                    who=WHO_PLATFORM,
                    detail=say("memoryDreamRefusedDetail", run_id=str(run_id)),
                    detail_label=say("labelWhyStopped"),
                ),
            )
            await session.commit()


async def _output_tokens_since(
    session: AsyncSession, project_id: uuid.UUID, since: datetime
) -> int:
    """`since` 之后这个项目花了多少输出 token，不含整理自己那些。

    量的东西是「这个项目最近写了多少」（见 `memory.dream` 的开头）：记忆是会话的
    副产品，写得越多越可能已经乱到值得梳一遍。`kind` 按前缀剔掉整理那一轮自己
    花的——网关的用量是延迟落库的，一条晚到的整理用量只有 kind 认得出来。
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(ResourceUsage.output_tokens), 0)).where(
            ResourceUsage.project_id == project_id,
            ResourceUsage.created_at > since,
            ResourceUsage.kind.notlike(f"{dream.DREAM_KIND}%"),
        )
    )
    return int(total or 0)


async def _dream_rooms(
    session: AsyncSession, project_id: uuid.UUID, *, since: datetime
) -> list[str]:
    """上次整理之后有新内容的房间：标题、实况文档、这段时间的发言。

    按最后动静排序取前几间。一间房上一整个窗口一个字都没说过，进来只是噪音——
    整理要的是「这段时间发生了什么」，不是「这个项目有哪些房间」。
    """
    newest = func.max(Block.created_at).label("newest")
    room = room_column(Block.conversation_id).label("room")
    rows = (
        await session.execute(
            select(room, newest)
            .where(Block.project_id == project_id, Block.created_at > since)
            .group_by(room)
            .order_by(newest.desc())
            .limit(dream.ROOMS_LIMIT)
        )
    ).all()
    out: list[str] = []
    for topic_id, _newest in rows:
        topic = await TopicRepository(session).get(topic_id)
        if topic is None:
            continue
        lines = [f"### <#{topic_id}> {topic.title}"]
        spoken = list(
            await session.scalars(
                select(Block)
                .where(
                    of_room(Block.conversation_id, topic_id),
                    Block.kind == BlockKind.message,
                    Block.created_at > since,
                )
                .order_by(Block.created_at.desc())
                .limit(dream.ROOM_LOG_LINES)
            )
        )
        if spoken:
            body = "\n".join(
                f"- {block.author}：{dream.clip(block.content, dream.ROOM_LINE_MAX)}"
                for block in reversed(spoken)
            )
            lines.append("这段时间的发言：\n" + dream.clip(body, dream.ROOM_LOG_MAX))
        out.append("\n\n".join(lines))
    return out
