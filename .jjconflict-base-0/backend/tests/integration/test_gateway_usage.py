"""Gateway L1/L2 wired into a turn: the sandbox env gets the project's VIRTUAL
key (minted once, persisted), and a turn that ends with usage=0 (the hooks
backends) gets its REAL usage drained from the gateway spend log into the
usage table."""

import uuid

import pytest

from app.core.errors import AppError
from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
from app.domain.agent.profiles import (
    TIER_BYO,
    TIER_DEFAULT,
    AgentProfile,
    ProfileRegistry,
)
from app.domain.agent.service import AgentResult, AgentService
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService


class QuietAgent(AgentService):
    """A turn that reports NO usage — the tmux/device reality."""

    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        yield AgentResult(text="ok", session_id="s1", usage=None)


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


async def _mk_service(factory, tmp_path, fake, profiles=None):
    svc = ChatService(
        session_factory=factory,
        agent=QuietAgent(),
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

    kw1, route1 = await svc._model_kwargs(pid, "local-docker")
    kw2, route2 = await svc._model_kwargs(pid, "local-docker")
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
        await svc._model_kwargs(pid, "local-docker")

    assert fake.minted == [pid]


@pytest.mark.anyio
async def test_gateway_disabled_does_not_require_a_virtual_key(client, tmp_path):
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, None)

    kwargs, route = await svc._model_kwargs(pid, "local-docker")

    assert route == "native"
    assert "env" not in kwargs


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

    kwargs, route = await svc._model_kwargs(pid, "local-docker")

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
async def test_device_turn_route_follows_the_deployment_supply(client, tmp_path):
    """A device turn's model env belongs to the device provider — the profile
    env (box-local gateway URL + a real key) must never reach the machine. The
    ROUTE follows where its traffic actually goes (#325 G2): the /llm gateway
    without a subscription, the metering proxy with one — same as the local
    container, so moving a topic to a device never swaps its model."""
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

    kwargs, route = await svc._model_kwargs(pid, "device")

    assert route == "gateway"
    assert "env" not in kwargs  # no profile env, no virtual key on the machine
    assert fake.minted == []  # the key is swapped in per request by /llm
    assert app_settings.subscription_enabled is False  # and with the flag on:

    import unittest.mock

    with unittest.mock.patch.object(app_settings, "subscription_enabled", True):
        kwargs, route = await svc._model_kwargs(pid, "device")
    assert route == "subscription"
    assert "env" not in kwargs  # the device provider builds the proxy env itself
    assert kwargs["model"] == ""  # the subscription's default, no --model flag


@pytest.mark.anyio
async def test_subscription_route_applies_only_to_the_hooks_providers(
    client, tmp_path, monkeypatch
):
    """subscription_enabled names a capability only the hooks providers (tmux,
    device) implement — they build the metering-proxy env themselves. The sdk
    provider under that flag used to fall through with no env and run on the
    backend's own inherited credentials — it must keep its profile/gateway
    routing instead."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "subscription_enabled", True)
    fake = FakeGateway()
    svc, _factory, pid, _tid = await _mk_service(client.test_factory, tmp_path, fake)

    kwargs, route = await svc._model_kwargs(pid, "tmux-hooks")
    assert route == "subscription"
    assert "env" not in kwargs  # the tmux provider builds the proxy env itself

    kwargs, route = await svc._model_kwargs(pid, "local-docker")
    assert route == "gateway"  # profile/gateway logic, not the subscription
    assert kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"


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
    from app.domain.agent.service import AgentUsage

    class ZeroUsageAgent(AgentService):
        def __init__(self) -> None:
            super().__init__(model="stub")

        async def stream_reply(self, **_kw):
            yield AgentResult(text="ok", session_id="s1", usage=AgentUsage())

    svc, factory, pid, tid = await _mk_service(client.test_factory, tmp_path, None)
    # Rebuild the pool around the zero-usage agent (the ctor built it already).
    from app.domain.agent.compute import ComputePool

    svc._compute = ComputePool.local(
        agent=ZeroUsageAgent(),
        workspace_root=str(tmp_path / "ws"),
        sandbox_enabled=False,
    )

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass

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
