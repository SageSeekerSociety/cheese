"""Gateway L1/L2 wired into a turn: the sandbox env gets the project's VIRTUAL
key (minted once, persisted), and a turn that ends with usage=0 (the hooks
backends) gets its REAL usage drained from the gateway spend log into the
usage table."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.agent.profiles import (
    TIER_BYO,
    TIER_DEFAULT,
    AgentProfile,
    ProfileRegistry,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute


def _replace_chat_sleep(monkeypatch, sleep):
    from app.domain.agent import chat

    # Replacing the shared module's sleep also stalls the TestClient's loop monitor.
    monkeypatch.setattr(
        chat, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": sleep})
    )


def _on_a_machine() -> ClaudeCodeRuntime:
    """A backend whose machine is somewhere else, so it builds that machine's
    model environment where the machine is."""
    return ClaudeCodeRuntime(DeviceChannel())


def _leases_a_machine() -> ClaudeCodeRuntime:
    """The Cloud backend, built the way the app wires it."""
    return ClaudeCodeRuntime(
        CloudChannel(
            configured=True,
            ensure_topic_cloud=AsyncMock(),
            read_topic_cloud=AsyncMock(),
        )
    )


def _in_this_process() -> ClaudeCodeRuntime:
    """A backend with no machine of its own: nothing out there will build it a
    model environment, so the platform hands it the resolved profile env."""
    return StubChannel().runtime


class QuietScreen(StubChannel):
    """A turn that reports NO usage — the interactive reality."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id, session_id="s1")
        self.acknowledges(topic_id, prompt)
        self.hook(
            topic_id,
            hook_event_name="Stop",
            session_id="s1",
            last_assistant_message="ok",
        )


class FakeGateway:
    """LlmGateway lookalike: records calls, serves canned spend."""

    def __init__(self) -> None:
        self.minted: list[uuid.UUID] = []
        self.budgets: list[tuple[str, float]] = []
        # date → {model: (prompt, completion, usd)}. Model-keyed on purpose: a
        # flat total is what swallowed mimo into the default model's row.
        self.days: dict[str, dict[str, tuple[int, int, float]]] = {}

    async def mint_project_key(self, project_id):
        self.minted.append(project_id)
        return f"sk-virt-{len(self.minted)}"

    async def set_key_budget(self, key, max_budget_usd):
        self.budgets.append((key, max_budget_usd))
        return True

    lag_calls = 0  # >0 → the first N spend reads see no rows yet (log lag)

    async def daily_spend_by_model(self, key, date):
        if self.lag_calls > 0:
            self.lag_calls -= 1
            return {}
        return {
            name: gw.ModelSpend(name, p, c, usd)
            for name, (p, c, usd) in self.days.get(date, {}).items()
        }

    async def daily_spend(self, key, date):
        by = await self.daily_spend_by_model(key, date)
        return gw.DailySpend(
            date=date,
            prompt_tokens=sum(m.prompt_tokens for m in by.values()),
            completion_tokens=sum(m.completion_tokens for m in by.values()),
            spend_usd=sum(m.spend_usd for m in by.values()),
        )


class FailingMintGateway(FakeGateway):
    async def mint_project_key(self, project_id):
        self.minted.append(project_id)
        return None


async def _mk_service(factory, tmp_path, fake, profiles=None, screen=None):
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(screen or QuietScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        profiles=profiles,
        gateway=fake,  # duck-typed LlmGateway
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()
    return svc, factory, pid, tid


@pytest.mark.anyio
async def test_virtual_key_minted_once_and_injected(business_db_factory, tmp_path):
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)

    kw1, route1 = await svc._model_kwargs(pid, _in_this_process())
    kw2, route2 = await svc._model_kwargs(pid, _in_this_process())
    # Injected into the turn env both times, but minted exactly once (persisted).
    assert route1 == route2 == "gateway"
    assert kw1["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"
    assert kw2["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"
    assert fake.minted == [pid]
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    assert (project.settings or {}).get("llm_gateway_key") == "sk-virt-1"


@pytest.mark.anyio
async def test_gateway_pool_refuses_turn_when_project_key_cannot_be_minted(
    business_db_factory, tmp_path
):
    fake = FailingMintGateway()
    svc, _factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)

    with pytest.raises(AppError, match="project-scoped key"):
        await svc._model_kwargs(pid, _in_this_process())

    assert fake.minted == [pid]


@pytest.mark.anyio
async def test_gateway_disabled_does_not_require_a_virtual_key(
    business_db_factory, tmp_path
):
    svc, _factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, None)

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())

    assert route == "native"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
    }


@pytest.mark.anyio
async def test_non_pool_profile_keeps_its_own_credentials(
    business_db_factory, tmp_path, monkeypatch
):
    from app.core.config import settings

    pool_url = "http://pool.example"
    monkeypatch.setattr(settings, "anthropic_base_url", pool_url)
    profiles = ProfileRegistry(
        [
            AgentProfile(
                "default",
                "Pool",
                TIER_DEFAULT,
                "pool-model",
                pool_url,
                "shared-pool-key",
            ),
            AgentProfile(
                "byo",
                "BYO",
                TIER_BYO,
                "byo-model",
                "https://byo.example",
                "byo-key",
            ),
        ],
        "default",
    )
    fake = FailingMintGateway()
    svc, factory, pid, _tid = await _mk_service(
        business_db_factory, tmp_path, fake, profiles=profiles
    )
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        project.settings = {"execution_profile": "byo"}
        await session.commit()

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())

    assert route == "native"
    assert kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "byo-key"
    assert fake.minted == []


@pytest.mark.anyio
async def test_zero_usage_turn_gets_real_usage_from_gateway(
    business_db_factory, tmp_path, monkeypatch
):
    async def _no_sleep(_s):
        return None

    _replace_chat_sleep(monkeypatch, _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    # Simulate LiteLLM's async log lag: the first drain sees nothing — the
    # settle-retry must pick the rows up so per-turn attribution still lands.
    fake.lag_calls = 1
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage
    from app.domain.usage.repositories import UsageRepository

    async with factory() as session:
        agg = await UsageRepository(session).for_project(pid)
        project = await ProjectRepository(session).get(pid)
        rows = (
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            )
            .scalars()
            .all()
        )
    assert (agg["input_tokens"], agg["output_tokens"]) == (120, 30)
    assert agg["cost_usd"] == pytest.approx(0.02)
    # Landed under the model that actually spent it — not the default.
    assert {r.model for r in rows} == {"claude-sonnet-5"}
    # Checkpoint persisted (v2, per-model) → a second drain consumes nothing.
    assert project is not None
    ckpt = (project.settings or {}).get("llm_gateway_usage_ckpt")
    assert ckpt and set(ckpt["models"]) == {"claude-sonnet-5"}
    seen = ckpt["models"]["claude-sonnet-5"]
    assert (seen["prompt"], seen["completion"]) == (120, 30)


@pytest.mark.anyio
async def test_settling_usage_allows_key_lookup_and_keeps_checkpoint_current(
    business_db_factory, tmp_path, monkeypatch
):
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, _factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)
    fake.lag_calls = 1
    settling = asyncio.Event()
    resume = asyncio.Event()

    async def wait_for_rows(_seconds):
        settling.set()
        await resume.wait()

    _replace_chat_sleep(monkeypatch, wait_for_rows)
    pending = asyncio.create_task(svc._drain_gateway_usage(pid))
    try:
        await asyncio.wait_for(settling.wait(), timeout=2)
        # New inference must proceed while an earlier turn waits for spend rows.
        assert await asyncio.wait_for(svc.project_gateway_key(pid), timeout=1) == key
        other = await asyncio.wait_for(svc._drain_gateway_usage(pid), timeout=1)
        assert other is not None
        assert [(u.model, u.input_tokens, u.output_tokens) for u in other] == [
            ("claude-sonnet-5", 120, 30)
        ]
    finally:
        resume.set()
        retried = await pending
    # The paused drain must read the checkpoint advanced by the other drain.
    assert retried is None


@pytest.mark.anyio
async def test_key_lookup_does_not_queue_behind_the_gateway_lock(
    business_db_factory, tmp_path
):
    """A project whose key is already minted and in step is answered WITHOUT
    taking `_gateway_lock`.

    That lock is process-wide (`get_chat_service` is `@lru_cache`d, so there is
    one ChatService per process) and the metering proxy asks for a key per
    request. A lookup that takes it queues every admission on the box behind
    whichever holder is slow, and that wait is what `/llm/admission`'s p95 is
    made of — measured on dev 2026-09-23 at 8.9 s, and reproduced with 20
    concurrent admissions for a single project fanning out into a 5.8 s tail.
    """
    fake = FakeGateway()
    svc, _factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)

    # Some other caller is inside the lock: a mint, a re-price, or a drain.
    await svc._gateway_lock.acquire()
    try:
        assert await asyncio.wait_for(svc.project_gateway_key(pid), timeout=1) == key
    finally:
        svc._gateway_lock.release()

    # And answering from the persisted row wrote nothing back.
    assert fake.minted == [pid]
    assert fake.budgets == []


@pytest.mark.anyio
async def test_a_slow_spend_read_does_not_stall_key_lookup(
    business_db_factory, tmp_path
):
    """The spend read is an HTTP round trip to LiteLLM. A drain must not hold
    `_gateway_lock` across it, or one slow `/spend/logs` stalls every admission
    for as long as the gateway takes to answer."""
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)

    reading = asyncio.Event()
    release = asyncio.Event()

    async def slow_daily_spend_by_model(api_key, date):
        reading.set()
        await release.wait()
        return await FakeGateway.daily_spend_by_model(fake, api_key, date)

    fake.daily_spend_by_model = slow_daily_spend_by_model  # type: ignore[method-assign]
    pending = asyncio.create_task(svc._drain_gateway_usage(pid))
    try:
        await asyncio.wait_for(reading.wait(), timeout=2)
        assert await asyncio.wait_for(svc.project_gateway_key(pid), timeout=1) == key
    finally:
        release.set()

    # The drain itself still lands its usage and advances the checkpoint.
    drained = await pending
    assert drained is not None
    assert [(u.model, u.input_tokens, u.output_tokens) for u in drained] == [
        ("claude-sonnet-5", 120, 30)
    ]
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    ckpt = (project.settings or {}).get("llm_gateway_usage_ckpt")
    assert ckpt and ckpt["models"]["claude-sonnet-5"]["prompt"] == 120
    assert ckpt["models"]["claude-sonnet-5"]["completion"] == 30


@pytest.mark.anyio
async def test_credits_burn_by_real_spend_not_raw_tokens(
    business_db_factory, tmp_path, monkeypatch
):
    """Gateway-routed turns consume credits from the REAL spend (cache discounts
    included), not the flat token rate — so 120+30 tokens at ¥0.02 with a
    ¥0.08/credit price burns 0.25 credits, not tokens/10k = 0.015."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "llm_gateway_credit_usd", 0.08)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    from app.domain.usage.repositories import ComputeGrantRepository

    async with factory() as session:
        await ComputeGrantRepository(session).grant(
            project_id=pid, source_task_id=None, credits_total=100.0
        )
        await session.commit()

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    async with factory() as session:
        summary = await ComputeGrantRepository(session).summary(pid)
    assert summary["credits_used"] == pytest.approx(0.02 / 0.08)  # 0.25, spend-priced


@pytest.mark.anyio
async def test_late_spend_rows_land_via_deferred_drain(
    business_db_factory, tmp_path, monkeypatch
):
    """LiteLLM batch-writes spend logs; when both the turn-end drain AND its
    settle retry see nothing, a deferred background drain lands the usage row
    shortly after instead of holding the turn (or losing the row)."""

    async def _no_sleep(_s):
        return None

    _replace_chat_sleep(monkeypatch, _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (80, 20, 0.01)}
    fake.lag_calls = 2  # first drain AND its settle retry both miss
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    # Let the deferred task run (its sleep is patched away).
    import asyncio as _asyncio

    for _ in range(10):
        pending = [t for t in svc._background_tasks if not t.done()]
        if not pending:
            break
        await _asyncio.gather(*pending, return_exceptions=True)

    from app.domain.usage.repositories import UsageRepository

    async with factory() as session:
        agg = await UsageRepository(session).for_project(pid)
    assert (agg["input_tokens"], agg["output_tokens"]) == (80, 20)


@pytest.mark.anyio
async def test_a_project_on_the_gateway_stays_there_when_the_subscription_arrives(
    business_db_factory, tmp_path
):
    """Deploying the subscription must not move a project that chose the gateway.

    What holds it there is the project's own supply setting, not a model saved
    on one of its agents: an agent is a participant, and which pool a project
    runs on is not a property of a participant (结论 44).

    Provider URLs and keys remain on the backend, away from the machine.
    """
    from app.core.config import settings as app_settings

    pool_url = "http://pool.example"
    profiles = ProfileRegistry(
        [
            AgentProfile(
                "default", "Pool", TIER_DEFAULT, "pool-model", pool_url, "pool-key"
            )
        ],
        "default",
    )
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(
        business_db_factory, tmp_path, fake, profiles=profiles
    )
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        project.settings = {**(project.settings or {}), "supply": "gateway"}
        await session.commit()

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())

    assert route == "gateway"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
    }
    assert kwargs["model"] == app_settings.agent_model
    assert fake.minted == []  # the key is swapped in per request by /llm


@pytest.mark.anyio
async def test_subscription_route_follows_the_capability_not_the_backend_name(
    business_db_factory, tmp_path, monkeypatch
):
    """A machine builds the metering-proxy env itself, so nothing about the
    transport travels from here — a channel that does NOT build one must keep
    its profile/gateway routing rather than fall through with no env at all and
    run on the backend process's own inherited credentials."""
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        project.settings = {**(project.settings or {}), "supply": "subscription"}
        await session.commit()

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())
    assert route == "subscription"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
    }
    assert kwargs["model"] == "claude-sonnet-5"

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())
    assert route == "gateway"  # profile/gateway logic, not the subscription
    assert kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"


@pytest.mark.anyio
async def test_a_leased_machine_takes_the_same_supply_as_an_enrolled_one(
    business_db_factory, tmp_path, monkeypatch
):
    """Cloud and device are two answers to WHICH machine, never to which supply.

    Both reach a machine over the same link and both assemble its model
    environment there, so a subscription deployment must meter them the same way
    and hand them the same --model alias. When this was decided by the backend's
    NAME, a Cloud turn matched neither name: it was shipped the profile env and
    a virtual gateway key, launched `claude --model <the pool's model>` at a
    subscription that does not serve it, and its usage row named the gateway's
    meter while its traffic went through the proxy — counted once in each.
    """
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        project.settings = {**(project.settings or {}), "supply": "subscription"}
        await session.commit()

    device_kwargs, device_route = await svc._model_kwargs(pid, _on_a_machine())
    cloud_kwargs, cloud_route = await svc._model_kwargs(pid, _leases_a_machine())

    assert cloud_route == device_route == "subscription"
    assert cloud_kwargs == device_kwargs
    assert cloud_kwargs["model"] == "claude-sonnet-5"
    assert set(cloud_kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
    }
    assert fake.minted == []  # no gateway key is minted for either


@pytest.mark.anyio
async def test_a_turn_runs_as_its_agent_and_an_ongoing_turn_keeps_its_snapshot(
    business_db_factory, tmp_path, monkeypatch
):
    from app.domain.agent_instance.configuration import AgentConfiguration
    from app.domain.agent_instance.services import AgentInstanceService

    svc, factory, pid, tid = await _mk_service(
        business_db_factory, tmp_path, FakeGateway()
    )
    async with factory() as session:
        agents = AgentInstanceService(session)
        agent = await agents.create(
            project_id=pid,
            handle="reviewer",
            type_name=None,
            display_name="Reviewer",
            configuration=AgentConfiguration(body="Original role"),
        )
        snapshot = agents.resolved(agent)
        await agents.configure(agent, AgentConfiguration(body="Edited role"))
        await session.commit()
        assert await agents.system_prompt(snapshot) == "Original role"
        # A later turn addressed to the same teammate resolves it afresh.
        edited = agents.resolved(agent)

    # A room does not have an agent: which one a turn runs as comes with the
    # turn. An ongoing turn keeps the snapshot it started with; the next one
    # sees the edit; a turn nobody addressed runs as the project's default.
    current, _ = await svc._model_kwargs(
        pid, _on_a_machine(), tid, agent=snapshot, acting_agent="reviewer"
    )
    following, _ = await svc._model_kwargs(
        pid, _on_a_machine(), tid, agent=edited, acting_agent="reviewer"
    )
    default, _ = await svc._model_kwargs(pid, _on_a_machine())
    # 换的是角色，不是模型：一个房间的主线永远走项目默认，谁来答都一样（结论 3）。
    # 这三行在这个测试里的用处正是说明「agent 变了，模型不变」。
    assert current["model"] == following["model"] == default["model"]
    assert default["model"] == settings.agent_model
    assert (
        current["env"]["CHEESE_AGENT_CONFIG"] != following["env"]["CHEESE_AGENT_CONFIG"]
    )
    repeated, _ = await svc._model_kwargs(
        pid, _on_a_machine(), tid, agent=edited, acting_agent="reviewer"
    )
    assert (
        repeated["env"]["CHEESE_AGENT_CONFIG"]
        == following["env"]["CHEESE_AGENT_CONFIG"]
    )
    monkeypatch.setattr(svc, "_agent_handle", AsyncMock(return_value="ops"))
    same_turn, _ = await svc._model_kwargs(
        pid, _on_a_machine(), tid, agent=snapshot, acting_agent="reviewer"
    )
    assert same_turn == current
    assert same_turn["agent_handle"] == "reviewer"
    different_author, _ = await svc._model_kwargs(
        pid, _on_a_machine(), tid, agent=edited
    )
    assert different_author["agent_handle"] == "ops"
    assert different_author["model"] == following["model"]
    assert (
        different_author["env"]["CHEESE_AGENT_CONFIG"]
        != following["env"]["CHEESE_AGENT_CONFIG"]
    )


@pytest.mark.anyio
async def test_usage_rows_record_their_route(
    business_db_factory, tmp_path, monkeypatch
):
    async def _no_sleep(_s):
        return None

    _replace_chat_sleep(monkeypatch, _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            ).scalars()
        )
    assert rows and all(r.route == "gateway" for r in rows)


@pytest.mark.anyio
async def test_one_drain_covers_several_models_without_absorbing_them(
    business_db_factory, tmp_path, monkeypatch
):
    """The regression this whole split exists for: one project's day mixes
    mimo and claude on the SAME key, and both must keep their own row in
    `by_model`. Collapsing them under `settings.agent_model` is exactly how
    mimo went uncounted on the dashboard."""

    async def _no_sleep(_s):
        return None

    _replace_chat_sleep(monkeypatch, _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {
        "mimo-v2.6-pro": (400, 100, 0.004),
        "claude-sonnet-5": (120, 30, 0.02),
    }
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            )
            .scalars()
            .all()
        )
    by = {r.model: r for r in rows}
    assert set(by) == {"mimo-v2.6-pro", "claude-sonnet-5"}
    assert (by["mimo-v2.6-pro"].input_tokens, by["mimo-v2.6-pro"].output_tokens) == (
        400,
        100,
    )
    assert by["mimo-v2.6-pro"].cost_usd == pytest.approx(0.004)
    assert (
        by["claude-sonnet-5"].input_tokens,
        by["claude-sonnet-5"].output_tokens,
    ) == (
        120,
        30,
    )


@pytest.mark.anyio
async def test_zero_usage_report_lands_as_unmetered_not_metered_zero(
    business_db_factory, tmp_path
):
    """Interactive Claude Code's Stop hook decodes to an all-zero usage — that
    is 'unknown', not 'this turn was free'. Without a meter for the route, the
    row must say unmetered."""

    class ZeroUsageScreen(StubChannel):
        def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
            del reply
            self.starts(topic_id, session_id="s1")
            self.acknowledges(topic_id, prompt)
            self.hook(
                topic_id,
                hook_event_name="Stop",
                session_id="s1",
                last_assistant_message="ok",
                usage={"input": 0, "output": 0},
            )

    svc, factory, pid, tid = await _mk_service(
        business_db_factory, tmp_path, None, screen=ZeroUsageScreen()
    )

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            ).scalars()
        )
    assert rows and all(r.kind == "chat:unmetered" for r in rows)
    assert all(r.total_tokens == 0 for r in rows)
