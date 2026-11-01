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
  ``_background_tasks``）→ 同名入参，逐字不变；
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
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import model_roles
from app.core.background import hold
from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError, ValidationError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import Charge, charge_new_spend, settle
from app.domain.agent.model_catalog import catalog, subagent_default
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.queries import _model_policy_call, _Proposed, task_credit_context
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.service import AgentUsage
from app.domain.agent.session_host.contract import EFFORT_ENV
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION
from app.domain.agent_instance.own import owned_instance, unusable
from app.domain.agent_instance.services import AgentInstanceService, ResolvedAgent
from app.domain.conversation.models import Conversation
from app.domain.policy import gate
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import binding
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.ledger import Payer, payer_for_project, team_terms
from app.domain.usage.model_access import overseas_approved
from app.domain.usage.models import MachineGatewayAccount, TaskGatewayAccount
from app.domain.usage.services import UsageService

logger = logging.getLogger(__name__)


#: 项目设置里这三把钥匙：虚拟 key 本身、上一次读到哪儿的用量检查点、
#: 以及这把 key 在网关上该带的 L2 预算。
_GW_KEY = "llm_gateway_key"
_GW_CKPT = "llm_gateway_usage_ckpt"
_GW_BUDGET = "llm_gateway_budget_usd"


#: ``project_gateway_key`` was not told the project key's budget target.
UNKNOWN_TARGET = object()


@dataclass(frozen=True)
class _KeySlot:
    """Where one of a project's gateway keys lives in its settings: the key,
    its usage checkpoint, the budget last set on it, and what the key is for
    (the suffix of its alias on the gateway; None for the room key)."""

    key: str
    ckpt: str
    budget: str
    purpose: str | None


#: The key the project's rooms run on.
_ROOM_SLOT = _KeySlot(_GW_KEY, _GW_CKPT, _GW_BUDGET, None)
#: The key the project's memory consolidation (dream) runs on. The project pays
#: for both; the dream has its own so its spend is read as the dream's, whatever
#: room turn ends while it runs (a key's spend is read per key and day, never
#: per request).
_DREAM_SLOT = _KeySlot(
    "llm_gateway_dream_key",
    "llm_gateway_dream_usage_ckpt",
    "llm_gateway_dream_budget_usd",
    "dream",
)


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


def window_env(model: str | None) -> dict[str, str]:
    """``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` for a session on ``model``: the window
    the gateway declares for it. Without it Claude Code assumes 200K for every
    model id it does not recognise and compacts at about 167K, whatever the
    model can hold. It knows every ``claude-`` id itself and ignores the
    variable for one, so none is sent.

    The variable is the session's, and Claude Code applies it to every model id
    it does not recognise, subagents included; a subagent on a model with a
    smaller window is held to its own by the per-model compaction windows in
    the session's settings (``session_launch.compact_windows``)."""
    if not model or model.lower().startswith("claude-"):
        return {}
    window = next(
        (m.context_window for m in gateway_catalog.snapshot() if m.id == model),
        None,
    )
    return {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(window)} if window else {}


def model_agents(project_settings: dict | None) -> dict[str, str]:
    """The project's models as Claude Code subagent definitions, by file name.

    The Agent tool takes a model only as one of Claude's family aliases, so a
    session runs a subagent on `kimi-k3` by starting the subagent type of that
    name, whose definition names the model; admission still decides whether
    it may (`/llm/admission`). Only models the platform pool serves (catalogue
    ``supply`` gateway) are listed: a subscription model is refused there unless
    the project or the seat already selected it.

    Claude Code reads its agent definitions once, when it starts, and a file
    written into a running session's agents directory is never seen by it. So
    the launch writes them (`device_launch`), and they are in the session's
    configuration hash: a model the gateway adds reaches a running session when
    it is next reopened, at a task boundary.
    """
    agents: dict[str, str] = {}
    for choice in catalog(project_settings).values():
        if choice["supply"] != GATEWAY:
            continue
        # Agent names are stricter than model ids; the id itself rides `model`.
        name = re.sub(r"[^a-z0-9._-]+", "-", choice["id"].lower()).strip("-.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", name):
            continue
        # The line the main agent reads in the Agent tool's list of types.
        description = (
            f"在 {choice['label']}（{choice['id']}）上跑一个分身。要用这个模型派活，"
            f"subagent_type 填 {name}；Agent 工具的 model 参数只认 Claude 的别名。"
        )
        agents[f"model-{name}.md"] = (
            "---\n"
            f"name: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"model: {choice['id']}\n"
            "---\n\n"
            "完成委派给你的任务，结束时把结果完整交回给委派你的 agent。\n"
        )
    return agents


def launch_env(configuration: dict, efforts: list[str] | tuple[str, ...]) -> dict:
    """The environment that carries a teammate's thinking effort and compaction
    share into its session.

    ``efforts`` are the ones the turn's model honours. An effort it does not is
    left out rather than sent: the harness would pass it to a model that either
    ignores it or refuses the request.
    """
    env: dict[str, str] = {}
    effort = configuration.get("effort")
    if isinstance(effort, str) and effort in efforts:
        env[EFFORT_ENV] = effort
    percent = configuration.get("compact_percent")
    if isinstance(percent, int) and not isinstance(percent, bool):
        env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(percent)
    return env


#: The usage route of a member's own coding agent: its model calls go from its
#: owner's machine on its owner's login, and nothing of them is the project's
#: to pay (`owner_provider`).
OWN_ROUTE = "own"

# Model names the owner's own Claude Code takes: its own aliases, and the
# vendor's ids. A teammate set to a model only the platform's gateway serves
# runs on the account's default instead.
_OWN_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable"})


def own_model(configured: object) -> str | None:
    if not isinstance(configured, str) or not configured:
        return None
    if configured in _OWN_ALIASES or configured.startswith("claude-"):
        return configured
    return None


def _own_model_kwargs(agent, acting_agent, environment) -> dict:
    """A member's own Claude Code: the model it is set to when its owner's
    login can run it, else the account's default; no project catalogue, no
    tier gate and no gateway, which are about the project's credits."""
    model = own_model(agent.configuration.get("model") or "sonnet")
    env: dict[str, str] = {
        "CHEESE_AGENT_CONFIG": hashlib.sha256(
            json.dumps(
                {"agent": agent.configuration, "git_author": acting_agent},
                sort_keys=True,
            ).encode()
            + b":own-login-v1"
        ).hexdigest(),
    }
    if model:
        env["ANTHROPIC_MODEL"] = model
    if environment is not None:
        env["CHEESE_ENVIRONMENT"] = json.dumps(environment)
    kwargs: dict = {"model": model, "env": env, "session_agent": agent.handle}
    if acting_agent is not None:
        kwargs["agent_handle"] = acting_agent
    return kwargs


async def _model_kwargs(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    profiles: ProfileRegistry | None,
    project_id: uuid.UUID,
    provider: RoomSessions | None,
    topic_id: uuid.UUID | None = None,
    *,
    agent: ResolvedAgent | None = None,
    acting_agent: str | None = None,
) -> tuple[dict, str]:
    """Resolve a turn's model, model environment and usage route.

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
        owned = await owned_instance(session, agent.instance_id)
        if owned is not None:
            reason = await unusable(session, owned, project.settings)
            if reason is not None:
                raise ValidationError(reason)
    if owned is not None:
        return _own_model_kwargs(agent, acting_agent, environment), OWN_ROUTE
    # A saved teammate may override the project main model.
    choices = catalog(project.settings)
    bound = binding.resolve(
        None,
        choices,
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
        policy = gate.policy_of(
            project.settings,
            (await team_terms(session, project.team_id)).model_tiers,
            overseas_approved=await overseas_approved(session, project.team_id),
        )
        proposed = await service._pass_policy_gate(
            session,
            topic_id,
            _model_policy_call(project, agent),
            policy,
            actor=acting_agent or agent.handle,
        )
        if proposed is not None:
            await session.commit()
            raise gate.OverTier(proposed.proposal.content)
    supply = bound.supply
    model = bound.wire_model
    child_choices = catalog(project.settings)
    child_default = subagent_default(project.settings, child_choices, policy)
    # An unused invalid child default must not block a valid main override.
    # Preserve it for the child request's admission refusal, never replace it.
    child_model = child_default
    if not child_default or child_default in child_choices:
        child_model = binding.resolve(
            None, child_choices, default_model=child_default
        ).wire_model
    capabilities = model_capabilities(model, child_model)
    window = window_env(model)
    agent_types = model_agents(project.settings)
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
                    "window": window,
                    "model_agents": agent_types,
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
            # The model's real window, so it does not compact at 200K.
            **window,
            # Written as subagent definitions by the launch (`model_agents`).
            "CHEESE_MODEL_AGENTS": json.dumps(agent_types),
            # The teammate's thinking effort and compaction share. Both are in
            # `agent.configuration`, and so in the hash above: changing either
            # restarts the session at the next task boundary, which is when
            # Claude Code reads its environment.
            **launch_env(
                agent.configuration, choices.get(bound.model, {}).get("efforts", [])
            ),
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
        profile = profiles.resolve(project.settings if project else None)
        kwargs["env"].update(profile.full_env())
        kwargs["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        kwargs["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        # Only the pool profile routes through the gateway; a profile with its
        # own base_url and credentials (a project's own) does not.
        pool_route = profile.base_url == settings.anthropic_base_url
    routed = pool_route and gateway is not None
    if routed:
        # L1/L2: the sandbox runs on the project's VIRTUAL gateway key — never
        # the master key (containment), attributable + budget-capped.
        context = None
        if topic_id is not None:
            async with sessions() as session:
                context = await task_credit_context(
                    session, project_id, topic_id, [acting_agent or agent.handle]
                )
        if context is not None:
            task_key = await _task_gateway_key(
                sessions, gateway, project_id, context.task_id, context.owner_handle
            )
            override = {"ANTHROPIC_AUTH_TOKEN": task_key} if task_key else None
        else:
            override = await _gateway_project_env(
                service, sessions, gateway, project_id
            )
        if not override:
            raise GatewayUnavailableError(
                "AI gateway could not provision a project-scoped key; "
                "no model call was made"
            )
        kwargs["env"] = {**kwargs.get("env", {}), **override}
    return kwargs, "gateway" if routed else "native"


def _key_in_step(
    settings: dict | None, slot: _KeySlot, target: float | None
) -> str | None:
    """The key in ``slot`` when the project's ``settings`` name one whose budget
    is ``target``: nothing to mint or re-price, so nothing to write."""
    s = settings or {}
    key = s.get(slot.key)
    if isinstance(key, str) and key and s.get(slot.budget) == target:
        return key
    return None


def room_key_in_step(settings: dict | None, target: float | None) -> str | None:
    """The project key from settings already read, by the same test
    `_gateway_project_env`'s read path makes, without opening a session to
    read the project again."""
    return _key_in_step(settings, _ROOM_SLOT, target)


async def project_gateway_key(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    *,
    dream: bool = False,
    task_id: uuid.UUID | None = None,
    credit_owner_handle: str | None = None,
    machine_conversation_id: uuid.UUID | None = None,
    budget_target: float | None | object = UNKNOWN_TARGET,
) -> str | None:
    """The project's virtual gateway key, minted on first use — the same one
    a local sandbox turn runs on, or with ``dream`` the key its memory
    consolidation runs on. Public because the remote-machine LLM proxy
    (routes/llm_proxy.py) has to swap it in per request: a machine off the box
    never receives a provider credential, only its own scoped cheese token.

    With ``machine_conversation_id`` it is the key that conversation's
    sandboxes call on (`MachineGatewayKey`), for ``credit_owner_handle`` or,
    without one, the project.

    ``budget_target``: the project key's ``_gateway_budget_target``, when the
    caller has just read it (`routes/llm_proxy.py`, in the transaction that
    admitted the call); the project key's read path then does not read it
    again. Only the project key takes it; the write path reads it under the
    lock whatever is given."""
    if machine_conversation_id is not None:
        return await _machine_gateway_key(
            sessions,
            gateway,
            project_id,
            machine_conversation_id,
            credit_owner_handle or "",
        )
    if task_id is not None and credit_owner_handle is not None:
        return await _task_gateway_key(
            sessions, gateway, project_id, task_id, credit_owner_handle
        )
    env = await _gateway_project_env(
        service,
        sessions,
        gateway,
        project_id,
        slot=_DREAM_SLOT if dream else _ROOM_SLOT,
        known_target=UNKNOWN_TARGET if dream else budget_target,
    )
    return (env or {}).get("ANTHROPIC_AUTH_TOKEN")


async def _task_gateway_key(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    owner_handle: str,
) -> str | None:
    if gateway is None:
        return None
    async with sessions() as session:
        account = await session.get(TaskGatewayAccount, (task_id, owner_handle))
        target = await UsageService(session).task_gateway_budget(
            project_id, task_id, owner_handle
        )
        if (
            account is not None
            and account.project_id == project_id
            and account.gateway_key
            and account.budget_usd == target
        ):
            return account.gateway_key
    async with sessions() as session:
        await session.execute(
            insert(TaskGatewayAccount)
            .values(
                conversation_id=task_id,
                owner_handle=owner_handle,
                project_id=project_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    TaskGatewayAccount.conversation_id,
                    TaskGatewayAccount.owner_handle,
                ]
            )
        )
        account = await session.scalar(
            select(TaskGatewayAccount)
            .where(
                TaskGatewayAccount.conversation_id == task_id,
                TaskGatewayAccount.owner_handle == owner_handle,
                TaskGatewayAccount.project_id == project_id,
            )
            .with_for_update()
        )
        if account is None:
            return None
        if not account.gateway_key:
            holder = hashlib.sha256(owner_handle.encode()).hexdigest()[:12]
            account.gateway_key = await gateway.mint_project_key(
                project_id, purpose=f"task-{task_id.hex}-{holder}"
            )
            if not account.gateway_key:
                return None
        target = await UsageService(session).task_gateway_budget(
            project_id, task_id, owner_handle
        )
        if account.budget_usd != target:
            if not await gateway.set_key_budget(account.gateway_key, target):
                return None
            account.budget_usd = target
        await session.commit()
        return account.gateway_key


async def _machine_gateway_key(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    owner_handle: str,
) -> str | None:
    if gateway is None:
        return None
    cap = settings.machine_model_daily_budget_usd
    async with sessions() as session:
        account = await session.get(
            MachineGatewayAccount, (conversation_id, owner_handle)
        )
        if (
            account is not None
            and account.project_id == project_id
            and account.gateway_key
            and account.daily_budget_usd == cap
        ):
            return account.gateway_key
    async with sessions() as session:
        await session.execute(
            insert(MachineGatewayAccount)
            .values(
                conversation_id=conversation_id,
                owner_handle=owner_handle,
                project_id=project_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    MachineGatewayAccount.conversation_id,
                    MachineGatewayAccount.owner_handle,
                ]
            )
        )
        account = await session.scalar(
            select(MachineGatewayAccount)
            .where(
                MachineGatewayAccount.conversation_id == conversation_id,
                MachineGatewayAccount.owner_handle == owner_handle,
                MachineGatewayAccount.project_id == project_id,
            )
            .with_for_update()
        )
        if account is None:
            return None
        if not account.gateway_key:
            holder = hashlib.sha256(owner_handle.encode()).hexdigest()[:12]
            account.gateway_key = await gateway.mint_project_key(
                project_id,
                purpose=f"machine-{conversation_id.hex}-{holder}",
                daily_budget_usd=cap,
            )
            if not account.gateway_key:
                return None
        elif account.daily_budget_usd != cap:
            # The day the key was minted with stays; only the amount moves.
            if not await gateway.set_key_budget(account.gateway_key, cap):
                return None
        account.daily_budget_usd = cap
        await session.commit()
        return account.gateway_key


async def _gateway_project_env(
    service: _GatewayUsage,
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    *,
    slot: _KeySlot = _ROOM_SLOT,
    known_target: float | None | object = UNKNOWN_TARGET,
) -> dict | None:
    """Env override for a gateway-routed turn: mint (once) and return the
    project's virtual key in ``slot``, and keep its L2 max_budget in step with
    the project's grants. Returns None on any gateway/admin failure; the caller
    must refuse the turn rather than expose default pool credentials.

    **Answering a project that needs nothing written takes no lock at all.**
    The write is serialised by the project's settings row lock alone
    (`repo.lock_settings`, held to the commit) — the in-process
    `_gateway_lock` that used to stand beside it is gone. That lock used to be
    held across the whole body, including the two gateway HTTP calls, so one
    project minting a key, or one drain asking the gateway for spend, stalled
    every admission on the box behind it. These calls are on the hot path of
    every model request (routes/llm_proxy.py asks once per turn, and the
    metering proxy asks per request), and the queued wait is what the
    /llm/admission p95 is made of. Measured on dev, 2026-09-23: 20 concurrent
    admissions for ONE project — key long since minted, no budget drift to
    apply — still fanned out into a 5.8 s tail. Nothing about answering that
    request is exclusive, so it is answered before the write is reached.
    """
    try:
        # Read path: the key exists and is in step → nothing to write.
        async with sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or gateway is None:
                return None
            s = dict(project.settings or {})
            key = s.get(slot.key)
            if isinstance(key, str) and key:
                target = (
                    await service._gateway_budget_target(session, project_id)
                    if known_target is UNKNOWN_TARGET
                    else known_target
                )
                in_step = _key_in_step(s, slot, target)
                if in_step is not None:
                    return {"ANTHROPIC_AUTH_TOKEN": in_step}

        # Write path: something must be minted or re-priced. The row lock
        # serialises it, and the settings are read under it here so a mint that
        # landed while this call waited is the one that gets used.
        async with sessions() as session:
            repo = ProjectRepository(session)
            project = await repo.get(project_id)
            if project is None or gateway is None:
                return None
            await repo.lock_settings(project)
            s = project.settings or {}
            key = s.get(slot.key)
            patch: dict[str, object] = {}
            remove: tuple[str, ...] = ()
            if not isinstance(key, str) or not key:
                key = await gateway.mint_project_key(project_id, purpose=slot.purpose)
                if not key:
                    return None
                patch[slot.key] = key
            target = await service._gateway_budget_target(session, project_id)
            # No target clears the brake: a budget left on the key from
            # before would refuse calls the platform now admits.
            if s.get(slot.budget) != target:
                if await gateway.set_key_budget(key, target):
                    if target is None:
                        remove = (slot.budget,)
                    else:
                        patch[slot.budget] = target
            if patch or remove:
                await repo.merge_settings(project, patch, remove=remove)
                await session.commit()
        return {"ANTHROPIC_AUTH_TOKEN": key}
    except Exception:  # never fail a turn on admin plumbing
        logger.exception("gateway project-env failed for %s", project_id)
        return None


@dataclass(frozen=True)
class TaskGatewayKey:
    project_id: uuid.UUID
    task_id: uuid.UUID
    owner_handle: str

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        statement = select(TaskGatewayAccount).where(
            TaskGatewayAccount.project_id == self.project_id,
            TaskGatewayAccount.conversation_id == self.task_id,
            TaskGatewayAccount.owner_handle == self.owner_handle,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None or not row.gateway_key:
            return None
        return row.gateway_key, row.checkpoint

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        account = await session.get(
            TaskGatewayAccount, (self.task_id, self.owner_handle)
        )
        assert account is not None
        account.checkpoint = checkpoint

    async def payer(self, session: AsyncSession) -> Payer:
        return await payer_for_project(session, self.project_id)


@dataclass(frozen=True)
class MachineGatewayKey:
    """The key a conversation's sandboxes call models on for one budget holder
    (``""`` when the project pays), apart from the session's own and capped
    per day on the gateway (`_machine_gateway_key`). The project pays, as for
    the task's key."""

    project_id: uuid.UUID
    conversation_id: uuid.UUID
    owner_handle: str

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        statement = select(MachineGatewayAccount).where(
            MachineGatewayAccount.project_id == self.project_id,
            MachineGatewayAccount.conversation_id == self.conversation_id,
            MachineGatewayAccount.owner_handle == self.owner_handle,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None or not row.gateway_key:
            return None
        return row.gateway_key, row.checkpoint

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        account = await session.get(
            MachineGatewayAccount, (self.conversation_id, self.owner_handle)
        )
        assert account is not None
        account.checkpoint = checkpoint

    async def payer(self, session: AsyncSession) -> Payer:
        return await payer_for_project(session, self.project_id)


@dataclass(frozen=True)
class ProjectGatewayKey:
    """A project's key on the gateway, kept in the project's settings beside
    its checkpoint: the rooms' key, or with ``dream`` the memory
    consolidation's. The project pays for what either spends."""

    project_id: uuid.UUID
    dream: bool = False

    @property
    def _slot(self) -> _KeySlot:
        return _DREAM_SLOT if self.dream else _ROOM_SLOT

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        project = await ProjectRepository(session).get(self.project_id)
        if project is None:
            return None
        if for_update:
            # Another backend process can charge during a rollout.
            await session.refresh(project, with_for_update=True)
        s = project.settings or {}
        key = s.get(self._slot.key)
        if not isinstance(key, str) or not key:
            return None  # nothing ever routed → nothing to meter
        ckpt = s.get(self._slot.ckpt)
        return key, ckpt if isinstance(ckpt, dict) else None

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        repo = ProjectRepository(session)
        project = await repo.get(self.project_id)
        assert project is not None
        await repo.merge_settings(project, {self._slot.ckpt: checkpoint})

    async def payer(self, session: AsyncSession) -> Payer:
        return await payer_for_project(session, self.project_id)


#: A turn's spend is read when it ends, and once more this much later if the
#: gateway had not logged it yet.
TURN_RETRY_AFTER = (3.0,)
#: How long after that a turn whose spend still had not surfaced is read again.
LATE_S = 20.0


async def charge_turn_spend(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    topic_id: uuid.UUID | None,
    turn_id: uuid.UUID,
) -> list[AgentUsage] | None:
    """Charge what the project's key spent for a turn, one row per model,
    reading again once if the gateway has not logged it yet."""
    async with sessions() as session:
        execution = await CreditBudgets(session).execution(turn_id, project_id)
        if topic_id is not None and await session.get(Conversation, topic_id) is None:
            topic_id = None
    key = (
        TaskGatewayKey(project_id, execution.conversation_id, execution.owner_handle)
        if execution is not None
        else ProjectGatewayKey(project_id)
    )
    charge = Charge(
        model=model_roles.model("agent_model"), topic_id=topic_id, turn_id=turn_id
    )
    usages = await settle(sessions, gateway, key, charge, retry_after=TURN_RETRY_AFTER)
    # What the conversation's sandboxes spent since, on their own keys: every
    # holder's, so none is left uncharged if the turn's holder is another.
    conversation_id = execution.conversation_id if execution else topic_id
    if conversation_id is None:
        return usages
    async with sessions() as session:
        holders = (
            await session.scalars(
                select(MachineGatewayAccount.owner_handle).where(
                    MachineGatewayAccount.project_id == project_id,
                    MachineGatewayAccount.conversation_id == conversation_id,
                )
            )
        ).all()
    landed = list(usages or [])
    for holder in holders:
        machine = MachineGatewayKey(project_id, conversation_id, holder)
        landed += await charge_new_spend(sessions, gateway, machine, charge) or []
    return landed or None


def _schedule_deferred_drain(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    background_tasks: set[asyncio.Task],
    project_id: uuid.UUID,
    topic_id: uuid.UUID | None,
    turn_id: uuid.UUID,
) -> None:
    """Late-landing spend rows: charge again in the background. Strong-ref'd so
    the pending commit can't be GC'd."""

    async def _later() -> None:
        await asyncio.sleep(LATE_S)
        usages = await charge_turn_spend(
            sessions, gateway, project_id, topic_id, turn_id
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
