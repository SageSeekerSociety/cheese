"""网关那一半的轮次账：这一轮跑哪个模型、带哪些凭证，以及它花了多少。

``ChatService`` 上这一族方法回答的是同一个问题的两半：

- **准入之前**：这一轮用哪个模型、哪套环境（``_model_kwargs``）、项目的虚拟网关
  key 长什么样、它的 L2 预算要不要跟着项目额度调（``project_gateway_key``、
  ``_gateway_project_env``）；
- **这一轮结束之后**：项目这把 key 怎么读、钱记给谁（``ProjectGatewayKey``，读和
  记账本身在 ``gateway_spend``），这一遍读不到就晚一点再读一遍
  （``_schedule_deferred_drain``）。

搬出来时按原样搬 —— 入参出参就是它们与调用方之间全部的约定，行为一格没动。形状
变化只有两类，都是「没有 `self` 可用了」：

- 实例上那几件共享的东西（``_sessions``、``_gateway``、``_profiles``、
  ``_gateway_lock``、``_background_tasks``）→ 同名入参，逐字不变；
  ``self._GW_KEY`` / ``_GW_CKPT`` / ``_GW_BUDGET`` 三个字符串 → 同名的模块级常量，
  这条线之外没有第二处读它们；
- 留在 ``ChatService`` 上、这条路回头要问的那些事（这一轮署谁的名、模型过不过
  项目的档位闸门、项目的 key 该带多少额度）→ ``service``，见 ``_GatewayUsage``：
  它是这条路的收件人，本模块只声明自己会问什么，pyright 在调用点核对
  ``ChatService`` 答不答得上来。

``app.api`` 一步都不碰（``LlmGateway`` 来自 ``app.domain.agent.gateway``），
事务边界也一格没动 —— sessionmaker 本身是入参，所以每一处
``async with self._sessions()`` 都变成了同一处的 ``async with sessions()``。
"""

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import hold
from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import Charge, settle
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.queries import _model_policy_call, _Proposed
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.service import AgentUsage
from app.domain.agent.supply import SUBSCRIPTION
from app.domain.agent_instance.services import AgentInstanceService, ResolvedAgent
from app.domain.policy import gate
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import binding
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.ledger import Payer, payer_for_project, team_terms

logger = logging.getLogger(__name__)


#: 项目设置里这三把钥匙：虚拟 key 本身、上一次读到哪儿的用量检查点、
#: 以及这把 key 在网关上该带的 L2 预算。
_GW_KEY = "llm_gateway_key"
_GW_CKPT = "llm_gateway_usage_ckpt"
_GW_BUDGET = "llm_gateway_budget_usd"
#: 平台自己在项目里干活（记忆整理）用的第二把 key 和它的检查点。这把 key 的花
#: 费是平台的，不进项目付钱的那把 key，所以结算时不会算到团队头上（#2233）；它
#: 也不带预算：平台的活不受团队额度约束。
_GW_PLATFORM_KEY = "llm_gateway_platform_key"
_GW_PLATFORM_CKPT = "llm_gateway_platform_usage_ckpt"


class _GatewayUsage(Protocol):
    """这条路的收件人：``ChatService`` 上留在原地的那三个问题。

    这一轮署谁的名（``_agent_handle``）、这次模型调用过不过项目的档位闸门
    （``_pass_policy_gate``）、项目的 key 该带多少额度（``_gateway_budget_target``）
    —— 它们各自还有别的调用方，方法在 ``ChatService`` 上原样留着。本模块声明自己
    会问哪些，类型在调用点核对；这里只列签名，不写实现。
    """

    async def _agent_handle(
        self, session: AsyncSession, topic_id: uuid.UUID
    ) -> str: ...

    async def _pass_policy_gate(
        self,
        session: AsyncSession,
        topic_id: uuid.UUID | None,
        call: gate.Call,
        policy: gate.Policy,
        *,
        actor: str,
    ) -> _Proposed | None: ...

    async def _gateway_budget_target(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> float | None: ...


def model_capabilities(*models: str | None) -> str:
    """``CLAUDE_CODE_MODEL_CAPABILITIES`` for a launch on these models.

    Claude Code puts the skill listing, the environment and the date in
    system-role messages in the middle of the conversation. A route the
    gateway marks as losing them (``GatewayModel.mid_conversation_system``)
    drops them or refuses the whole request, so on those models Claude Code is
    told to put them in the first user message instead. Every other model keeps
    its default. Empty when no model needs it."""
    unsupported = {
        m.id for m in gateway_catalog.snapshot() if not m.mid_conversation_system
    }
    return ";".join(
        f"{model}=-mid_conv_system"
        for model in sorted({m for m in models if m} & unsupported)
    )


async def _model_kwargs(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    profiles: ProfileRegistry | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
    provider: RoomSessions | None,
    topic_id: uuid.UUID | None = None,
    *,
    agent: ResolvedAgent | None = None,
    acting_agent: str | None = None,
    platform: bool = False,
) -> tuple[dict, str]:
    """Resolve a turn's model, model environment and usage route.

    ``platform`` is a turn the platform runs for itself (memory
    consolidation): it runs on the project's platform key, whose spend
    `ProjectGatewayKey(platform=True)` charges to the platform.

    Which model comes from the binding of the work this turn belongs to —
    and a room's main thread is not a piece of work, so it always gets the
    project default (`room_task/binding.py`).

    Machine providers assemble their own scoped credentials. Other providers
    retain their gateway/profile transport, carrying that resolved model.
    The optional snapshots keep model, role and author consistent within a turn.

    ``provider=None`` means there is no machine in this turn at all (私聊 走
    platform work): the platform is the one about to call the model, so it needs
    the same base_url + key a sandbox would have been handed. That is exactly
    the not-``builds_model_env`` branch, so it falls through to it rather than
    growing a second way to answer the same question.
    """
    environment = None
    async with sessions() as session:
        project = await ProjectRepository(session).get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        topic = await TopicRepository(session).get(topic_id) if topic_id else None
        if topic is not None:
            if acting_agent is None:
                acting_agent = await service._agent_handle(session, topic.id)
            # Overview remains available to repair failed project setup.
            environment = (
                EnvironmentConfig().snapshot()
                if topic.kind == TopicKind.root
                else await pin_environment(session, project_id, topic.id)
            )
            await session.commit()
        if agent is None:
            agents = AgentInstanceService(session)
            agent = (
                await agents.for_topic(topic, project)
                if topic
                else await agents.for_project(project)
            )
    # A saved teammate may override the project main model.
    bound = binding.resolve(
        None,
        binding.catalog(project.settings),
        agent_model=agent.configuration.get("model"),
        default_model=(project.settings or {}).get("default_model"),
    )
    # 解析出来的那个模型还要过一遍项目的档位策略（结论 3 后半）。闸门不写进
    # `binding.resolve`：那个函数只答「用哪个模型」，「超档怎么办」是另一个问
    # 题，而且它的另一个调用者是要机器的那条路（`domain/policy/gate.py`）。
    #
    # 一条房间主线在组装那一步就过过闸门了（那里是这一轮占用任何东西之前）；
    # 走到这里还没过的，是平台自己发起的那一轮 —— 记忆整理（dream），
    # 它不经过组装。所以这一处仍然是必要的，而且仍然在任何请求发出去之前。
    async with sessions() as session:
        proposed = await service._pass_policy_gate(
            session,
            topic_id,
            _model_policy_call(project, agent),
            gate.policy_of(
                project.settings,
                (await team_terms(session, project.team_id)).model_tiers,
            ),
            actor=acting_agent or agent.handle,
        )
        if proposed is not None:
            await session.commit()
            raise gate.OverTier(proposed.proposal.content)
    supply = bound.supply
    model = bound.wire_model
    child_default = (project.settings or {}).get("default_subagent_model") or (
        project.settings or {}
    ).get("default_model")
    child_choices = binding.catalog(project.settings)
    # An unused invalid child default must not block a valid main override.
    # Preserve it for the child request's admission refusal, never replace it.
    child_model = child_default
    if not child_default or child_default in child_choices:
        child_model = binding.resolve(
            None, child_choices, default_model=child_default
        ).wire_model
    capabilities = model_capabilities(model, child_model)
    config_hash = hashlib.sha256(
        # Author identity, chat skills, and native RC arguments are installed
        # at process birth; refresh them together at the next task boundary.
        #
        # 模型和它的池也在里面：两条路都在启动那一刻把 model 钉进进程 ——
        # 容器那条路是 `session_launch.py` 拼进 argv 的 `claude --model`，
        # device 那条路是下面的 `ANTHROPIC_MODEL`。Claude Code 按它组装整套
        # 系统提示词和自我介绍（Opus 与 Sonnet 用的是两份不同的提示词），
        # 而这个哈希是唯一比较「屏幕是不是还配得上现在的选择」的地方，所以改
        # 项目模型在两条路上都会到下一个 task boundary 收屏重开一次，提示词
        # 随之换成新模型的。device 上每个请求实际跑哪个模型仍然只由准入决定
        # （结论 46），计量代理把答案写进请求体；启动时的这个名字只决定提示
        # 词，在重开之前的那几轮里它可能落后于绑定。容器那条路没有代理改写，
        # 不放进哈希就是屏幕带着 `--model glm-5.2` 继续跑而准入已经解析成订阅
        # 池，此后每一轮都死在「订阅池收到 glm-5.2」上，直到有人手动重启屏幕。
        (
            json.dumps(
                {
                    "agent": agent.configuration,
                    "git_author": acting_agent,
                    "model": model,
                    "supply": supply,
                    "subagent_model": (project.settings or {}).get(
                        "default_subagent_model"
                    ),
                    "capabilities": capabilities,
                },
                sort_keys=True,
            )
            + ":explicit-chat-v5-launch-model"
            + (":native-rc-v1" if supply == SUBSCRIPTION else "")
        ).encode()
    ).hexdigest()
    kwargs: dict = {
        "model": model,
        "env": {
            "CHEESE_AGENT_CONFIG": config_hash,
            "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
            # The bound model, so Claude Code builds the system prompt and
            # self-description for the model the turn actually runs on.
            "ANTHROPIC_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": child_model,
        },
        # Which conversation the turn belongs to, and so which session's
        # machines it runs on. Separate from `agent_handle` below, which is
        # the SEAT the turn authors under — the two are different strings
        # and the place is recorded under this one.
        "session_agent": agent.handle,
    }
    if capabilities:
        kwargs["env"]["CLAUDE_CODE_MODEL_CAPABILITIES"] = capabilities
    if acting_agent is not None:
        kwargs["agent_handle"] = acting_agent
    if environment is not None:
        kwargs["env"]["CHEESE_ENVIRONMENT"] = json.dumps(environment)
    if provider is not None and provider.builds_model_env:
        return kwargs, supply
    pool_route = True
    if profiles is not None:
        profile = profiles.resolve(
            project.settings if project else None,
            project.owner_handle if project else None,
        )
        kwargs["env"].update(profile.full_env())
        kwargs["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        kwargs["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        # Only the pool profile routes through the gateway; the testing
        # (native Claude) profiles pin their own base_url + credentials.
        pool_route = profile.base_url == settings.anthropic_base_url
    routed = pool_route and gateway is not None
    if routed:
        # L1/L2: the sandbox runs on the project's VIRTUAL gateway key — never
        # the master key (containment), attributable + budget-capped.
        override = await _gateway_project_env(
            service, sessions, gateway, gateway_lock, project_id, platform=platform
        )
        if not override:
            raise GatewayUnavailableError(
                "AI gateway could not provision a project-scoped key; "
                "no model call was made"
            )
        kwargs["env"] = {**kwargs.get("env", {}), **override}
    return kwargs, "gateway" if routed else "native"


async def project_gateway_key(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
) -> str | None:
    """The project's virtual gateway key, minted on first use — the same one
    a local sandbox turn runs on. Public because the remote-machine LLM proxy
    (routes/llm_proxy.py) has to swap it in per request: a machine off the box
    never receives a provider credential, only its own scoped cheese token."""
    env = await _gateway_project_env(
        service, sessions, gateway, gateway_lock, project_id
    )
    return (env or {}).get("ANTHROPIC_AUTH_TOKEN")


async def _gateway_project_env(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
    *,
    platform: bool = False,
) -> dict | None:
    """Env override for a gateway-routed turn: mint (once) and return the
    project's virtual key, and keep its L2 max_budget in step with the
    project's grants. Returns None on any gateway/admin failure; the caller
    must refuse the turn rather than expose default pool credentials.

    **Answering a project that needs nothing written takes no lock at all.**
    `_gateway_lock` serialises the settings read-modify-write, but this
    method used to hold it across the whole body — including the two gateway
    HTTP calls — so one project minting a key, or one drain asking the
    gateway for spend, stalled every admission on the box behind it. These
    calls are on the hot path of every model request (routes/llm_proxy.py
    asks once per turn, and the metering proxy asks per request), and the
    queued wait is what the /llm/admission p95 is made of. Measured on dev,
    2026-09-23: 20 concurrent admissions for ONE project — key long since
    minted, no budget drift to apply — still fanned out into a 5.8 s tail.
    Nothing about answering that request is exclusive, so it is answered
    before the lock is reached.

    ``platform`` answers with the project's platform key instead: minted once
    the same way, never budget-capped.
    """
    if platform:
        return await _gateway_platform_env(sessions, gateway, gateway_lock, project_id)
    try:
        # Read path: the key exists and is in step → nothing to write.
        async with sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or gateway is None:
                return None
            s = dict(project.settings or {})
            key = s.get(_GW_KEY)
            if isinstance(key, str) and key:
                target = await service._gateway_budget_target(session, project_id)
                if s.get(_GW_BUDGET) == target:
                    return {"ANTHROPIC_AUTH_TOKEN": key}

        # Write path: something must be minted or re-priced. Serialised, and
        # the settings row is re-read here so a mint that landed while this
        # call waited on the lock is the one that gets used.
        async with gateway_lock:
            async with sessions() as session:
                project = await ProjectRepository(session).get(project_id)
                if project is None or gateway is None:
                    return None
                s = dict(project.settings or {})
                key = s.get(_GW_KEY)
                if not isinstance(key, str) or not key:
                    key = await gateway.mint_project_key(project_id)
                    if not key:
                        return None
                    s[_GW_KEY] = key
                target = await service._gateway_budget_target(session, project_id)
                # No target clears the brake: a budget left on the key from
                # before would refuse calls the platform now admits.
                if s.get(_GW_BUDGET) != target:
                    if await gateway.set_key_budget(key, target):
                        if target is None:
                            s.pop(_GW_BUDGET, None)
                        else:
                            s[_GW_BUDGET] = target
                if s != (project.settings or {}):
                    project.settings = s
                    await session.commit()
        return {"ANTHROPIC_AUTH_TOKEN": key}
    except Exception:  # noqa: BLE001 — never fail a turn on admin plumbing
        logger.exception("gateway project-env failed for %s", project_id)
        return None


async def _gateway_platform_env(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
) -> dict | None:
    """The project's platform key as a turn env, minted on first use."""
    try:
        async with sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or gateway is None:
                return None
            key = (project.settings or {}).get(_GW_PLATFORM_KEY)
            if isinstance(key, str) and key:
                return {"ANTHROPIC_AUTH_TOKEN": key}
        async with gateway_lock:
            async with sessions() as session:
                project = await ProjectRepository(session).get(project_id)
                if project is None:
                    return None
                s = dict(project.settings or {})
                key = s.get(_GW_PLATFORM_KEY)
                if not isinstance(key, str) or not key:
                    key = await gateway.mint_project_key(project_id, platform=True)
                    if not key:
                        return None
                    s[_GW_PLATFORM_KEY] = key
                    project.settings = s
                    await session.commit()
        return {"ANTHROPIC_AUTH_TOKEN": key}
    except Exception:  # noqa: BLE001 — never fail a turn on admin plumbing
        logger.exception("gateway platform-env failed for %s", project_id)
        return None


@dataclass(frozen=True)
class ProjectGatewayKey:
    """A project's key on the gateway, or with ``platform`` the platform's own
    key inside the project, whose spend the platform pays (#2233). Both live in
    the project's settings beside their checkpoints."""

    project_id: uuid.UUID
    platform: bool = False

    @property
    def _fields(self) -> tuple[str, str]:
        if self.platform:
            return _GW_PLATFORM_KEY, _GW_PLATFORM_CKPT
        return _GW_KEY, _GW_CKPT

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        project = await ProjectRepository(session).get(self.project_id)
        if project is None:
            return None
        if for_update:
            # Another backend process can charge during a rollout.
            await session.refresh(project, with_for_update=True)
        key_field, ckpt_field = self._fields
        s = project.settings or {}
        key = s.get(key_field)
        if not isinstance(key, str) or not key:
            return None  # nothing ever routed → nothing to meter
        ckpt = s.get(ckpt_field)
        return key, ckpt if isinstance(ckpt, dict) else None

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        project = await ProjectRepository(session).get(self.project_id)
        assert project is not None
        project.settings = {**(project.settings or {}), self._fields[1]: checkpoint}

    async def payer(self, session: AsyncSession) -> Payer | None:
        if self.platform:
            return None
        return await payer_for_project(session, self.project_id)


#: A turn's spend is read when it ends, and once more this much later if the
#: gateway had not logged it yet.
TURN_RETRY_AFTER = (3.0,)
#: How long after that a turn whose spend still had not surfaced is read again.
LATE_S = 20.0


async def charge_turn_spend(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> list[AgentUsage] | None:
    """Charge what the project's key spent for a turn, one row per model,
    reading again once if the gateway has not logged it yet."""
    return await settle(
        sessions,
        gateway,
        ProjectGatewayKey(project_id),
        Charge(
            model=settings.agent_model,
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
        ),
        retry_after=TURN_RETRY_AFTER,
        lock=gateway_lock,
    )


def _schedule_deferred_drain(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    background_tasks: set[asyncio.Task],
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> None:
    """Late-landing spend rows: charge again in the background. Strong-ref'd so
    the pending commit can't be GC'd."""

    async def _later() -> None:
        await asyncio.sleep(LATE_S)
        usages = await charge_turn_spend(
            sessions, gateway, gateway_lock, project_id, topic_id, turn_id
        )
        if not usages:
            return  # still nothing — the next turn's charge picks it up
        logger.info(
            "deferred usage drain landed for turn %s (%s)",
            turn_id,
            ", ".join(
                f"{u.model or '?'}:{u.input_tokens}+{u.output_tokens}" for u in usages
            ),
        )

    hold(
        asyncio.create_task(_later()),
        background_tasks,
        name=f"deferred-usage-drain-{turn_id}",
    )
