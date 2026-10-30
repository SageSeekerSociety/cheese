"""这一间房的记忆账：每轮对一次账，以及平台自己过一遍（整理）。

`ChatService` 上留 `sweep_memory_dreams` / `run_memory_dream` 两行委托，调用点
（`agent/periodic.py` 的周期任务、测试）走那里；`_output_tokens_since` 照旧从
`chat.py` 导得出来（有一条测试在那里导它），其余名字都从本模块取。

**两份状态只在这里读写**：`_turns`（每个座位这一轮署谁的名、算谁的 private）和
`_syncs`（每间房的对账锁）。

对账走的是会话那条通道（`ComputePool.report_to`），所以这个对象由 `ChatService`
自己装上去，不是外面传进来的。整理不经过任何会话：每一份是平台在项目的整理 key 上
发的一次模型调用（`run_dream`），key 由 `ChatService` 发放——见 `_MemoryHost`。
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.audience import seats_only
from app.core import alerting, model_roles
from app.core.errors import NotFoundError
from app.domain.agent.compute import ComputePool
from app.domain.agent.dream_usage import drain_dream_spend, record_dream_usage
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.harness import SessionRef
from app.domain.agent.queries import _agent_handle, _say_memory_change
from app.domain.gateway_chat import GatewayCallError, GatewayChat
from app.domain.memory import dream
from app.domain.memory.dream_prompt import (
    PRIVATE_DREAM_SYSTEM,
    TEAM_DREAM_SYSTEM,
    private_dream_prompt,
    team_dream_prompt,
)
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore, private_owners
from app.domain.memory.models import MemoryDreamRunStatus
from app.domain.memory.session import (
    MemoryChange,
    apply_tree,
    apply_withdrawn,
    prefix_of_scope,
    read_tree,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.models import ResourceUsage

logger = logging.getLogger(__name__)

#: How many rooms keep the memory-turn bookkeeping below. It is a cache of the
#: last turn's speakers, nothing reads an entry older than the turn it belongs
#: to, and a bounded map is what keeps a long-lived process from growing one
#: entry per room it has ever seen.
MEMORY_TURNS_KEPT = 512

#: How long one consolidation call may take, and how much it may answer: the
#: files it rewrites come back whole.
DREAM_CALL_S = 300.0
DREAM_ANSWER_TOKENS = 16_000


class _MemoryHost(Protocol):
    """这条路的收件人：``ChatService`` 上留在原地的协作者。

    整理的每一份调用发在项目的整理 key 上，key 由 ``ChatService`` 发放（它还有
    别的调用方）。本模块声明自己会问哪些，类型在调用点核对；这里只列签名，不写实现。

    库里其余几件（会话工厂、算力池、网关）不在这里：它们是注入进来的协作者。
    """

    async def project_gateway_key(
        self, project_id: uuid.UUID, *, dream: bool = False
    ) -> str | None: ...


@dataclass(frozen=True)
class _ScopeDream:
    """一个作用域这一次整理的结局：改了哪些、它自己那句交代，或者为什么没写。"""

    scope: MemoryFileScope
    #: 改过的路径（带作用域前缀），删掉的也在里面。
    changed: tuple[str, ...] = ()
    #: 其中删掉的条数。
    removed: int = 0
    summary: str = ""
    #: 要删的太多、整份没写时那句话。
    refusal: str = ""
    #: 没问成（没有网关、调用失败、答回来的不是一份改动）时的原因。
    failure: str = ""


@dataclass(frozen=True)
class _Turn:
    """一个座位最近那一轮的记忆账。"""

    room_id: uuid.UUID
    acting: str
    #: 这一轮那一位（`turn_speaker`）；None 时这一轮只有 team。
    speaker: str | None


class MemoryLedger:
    """记忆的对账与整理。见模块开头。"""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker,
        compute: ComputePool,
        gateway: LlmGateway | None,
        host: _MemoryHost,
    ) -> None:
        self._sessions = sessions
        self._compute = compute
        self._gateway = gateway
        self._host = host
        # 每一个座位（对话, agent）这一轮的记忆账：在哪间房、署谁的名、算谁的
        # private（组装那一轮时记下，见 `remember_turn`）。两个对账时刻手上只有
        # 那个座位的会话，所以这份点名只能从别的时刻留下来。没记过的座位按「只
        # 有 team」对、署 system。
        self._turns: dict[tuple[uuid.UUID, str], _Turn] = {}
        # 同一间房的对账一次只跑一场（`sync`）。
        self._syncs: dict[uuid.UUID, asyncio.Lock] = {}

    def remember_turn(
        self,
        room_id: uuid.UUID,
        seat: tuple[uuid.UUID, str],
        *,
        acting: str,
        speaker: str | None,
    ) -> None:
        """记下一个座位（对话, agent）这一轮的记忆账：署谁的名、算谁的 private。

        组装一轮的时候才知道这两件事（`_assemble_turn`），而对账的两个时刻（输入
        之前、会话停下来之后）手上只有那个座位的会话。记的是刚才 `memory_index`
        读过的那一位，所以「注入里看得见的」和「铺到会话目录里的」是同一份。
        """
        self._turns.pop(seat, None)
        self._turns[seat] = _Turn(room_id, acting, speaker)
        while len(self._turns) > MEMORY_TURNS_KEPT:
            del self._turns[next(iter(self._turns))]

    def _scopes(
        self, seat: tuple[uuid.UUID, str]
    ) -> list[tuple[MemoryFileScope, str | None]]:
        """这一次对账要点名的那几棵树：team 一份，加这个座位这一轮那一位的
        private（`turn_speaker`）。

        点过名的作用域才是这一次对账的范围（`memory.session` 的开头那一段）：
        没点到的 private 不铺下去；会话目录里上一轮铺下的别人那一份，会话交回来、
        从树上拿走，平台替那个人收下（`tree.withdrawn_prefixes`）。多人
        的房间里别人的 private 不进这一轮（产品决定，2026-10-30），所以只点这个座
        位自己的那一位，不点同一间房里别的座位的。
        """
        turn = self._turns.get(seat)
        speaker = turn.speaker if turn is not None else None
        return [
            (MemoryFileScope.team, None),
            *([(MemoryFileScope.private, speaker)] if speaker else []),
        ]

    def _writer_of(self, room_id: uuid.UUID, prefix: str) -> str:
        """这间房里最近一轮答的是 ``prefix`` 那个人的座位，署的是谁的名；不知道
        就是 system。"""
        for turn in reversed(self._turns.values()):
            if (
                turn.room_id == room_id
                and turn.speaker
                and prefix_of_scope(MemoryFileScope.private, turn.speaker) == prefix
            ):
                return turn.acting or "system"
        return "system"

    async def sync(self, session: SessionRef) -> None:
        """对一遍这个座位的会话的记忆账；同一间房的两场对账排队，不交错。

        输入之前和会话停下来时各对一次账；一间房里几个座位的会话可能用的是同一
        棵树。两场交错时，后一场读到的是前一场提交之前的数据库：它会把平台的旧版
        铺回会话，盖掉刚落库的改动。
        """
        async with self._syncs.setdefault(session.topic_id, asyncio.Lock()):
            await self.sync_once(session)

    async def sync_once(self, session: SessionRef) -> None:
        """对一遍这个座位的会话的记忆账：平台这一份铺下去，会话改过的收回来。

        两个时刻问它：输入之前（让 agent 一睁眼读到的就是平台现在这一份，别人刚
        改的也在里面）和会话停下来之后（runner 报会话空闲：它是在干活时写的，写的
        时候还没停下）。两个时刻做的是同一件事，因为对账是幂等的 —— 「上一次对过什么」在
        会话那边的基线里，不在这里的记忆里。

        一次对账是跨机的一次往返，所以中间不持有事务：先把数据库这一份读出来、
        放开，再问会话，最后在一个短事务里写回、把改动说进房间。
        """
        topic_id = session.topic_id
        seat = (session.conversation_id, session.agent_handle)
        scopes = self._scopes(seat)
        turn = self._turns.get(seat)
        updated_by = (turn.acting if turn else "") or "system"
        async with self._sessions() as db:
            topic = await TopicRepository(db).get(topic_id)
            if topic is None:
                return
            project_id = topic.project_id
            stored = await read_tree(db, project_id, scopes)
        # What is said in a private chat or private channel stays with the people
        # in it (#3830): its session reads the project's shared memory but does
        # not write it.
        collected = (
            [s for s in scopes if s[0] is not MemoryFileScope.team]
            if seats_only(topic)
            else scopes
        )
        answer = await self._compute.memory(session, {"scopes": stored.scopes})
        if answer is None:
            # 这间房现在没有能对账的会话：没有活着的会话，或者这个 harness 的会话
            # 不落记忆文件。两种都只是「这里没有这件事」，不是失败。
            return
        async with self._sessions() as db:
            change = await apply_tree(
                db,
                project_id,
                stored,
                answer,
                scopes=collected,
                updated_by=updated_by,
            )
            # 上一轮那一位的 private，这一次没点名、会话交了回来：替那个人收下
            # （`memory.session.apply_withdrawn`）。写它的那一轮不在这个座位上，
            # 所以只记进那个人的私聊，不说给这一轮的 agent。
            said = _said(change)
            for prefix, given in (answer.get("withdrawn") or {}).items():
                handed, owners = await apply_withdrawn(
                    db,
                    project_id,
                    {prefix: given},
                    updated_by=self._writer_of(topic_id, prefix),
                )
                if _said(handed):
                    said = True
                    await _say_memory_change(
                        db, project_id, handed, owners, writer_room=None
                    )
            if _said(change):
                # 说给写它的那段对话：一间房里的任务是它自己的一段对话。
                await _say_memory_change(
                    db,
                    project_id,
                    change,
                    collected,
                    writer_room=session.conversation_id,
                )
            # 一次对账大部分时候什么都没变：那就什么都不说，也没有可提交的。这条
            # 事件是给人扫一眼的，而每一轮都发一条「没变」等于把它淹没。
            if said:
                await db.commit()

    # --- dream：平台自己过一遍这个项目的记忆 --------------------------------

    async def sweep(self) -> dict:
        """巡检一圈：哪些项目该整理记忆了，逐个跑（`periodic_jobs` 里的一个）。

        判据是「这个项目最近花了多少」和「距上次整理多久」（`memory.dream` 模块
        开头那一段），所以先挨个项目问一句「该不该」，再动手。不该整理的项目在
        `run_dream` 的第一段就返回了，代价是一次花销求和。

        **串行**：一次整理是几次可能各跑几分钟的模型调用，同时起很多个只会互相
        抢网关。这一条钟慢一拍不要紧——下一次巡检还会来，而整理晚一小时没有代价。
        """
        done: list[str] = []
        failed: list[str] = []
        async with self._sessions() as session:
            # An archived project runs nothing, a dream included.
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
        做的是它做不了的那一半：读这个项目的花销、把每一份整理调用发出去、把结果
        收回来。

        **按作用域分开跑，每一份是一次模型调用**（`memory.dream` 的开头那一段）：
        先 team，再每一个有 private 的人（`_dream_scope`）。没有会话、没有座位，所
        以整理不在任何房间里说话，也不在任何现场留下记录；结果只记在
        `memory_dream_runs` 里，被拦下的那一份报给平台的告警。
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
            # 改动署项目默认芝士的名，和它自己的会话改记忆时一样。
            author = await _agent_handle(session, root_topic_id)
            # 每个人的 private 都要整理，不只是最近说过话的几个人——整理是唯一一个
            # 把整棵树放在一起看的时刻，漏掉一个没说过话的人，等于他的记忆没有人
            # 整理（`private_owners` 的开头那一段）。
            owners = await private_owners(session, project_id)
            run = await dream.open_run(session, project_id, tokens_at_start=tokens)
            run_id = run.id
            await session.commit()
        logger.info("memory dream starting project=%s tokens=%s", project_id, tokens)

        team = await self._dream_scope(
            project_id,
            MemoryFileScope.team,
            None,
            system=TEAM_DREAM_SYSTEM,
            prompt=team_dream_prompt,
            author=author,
        )
        # private 那几份对着的是 team 整理之后的索引：判「这条只是重复了 team」要
        # 对的是 team 现在的样子。
        async with self._sessions() as session:
            team_index = await MemoryFileStore(session).index_text(
                project_id, MemoryFileScope.team, None
            )
        privates = [
            await self._dream_scope(
                project_id,
                MemoryFileScope.private,
                owner,
                system=PRIVATE_DREAM_SYSTEM,
                prompt=lambda tree: private_dream_prompt(tree, team_index or ""),
                author=author,
            )
            for owner in owners
        ]
        # 这一次花的钱都在项目的整理 key 上：当场按 dream 记下。
        drained = await drain_dream_spend(
            self._sessions,
            self._gateway,
            project_id,
            root_topic_id,
            run_id,
        )

        failed = bool(team.failure)
        refused = bool(team.refusal or any(p.refusal for p in privates))
        if failed:
            status = MemoryDreamRunStatus.failed
        elif refused:
            status = MemoryDreamRunStatus.refused
        else:
            status = MemoryDreamRunStatus.completed
        async with self._sessions() as session:
            state = await dream.state_of(session, project_id)
            if state is not None:
                if failed:
                    await dream.release(session, state)
                else:
                    await dream.finish(session, state, now=datetime.now(UTC))
            run = await dream.run_of(session, run_id)
            if run is not None:
                await dream.close_run(
                    session,
                    run,
                    status=status,
                    summary=_dream_summary(team, privates),
                    # 只列 team 的：某个人 private 里的文件名也是他的内容。
                    files=list(team.changed),
                    now=datetime.now(UTC),
                )
            await record_dream_usage(
                session,
                project_id=project_id,
                root_topic_id=root_topic_id,
                turn_id=run_id,
                drained=bool(drained),
            )
            await session.commit()
        logger.info(
            "memory dream %s project=%s team_changed=%s private_changed=%s",
            status.value,
            project_id,
            len(team.changed),
            sum(len(p.changed) for p in privates),
        )
        if refused:
            _alert_refused(project_id, run_id, team, privates)
        return {
            "status": status.value,
            "tokens": tokens,
            "files": list(team.changed),
            "summary": team.summary,
        }

    async def _dream_scope(
        self,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner: str | None,
        *,
        system: str,
        prompt: Callable[[dict[str, str]], str],
        author: str,
    ) -> _ScopeDream:
        """整理一个作用域：一次模型调用，平台核过它的答案再写回这一个作用域。

        调用发在项目的整理 key 上（`project_gateway_key(dream=True)`），花费由
        `run_dream` 按 dream 结算。输入只有这一个作用域（private 那一份另带 team
        的索引），没有会话、没有工具、没有凭证，所以它读到的东西只能落回这一棵树。
        写进去之后不在任何房间里说一声：改了什么在记忆面板上看。

        要删的超过一半且超过 3 条，整份不写（`dream.refused_scopes`）。
        """
        scopes: list[tuple[MemoryFileScope, str | None]] = [(scope, owner)]
        prefix = prefix_of_scope(scope, owner)
        async with self._sessions() as session:
            stored = await read_tree(session, project_id, scopes)
        before = stored.scopes.get(prefix, {})
        key = await self._host.project_gateway_key(project_id, dream=True)
        if not key:
            return _ScopeDream(scope, failure="no_gateway")
        chat = GatewayChat(
            key, model_roles.model("agent_model"), max_tokens=DREAM_ANSWER_TOKENS
        )
        try:
            said = await chat.complete(
                system=system,
                prompt=prompt(dream.trim_tree(before)),
                timeout=DREAM_CALL_S,
                json_response=True,
            )
            answer = dream.dream_answer(before, said.content)
        except (GatewayCallError, dream.DreamAnswerError) as exc:
            logger.warning(
                "memory dream not applied project=%s scope=%s: %s",
                project_id,
                scope.value,
                exc,
            )
            return _ScopeDream(scope, failure=str(exc))
        files = {f"{prefix}/{name}": content for name, content in answer.tree.items()}
        if refused := dream.refused_scopes(stored.contents, files):
            return _ScopeDream(
                scope,
                refusal="；".join(
                    f"要删 {len(paths)} 条" for paths in refused.values()
                ),
            )
        async with self._sessions() as session:
            change = await apply_tree(
                session,
                project_id,
                stored,
                {"files": files},
                scopes=scopes,
                updated_by=author,
            )
            await session.commit()
        return _ScopeDream(
            scope,
            changed=tuple(sorted({*change.added, *change.updated, *change.removed})),
            removed=len(change.removed),
            summary=answer.summary,
        )


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


def _dream_summary(team: "_ScopeDream", privates: list["_ScopeDream"]) -> str:
    """`memory_dream_runs.summary`：team 那一份的交代，加 private 那几份的数。

    private 只写数：几个人、改了几条、删了几条、几份没写成。谁的、改了什么、它自
    己那句交代都不写——那是这个人的内容，而这一行谁都可能读到。
    """
    if team.refusal:
        lines = [f"team 拒绝执行：{team.refusal}"]
    elif team.failure:
        lines = [f"team 没有写：{team.failure}"]
    else:
        lines = [dream.clip(team.summary, 1000) or "team 没有改动"]
    if privates:
        changed = sum(len(p.changed) - p.removed for p in privates)
        removed = sum(p.removed for p in privates)
        said = f"private：{len(privates)} 人，改 {changed}、删 {removed}"
        refused = sum(1 for p in privates if p.refusal)
        failed = sum(1 for p in privates if p.failure)
        if refused:
            said += f"，{refused} 人拒绝执行"
        if failed:
            said += f"，{failed} 人没写成"
        lines.append(said)
    return "\n".join(lines)


def _alert_refused(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    team: "_ScopeDream",
    privates: list["_ScopeDream"],
) -> None:
    """一份整理要删的太多、没写：报给平台的告警，不进任何房间。

    这条要有人看，因为它说的是一次**什么都没发生**：记忆一条都没少，而那棵树多半
    出了事。要动手的是平台的人（看那棵树怎么了），所以说给告警，不说给房间；
    private 那几份只报个数，不报是谁的。
    """
    lines = [f"project {project_id}", f"run {run_id}"]
    if team.refusal:
        lines.append(f"team：{team.refusal}")
    if refused := sum(1 for p in privates if p.refusal):
        lines.append(f"private：{refused} 份要删的太多，没写")
    logger.warning("memory dream refused a bulk delete: %s", "; ".join(lines))
    alerting.send(
        "记忆整理拦下了一次批量删除",
        lines,
        key=f"memory-dream-refused:{project_id}",
    )


def _said(change: MemoryChange) -> bool:
    """这一份改动有没有要说的：改了什么，或者有被盖回去、没收下的。"""
    return not change.is_empty() or bool(change.refused or change.rejected)
