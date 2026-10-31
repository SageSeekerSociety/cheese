"""Gateway L1/L2 wired into a turn: the sandbox env gets the project's VIRTUAL
key (minted once, persisted), and a turn that ends with no usage of its own (a
Claude Code session reports none) gets its REAL usage drained from the gateway
spend log into the usage table."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api import deps as session_turn_deps
from app.core.config import settings
from app.core.errors import AppError
from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.profiles import (
    TIER_BYO,
    TIER_DEFAULT,
    AgentProfile,
    ProfileRegistry,
)
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, finish_turn, stub_compute
from tests.integration.conftest import (
    approve_overseas,
    project_service,
    put_on_plan,
    registered,
)
from tests.support.hang import HANG_S

# The days the fakes answer for are read from `utc_now()`, the moment the drain
# reads too, held still for each test (`pinned_clock`): a run across UTC
# midnight cannot split them.
pytestmark = pytest.mark.usefixtures("pinned_clock")


def _replace_chat_sleep(monkeypatch, sleep):
    from app.domain.agent import chat, gateway_spend, gateway_usage

    # Replacing the shared module's sleep also stalls the TestClient's loop monitor.
    # The turn's waits — the settle-retry before the second spend read, and the
    # deferred drain's twenty seconds — are `gateway_spend.settle`'s, so the
    # replacement goes on each module's own `asyncio` name.
    stub = SimpleNamespace(**{**vars(asyncio), "sleep": sleep})
    for module in (chat, gateway_spend, gateway_usage):
        monkeypatch.setattr(module, "asyncio", stub)


def _on_a_machine() -> RoomSessions:
    """A backend whose machine is somewhere else, so it builds that machine's
    model environment where the machine is."""
    return RoomSessions(DeviceChannel(), CLAUDE_CODE, SessionHost())


def _in_this_process() -> RoomSessions:
    """A backend with no machine of its own: nothing out there will build it a
    model environment, so the platform hands it the resolved profile env."""
    return StubChannel().runtime


class QuietScreen(StubChannel):
    """A turn that reports NO usage — the interactive reality."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.stops(topic_id, "ok")


class FakeGateway:
    """LlmGateway lookalike: records calls, serves canned spend."""

    def __init__(self) -> None:
        self.minted: list[uuid.UUID] = []
        self.budgets: list[tuple[str, float]] = []
        # date → {model: (prompt, completion, usd)}. Model-keyed on purpose: a
        # flat total is what swallowed mimo into the default model's row.
        self.days: dict[str, dict[str, tuple[int, int, float]]] = {}

    async def mint_project_key(self, project_id, *, purpose=None):
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
    async def mint_project_key(self, project_id, *, purpose=None):
        self.minted.append(project_id)
        return None


class WaitingMintGateway(FakeGateway):
    """A mint that parks until the test lets it finish, so a second caller can
    be brought up while the first is still inside its write."""

    def __init__(self) -> None:
        super().__init__()
        self.minting = asyncio.Event()
        self.release = asyncio.Event()

    async def mint_project_key(self, project_id, *, purpose=None):
        self.minting.set()
        await self.release.wait()
        return await FakeGateway.mint_project_key(self, project_id, purpose=purpose)


async def _mk_service(factory, tmp_path, fake, profiles=None, screen=None):
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(screen or QuietScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        profiles=profiles,
        gateway=fake,  # duck-typed LlmGateway
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
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
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CHEESE_MODEL_AGENTS",
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
    await finish_turn(svc, tid)

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
async def test_gateway_daily_delta_larger_than_int32_is_recorded(
    business_db_factory, tmp_path, monkeypatch
):
    async def _no_sleep(_s):
        return None

    _replace_chat_sleep(monkeypatch, _no_sleep)
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"deepseek/deepseek-flash": (2_147_483_648, 20, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await finish_turn(svc, tid)

    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage

    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].input_tokens == 2_147_483_648
    assert rows[0].total_tokens == 2_147_483_668


@pytest.mark.anyio
async def test_settling_usage_allows_key_lookup_and_keeps_checkpoint_current(
    business_db_factory, tmp_path, monkeypatch
):
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, _factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)
    fake.lag_calls = 1
    settling = asyncio.Event()
    resume = asyncio.Event()

    async def wait_for_rows(_seconds):
        settling.set()
        await resume.wait()

    _replace_chat_sleep(monkeypatch, wait_for_rows)
    pending = asyncio.create_task(svc.charge_turn_spend(pid, tid, uuid.uuid4()))
    try:
        await asyncio.wait_for(settling.wait(), timeout=HANG_S)
        # New inference must proceed while an earlier turn waits for spend rows.
        assert (
            await asyncio.wait_for(svc.project_gateway_key(pid), timeout=HANG_S) == key
        )
        other = await asyncio.wait_for(
            svc.charge_turn_spend(pid, tid, uuid.uuid4()), timeout=HANG_S
        )
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
async def test_failed_usage_insert_does_not_advance_gateway_checkpoint(
    business_db_factory, tmp_path, monkeypatch
):
    from app.domain.usage.repositories import UsageRepository

    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    await svc.project_gateway_key(pid)
    turn_id = uuid.uuid4()
    add = UsageRepository.add
    calls = 0

    async def fail_once(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("usage insert failed")
        return await add(self, **kwargs)

    monkeypatch.setattr(UsageRepository, "add", fail_once)
    assert await svc.charge_turn_spend(pid, tid, turn_id) is None
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        aggregate = await UsageRepository(session).for_project(pid)
    assert project is not None
    assert "llm_gateway_usage_ckpt" not in (project.settings or {})
    assert aggregate["total_tokens"] == 0

    assert await svc.charge_turn_spend(pid, tid, turn_id) is not None
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        aggregate = await UsageRepository(session).for_project(pid)
    assert project is not None
    assert (aggregate["input_tokens"], aggregate["output_tokens"]) == (120, 30)
    assert (
        project.settings["llm_gateway_usage_ckpt"]["models"]["claude-sonnet-5"][
            "prompt"
        ]
        == 120
    )


@pytest.mark.anyio
async def test_a_key_in_step_is_answered_without_writing(business_db_factory, tmp_path):
    """Two callers after one project's key, both answered the same one, with
    nothing written back.

    This is the hot path: the metering proxy asks for a key per request. Any
    serialisation put in front of it queues every admission on the box behind
    whichever holder is slow, and that wait is what `/llm/admission`'s p95 is
    made of — measured on dev 2026-09-23 at 8.9 s, and reproduced with 20
    concurrent admissions for a single project fanning out into a 5.8 s tail.
    There is no in-process `_gateway_lock` any more; the read path takes no
    lock at all (design §2.3).
    """
    fake = FakeGateway()
    svc, _factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)
    budgets = list(fake.budgets)

    one, two = await asyncio.wait_for(
        asyncio.gather(svc.project_gateway_key(pid), svc.project_gateway_key(pid)),
        timeout=HANG_S,
    )
    assert one == two == key

    # And answering from the persisted row wrote nothing back.
    assert fake.minted == [pid]
    assert fake.budgets == budgets


@pytest.mark.anyio
async def test_two_writers_mint_one_key_between_them(business_db_factory, tmp_path):
    """Two callers that both find no key mint one between them, and both use
    that one.

    This is what the settings row lock holds up now that `_gateway_lock` is
    gone: the write is read-modify-write on `project.settings`, and without
    `repo.lock_settings` both callers would mint and the project would be left
    with two keys in play. Both callers are brought up while the first is
    still inside its mint, so the second is reading before the first has
    committed anything.
    """
    fake = WaitingMintGateway()
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)

    first = asyncio.create_task(svc._gateway_project_env(pid))
    await asyncio.wait_for(fake.minting.wait(), timeout=HANG_S)
    second = asyncio.create_task(svc._gateway_project_env(pid))
    await asyncio.sleep(0)  # let it read and start waiting on the row lock
    fake.release.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), timeout=HANG_S)

    assert one is not None and two is not None
    assert one["ANTHROPIC_AUTH_TOKEN"] == two["ANTHROPIC_AUTH_TOKEN"]
    assert fake.minted == [pid]
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    minted = one["ANTHROPIC_AUTH_TOKEN"]
    assert (project.settings or {}).get("llm_gateway_key") == minted


@pytest.mark.anyio
async def test_a_slow_spend_read_does_not_stall_key_lookup(
    business_db_factory, tmp_path
):
    """The spend read is an HTTP round trip to LiteLLM. A drain must not hold
    anything across it, or one slow `/spend/logs` stalls every admission for
    as long as the gateway takes to answer."""
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    key = await svc.project_gateway_key(pid)

    reading = asyncio.Event()
    release = asyncio.Event()

    async def slow_daily_spend_by_model(api_key, date):
        reading.set()
        await release.wait()
        return await FakeGateway.daily_spend_by_model(fake, api_key, date)

    fake.daily_spend_by_model = slow_daily_spend_by_model  # type: ignore[method-assign]
    pending = asyncio.create_task(svc.charge_turn_spend(pid, tid, uuid.uuid4()))
    try:
        await asyncio.wait_for(reading.wait(), timeout=HANG_S)
        assert (
            await asyncio.wait_for(svc.project_gateway_key(pid), timeout=HANG_S) == key
        )
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
    included), not the flat token rate — so 120+30 tokens that cost $0.02
    burn 2 credits at a cent each, not tokens/10k = 0.015."""
    fake = FakeGateway()
    fake.days[gw.utc_today()] = {"claude-sonnet-5": (120, 30, 0.02)}
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    from app.domain.usage.ledger import Ledger
    from app.domain.usage.services import UsageService

    async with factory() as session:
        await Ledger(session).grant_earmark(
            project_id=pid, source_task_id=None, credits_total=100.0
        )
        await session.commit()

    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await finish_turn(svc, tid)

    async with factory() as session:
        summary = await UsageService(session).project_credits(pid)
    assert summary["credits_used"] == pytest.approx(2.0)  # spend-priced


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
    await finish_turn(svc, tid)
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
    """Deploying the subscription must not move a project off the gateway.

    What holds it there is the catalogue default: the pool follows the model a
    turn is bound to, and a subscription model is never the default, so a
    project that picked nothing runs on the gateway's agent model. It is not a
    model saved on one of its agents either: an agent is a participant, and
    which pool a project runs on is not a property of a participant (结论 44).

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

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())

    assert route == "gateway"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CHEESE_MODEL_AGENTS",
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
        project.settings = {**(project.settings or {}), "default_model": "sonnet"}
        await put_on_plan(session, project.team_id, "reserve")
        await approve_overseas(session, project.team_id)
        await session.commit()

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())
    assert route == "subscription"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CHEESE_MODEL_AGENTS",
    }
    assert kwargs["model"] == "claude-sonnet-5"

    kwargs, route = await svc._model_kwargs(pid, _in_this_process())
    assert route == "gateway"  # profile/gateway logic, not the subscription
    assert kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"


@pytest.mark.anyio
async def test_a_turn_on_a_machine_is_metered_by_the_subscription_proxy(
    business_db_factory, tmp_path, monkeypatch
):
    """A session whose machine is elsewhere — a person's or the platform's —
    has that machine's model environment built there, so a subscription
    deployment meters it through the proxy and hands it the subscription's
    --model alias; no gateway key is minted for it.
    """
    fake = FakeGateway()
    svc, factory, pid, _tid = await _mk_service(business_db_factory, tmp_path, fake)
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        project.settings = {**(project.settings or {}), "default_model": "sonnet"}
        await put_on_plan(session, project.team_id, "reserve")
        await approve_overseas(session, project.team_id)
        await session.commit()

    kwargs, route = await svc._model_kwargs(pid, _on_a_machine())

    assert route == "subscription"
    assert kwargs["model"] == "claude-sonnet-5"
    assert set(kwargs["env"]) == {
        "CHEESE_AGENT_CONFIG",
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CHEESE_MODEL_AGENTS",
    }
    assert fake.minted == []  # no gateway key is minted for it


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
    await finish_turn(svc, tid)

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
    await finish_turn(svc, tid)

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
    business_db_factory, tmp_path, monkeypatch
):
    """A Claude Code session's turn ends with no usage of its own — that is
    'unknown', not 'this turn was free'. Without a meter for the route, the row
    must say unmetered."""
    svc, factory, pid, tid = await _mk_service(
        business_db_factory, tmp_path, None, screen=QuietScreen()
    )

    from app.core.config import settings

    ran_on = settings.agent_model
    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    # The deployment default is not what the turn ran on; the row must name
    # the model the turn was launched with, whatever the default says by then.
    monkeypatch.setattr(settings, "agent_model", "not-what-the-turn-ran-on")
    await finish_turn(svc, tid)

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
    assert {r.model for r in rows} == {ran_on}


@pytest.mark.anyio
async def test_a_route_marked_without_mid_conversation_system_launches_without_it(
    business_db_factory, tmp_path, monkeypatch
):
    """A gateway model marked ``supports_mid_conversation_system: false`` loses
    the system messages Claude Code puts mid-conversation (the skill listing
    among them), so a launch on it asks for them in the first user message. A
    model nobody marked keeps Claude Code's default."""
    from app.domain.agent import gateway_catalog
    from app.domain.agent.gateway import GatewayModel

    monkeypatch.setattr(settings, "agent_model", "keeps-it")

    class Catalog:
        async def models(self):
            return [
                GatewayModel("keeps-it", "Keeps it", True, True),
                GatewayModel(
                    "loses-it", "Loses it", True, True, mid_conversation_system=False
                ),
            ]

    gateway_catalog.reset()
    await gateway_catalog.refresh(Catalog())
    try:
        svc, factory, pid, _tid = await _mk_service(
            business_db_factory, tmp_path, FakeGateway()
        )
        unmarked, _ = await svc._model_kwargs(pid, _in_this_process())

        async with factory() as session:
            project = await ProjectRepository(session).get(pid)
            assert project is not None
            project.settings = {**(project.settings or {}), "default_model": "loses-it"}
            await session.commit()
        marked, _ = await svc._model_kwargs(pid, _in_this_process())
    finally:
        gateway_catalog.reset()

    assert "CLAUDE_CODE_MODEL_CAPABILITIES" not in unmarked["env"]
    assert (
        marked["env"]["CLAUDE_CODE_MODEL_CAPABILITIES"] == "loses-it=-mid_conv_system"
    )
    # A screen launched before the mark is reopened with it.
    assert (
        marked["env"]["CHEESE_AGENT_CONFIG"] != unmarked["env"]["CHEESE_AGENT_CONFIG"]
    )


@pytest.mark.anyio
async def test_cache_reads_the_gateway_reports_reach_the_ledger_row(
    business_db_factory, tmp_path
):
    """The gateway prices a cached prompt cheaper and says how much of it was
    read from cache; the row the turn lands keeps that, so the cache-hit share
    of gateway spend can be read off the ledger like any other route's."""
    import hashlib

    import httpx
    from sqlalchemy import select

    from app.domain.usage.models import ResourceUsage

    def daily_activity(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-virtual"})
        if request.url.path != "/user/daily/activity":
            return httpx.Response(200, json={})
        mine = hashlib.sha256(b"sk-virtual").hexdigest()
        metrics = {
            "prompt_tokens": 1_170_000,
            "completion_tokens": 4_000,
            "spend": 2.02,
            "cache_read_input_tokens": 1_080_000,
            "cache_creation_input_tokens": 30_000,
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "date": request.url.params["start_date"],
                        "breakdown": {
                            "models": {
                                "gpt-6-astra": {
                                    "api_key_breakdown": {mine: {"metrics": metrics}}
                                }
                            }
                        },
                    }
                ]
            },
        )

    gateway = gw.LlmGateway(
        "http://gw", "mk", transport=httpx.MockTransport(daily_activity)
    )
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, gateway)
    await svc.project_gateway_key(pid)

    await svc.charge_turn_spend(pid, tid, uuid.uuid4())

    async with factory() as session:
        rows = list(
            await session.scalars(
                select(ResourceUsage).where(ResourceUsage.project_id == pid)
            )
        )
    assert [
        (r.model, r.route, r.input_tokens, r.cache_read_tokens, r.cache_write_tokens)
        for r in rows
    ] == [("gpt-6-astra", "gateway", 1_170_000, 1_080_000, 30_000)]


@pytest.mark.anyio
async def test_the_dashboard_counts_every_call_the_gateway_answered(
    business_db_factory, tmp_path
):
    """The platform dashboard's count of calls covers gateway traffic call by
    call: a drain that lands many calls as one ledger row still counts each of
    them, and a call the gateway already reported is not counted again by the
    next drain."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    import httpx

    from app.domain.usage.repositories import UsageRepository

    answered = {"calls": 37, "prompt_tokens": 9_000, "spend": 0.5}

    def daily_activity(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-virtual"})
        if request.url.path != "/user/daily/activity":
            return httpx.Response(200, json={})
        mine = hashlib.sha256(b"sk-virtual").hexdigest()
        metrics = {
            "prompt_tokens": answered["prompt_tokens"],
            "completion_tokens": 0,
            "spend": answered["spend"],
            "successful_requests": answered["calls"],
            "failed_requests": 2,
            "api_requests": answered["calls"] + 2,
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "date": request.url.params["start_date"],
                        "breakdown": {
                            "models": {
                                "gpt-6-astra": {
                                    "api_key_breakdown": {mine: {"metrics": metrics}}
                                }
                            }
                        },
                    }
                ]
            },
        )

    gateway = gw.LlmGateway(
        "http://gw", "mk", transport=httpx.MockTransport(daily_activity)
    )
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, gateway)
    await svc.project_gateway_key(pid)
    now = datetime.now(UTC)

    async def dashboard_calls() -> int:
        async with factory() as session:
            totals = await UsageRepository(session).platform_totals(
                since=now - timedelta(days=1), until=now + timedelta(days=1)
            )
        return totals["calls"]

    await svc.charge_turn_spend(pid, tid, uuid.uuid4())
    assert await dashboard_calls() == 37

    answered.update(calls=40, prompt_tokens=9_900, spend=0.55)
    await svc.charge_turn_spend(pid, tid, uuid.uuid4())
    assert await dashboard_calls() == 40


@pytest.mark.anyio
async def test_a_model_the_gateway_adds_is_a_subagent_type_from_the_next_launch(
    business_db_factory, tmp_path, monkeypatch
):
    """A model an operator adds to the gateway while sessions run can be named
    for a subagent once the session is next opened: its launch carries the
    model as a subagent type, and the session's configuration moves, which is
    what reopens a running session at its next task boundary."""
    from app.core.config import settings as app_settings
    from app.domain.agent import gateway_catalog

    def routed(*names):
        rows = [
            {
                "model_name": name,
                "litellm_params": {"model": f"openai/{name}"},
                "model_info": {
                    "cheese_selectable": True,
                    "input_cost_per_token": 0.000001,
                    "output_cost_per_token": 0.000001,
                },
            }
            for name in names
        ]
        answering = gw.LlmGateway(
            "http://gw",
            "mk",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": rows})
            ),
        )
        return gateway_catalog.refresh(answering)

    monkeypatch.setattr(app_settings, "agent_model", "glm-5.2")
    gateway_catalog.reset()
    try:
        svc, _factory, pid, _tid = await _mk_service(
            business_db_factory, tmp_path, FakeGateway()
        )
        await routed("glm-5.2")
        before, _ = await svc._model_kwargs(pid, _on_a_machine())
        assert "model-kimi-k3.md" not in json.loads(
            before["env"]["CHEESE_MODEL_AGENTS"]
        )

        await routed("glm-5.2", "kimi-k3")
        after, _ = await svc._model_kwargs(pid, _on_a_machine())
    finally:
        gateway_catalog.reset()

    agents = json.loads(after["env"]["CHEESE_MODEL_AGENTS"])
    assert "name: kimi-k3\n" in agents["model-kimi-k3.md"]
    assert "model: kimi-k3\n" in agents["model-kimi-k3.md"]
    assert after["env"]["CHEESE_AGENT_CONFIG"] != before["env"]["CHEESE_AGENT_CONFIG"]


@pytest.mark.anyio
async def test_a_session_on_a_gateway_model_is_told_the_window_the_gateway_declares(
    business_db_factory, tmp_path, monkeypatch
):
    """Claude Code assumes 200K for a model id it does not know and compacts at
    about 167K, so a session on a gateway model is told the window the gateway
    declares for it. Each model compacts where its provider recommends, or at
    its own window; a subagent on a model the gateway declares no window for
    compacts at the 200K Claude Code would have assumed, not the session's."""
    from app.core.config import settings as app_settings
    from app.domain.agent import gateway_catalog
    from app.domain.agent.harness.claude_code.session_launch import (
        session_settings,
    )

    def row(name, **window):
        return {
            "model_name": name,
            "litellm_params": {"model": f"anthropic/{name}"},
            "model_info": {
                "cheese_selectable": True,
                "input_cost_per_token": 0.000001,
                "output_cost_per_token": 0.000001,
                **window,
            },
        }

    rows = [
        row(
            "deepseek-flash",
            max_input_tokens=920_000,
            cheese_auto_compact_window=786_432,
        ),
        row("gpt-6.1-sol", max_input_tokens=922_000),
        row("kimi-k3"),
        # Past what Claude Code accepts as an auto-compact window.
        row("gemini-4-argon", max_input_tokens=1_048_576),
        # Claude Code knows a claude- model's window itself.
        row("claude-sonnet-5-5", max_input_tokens=1_000_000),
        # Routed but not a menu item: no session runs on it.
        row("glm-4.5", cheese_selectable=False, max_input_tokens=128_000),
    ]
    answering = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": rows})
        ),
    )
    monkeypatch.setattr(app_settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    try:
        await gateway_catalog.refresh(answering)
        svc, factory, pid, _tid = await _mk_service(
            business_db_factory, tmp_path, FakeGateway()
        )
        declared, _ = await svc._model_kwargs(pid, _on_a_machine())
        compaction = session_settings().get("modelSettings", {})

        async with factory() as session:
            project = await ProjectRepository(session).get(pid)
            assert project is not None
            project.settings = {**(project.settings or {}), "default_model": "kimi-k3"}
            await session.commit()
        undeclared, _ = await svc._model_kwargs(pid, _on_a_machine())
    finally:
        gateway_catalog.reset()

    assert declared["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "920000"
    # No window declared: Claude Code keeps the one it assumes.
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in undeclared["env"]
    assert compaction["deepseek-flash"] == {"autoCompactWindow": 786_432}
    assert compaction["gpt-6.1-sol"] == {"autoCompactWindow": 922_000}
    assert compaction["kimi-k3"] == {"autoCompactWindow": 200_000}
    assert compaction["gemini-4-argon"] == {"autoCompactWindow": 1_000_000}
    assert "claude-sonnet-5-5" not in compaction
    assert "glm-4.5" not in compaction
    # The settings are part of what a launch is compared by; the gateway's row
    # order (not by id above) must not change them.
    assert list(compaction) == sorted(compaction)
