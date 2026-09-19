"""Gateway L1/L2 wired into a turn: the sandbox env gets the project's VIRTUAL
key (minted once, persisted), and a turn that ends with usage=0 (the hooks
backends) gets its REAL usage drained from the gateway spend log into the
usage table."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

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
        self.days: dict[str, tuple[int, int, float]] = {}

    async def mint_project_key(self, project_id):
        self.minted.append(project_id)
        return f"sk-virt-{len(self.minted)}"

    async def set_key_budget(self, key, max_budget_usd):
        self.budgets.append((key, max_budget_usd))
        return True

    lag_calls = 0  # >0 → the first N daily_spend calls return nothing (log lag)

    async def daily_spend(self, key, date):
        if self.lag_calls > 0:
            self.lag_calls -= 1
            return gw.DailySpend(
                date=date, prompt_tokens=0, completion_tokens=0, spend_usd=0.0
            )
        p, c, usd = self.days.get(date, (0, 0, 0.0))
        return gw.DailySpend(
            date=date, prompt_tokens=p, completion_tokens=c, spend_usd=usd
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
async def test_virtual_key_minted_once_and_injected(client, tmp_path):
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)

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
    client, tmp_path
):
    fake = FailingMintGateway()
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)

    with pytest.raises(AppError, match="project-scoped key"):
        await svc._model_kwargs(pid, _in_this_process())

    assert fake.minted == [pid]


@pytest.mark.anyio
async def test_gateway_disabled_does_not_require_a_virtual_key(client, tmp_path):
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, None)

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())

    assert route == "native"
    assert set(kwargs["env"]) == {"CHEESE_AGENT_CONFIG"}


@pytest.mark.anyio
async def test_non_pool_profile_keeps_its_own_credentials(
    client, tmp_path, monkeypatch
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
        client.test_factory, tmp_path, fake, profiles=profiles
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
    client, tmp_path, monkeypatch
):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("app.domain.agent.chat.asyncio.sleep", _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = (120, 30, 0.02)
    # Simulate LiteLLM's async log lag: the first drain sees nothing — the
    # settle-retry must pick the rows up so per-turn attribution still lands.
    fake.lag_calls = 1
    svc, factory, pid, tid = await _mk_service(client.test_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)

    from app.domain.usage.repositories import UsageRepository

    async with factory() as session:
        agg = await UsageRepository(session).for_project(pid)
        project = await ProjectRepository(session).get(pid)
    assert (agg["input_tokens"], agg["output_tokens"]) == (120, 30)
    assert agg["cost_usd"] == pytest.approx(0.02)
    # Checkpoint persisted → a second drain would consume nothing new.
    assert project is not None
    ckpt = (project.settings or {}).get("llm_gateway_usage_ckpt")
    assert ckpt and ckpt["prompt"] == 120 and ckpt["completion"] == 30


@pytest.mark.anyio
async def test_settling_usage_allows_key_lookup_and_keeps_checkpoint_current(
    client, tmp_path, monkeypatch
):
    fake = FakeGateway()
    fake.days[gw.utc_today()] = (120, 30, 0.02)
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)
    fake.lag_calls = 1
    settling = asyncio.Event()
    resume = asyncio.Event()

    async def wait_for_rows(_seconds):
        settling.set()
        await resume.wait()

    monkeypatch.setattr("app.domain.agent.chat.asyncio.sleep", wait_for_rows)
    pending = asyncio.create_task(svc._drain_gateway_usage(pid))
    try:
        await asyncio.wait_for(settling.wait(), timeout=2)
        # New inference must proceed while an earlier turn waits for spend rows.
        assert await asyncio.wait_for(svc.project_gateway_key(pid), timeout=1) == key
        other = await asyncio.wait_for(svc._drain_gateway_usage(pid), timeout=1)
        assert other is not None
        assert (other.input_tokens, other.output_tokens) == (120, 30)
    finally:
        resume.set()
        retried = await pending
    # The paused drain must read the checkpoint advanced by the other drain.
    assert retried is None


@pytest.mark.anyio
async def test_credits_burn_by_real_spend_not_raw_tokens(client, tmp_path, monkeypatch):
    """Gateway-routed turns consume credits from the REAL spend (cache discounts
    included), not the flat token rate — so 120+30 tokens at ¥0.02 with a
    ¥0.08/credit price burns 0.25 credits, not tokens/10k = 0.015."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "llm_gateway_credit_usd", 0.08)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = (120, 30, 0.02)
    svc, factory, pid, tid = await _mk_service(client.test_factory, tmp_path, fake)

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
async def test_late_spend_rows_land_via_deferred_drain(client, tmp_path, monkeypatch):
    """LiteLLM batch-writes spend logs; when both the turn-end drain AND its
    settle retry see nothing, a deferred background drain lands the usage row
    shortly after instead of holding the turn (or losing the row)."""

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("app.domain.agent.chat.asyncio.sleep", _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = (80, 20, 0.01)
    fake.lag_calls = 2  # first drain AND its settle retry both miss
    svc, factory, pid, tid = await _mk_service(client.test_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    # Let the deferred task run (its sleep is patched away).
    import asyncio as _asyncio

    for _ in range(10):
        pending = [t for t in svc._memory_tasks if not t.done()]
        if not pending:
            break
        await _asyncio.gather(*pending, return_exceptions=True)

    from app.domain.usage.repositories import UsageRepository

    async with factory() as session:
        agg = await UsageRepository(session).for_project(pid)
    assert (agg["input_tokens"], agg["output_tokens"]) == (80, 20)


@pytest.mark.anyio
async def test_device_turn_keeps_its_saved_gateway_model_when_subscription_is_enabled(
    client, tmp_path
):
    """Adding subscription supply preserves the saved API model and gateway route.

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
    svc, _factory, pid, _tid = await _mk_service(
        client.test_factory, tmp_path, fake, profiles=profiles
    )

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())

    assert route == "gateway"
    assert set(kwargs["env"]) == {"CHEESE_AGENT_CONFIG"}
    assert kwargs["model"] == app_settings.agent_model
    assert fake.minted == []  # the key is swapped in per request by /llm
    assert app_settings.subscription_enabled is False  # and with the flag on:

    import unittest.mock

    with unittest.mock.patch.object(app_settings, "subscription_enabled", True):
        subscription_kwargs, subscription_route = await svc._model_kwargs(
            pid, _on_a_machine()
        )

    assert subscription_route == route == "gateway"
    assert subscription_kwargs == kwargs
    assert fake.minted == []


@pytest.mark.anyio
async def test_subscription_route_follows_the_capability_not_the_backend_name(
    client, tmp_path, monkeypatch
):
    """subscription_enabled names a capability a backend has to IMPLEMENT — it
    builds the metering-proxy env itself, so nothing travels from here. A
    backend without that transport used to fall through under the same flag with
    no env at all and run on the backend process's own inherited credentials; it
    must keep its profile/gateway routing instead."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    fake = FakeGateway()
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())
    assert route == "subscription"
    assert set(kwargs["env"]) == {"CHEESE_AGENT_CONFIG"}
    assert kwargs["model"] == "claude-sonnet-5"

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())
    assert route == "gateway"  # profile/gateway logic, not the subscription
    assert kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"


@pytest.mark.anyio
async def test_a_leased_machine_takes_the_same_supply_as_an_enrolled_one(
    client, tmp_path, monkeypatch
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
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        from app.domain.agent_instance.configuration import AgentConfiguration
        from app.domain.agent_instance.services import AgentInstanceService

        agents = AgentInstanceService(session)
        agent = await agents.materialize_default(project)
        await agents.configure(
            agent, AgentConfiguration(**{**agent.configuration, "model": "opus"})
        )
        await session.commit()

    device_kwargs, device_route = await svc._model_kwargs(pid, _on_a_machine())
    cloud_kwargs, cloud_route = await svc._model_kwargs(pid, _leases_a_machine())

    assert cloud_route == device_route == "subscription"
    assert cloud_kwargs == device_kwargs
    assert cloud_kwargs["model"] == "claude-opus-5"
    assert set(cloud_kwargs["env"]) == {"CHEESE_AGENT_CONFIG"}
    assert fake.minted == []  # no gateway key is minted for either


@pytest.mark.anyio
async def test_a_turn_runs_as_its_agent_and_an_ongoing_turn_keeps_its_snapshot(
    client, tmp_path, monkeypatch
):
    from app.core.config import settings
    from app.domain.agent_instance.configuration import AgentConfiguration
    from app.domain.agent_instance.services import AgentInstanceService

    monkeypatch.setattr(settings, "subscription_enabled", True)
    svc, factory, pid, tid = await _mk_service(
        client.test_factory, tmp_path, FakeGateway()
    )
    async with factory() as session:
        agents = AgentInstanceService(session)
        agent = await agents.create(
            project_id=pid,
            handle="reviewer",
            type_name=None,
            display_name="Reviewer",
            configuration=AgentConfiguration(model="opus", body="Original role"),
        )
        snapshot = agents.resolved(agent)
        await agents.configure(
            agent, AgentConfiguration(model="fable", body="Edited role")
        )
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
    assert current["model"] == "claude-opus-5"
    assert following["model"] == "claude-fable-5"
    assert default["model"] == "claude-sonnet-5"
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
async def test_usage_rows_record_their_route(client, tmp_path, monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("app.domain.agent.chat.asyncio.sleep", _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = (120, 30, 0.02)
    svc, factory, pid, tid = await _mk_service(client.test_factory, tmp_path, fake)

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
async def test_zero_usage_report_lands_as_unmetered_not_metered_zero(client, tmp_path):
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
        client.test_factory, tmp_path, None, screen=ZeroUsageScreen()
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
