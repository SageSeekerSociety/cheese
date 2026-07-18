"""Gateway L1/L2 wired into a turn: the sandbox env gets the project's VIRTUAL
key (minted once, persisted), and a turn that ends with usage=0 (the hooks
backends) gets its REAL usage drained from the gateway spend log into the
usage table."""

import uuid

import pytest

from app.domain.agent import gateway as gw
from app.domain.agent.chat import ChatService
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


async def _mk_service(factory, tmp_path, fake):
    svc = ChatService(
        session_factory=factory,
        agent=QuietAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
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

    kw1, routed1 = await svc._model_kwargs(pid)
    kw2, routed2 = await svc._model_kwargs(pid)
    # Injected into the turn env both times, but minted exactly once (persisted).
    assert routed1 and routed2
    assert kw1["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"
    assert kw2["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-virt-1"
    assert fake.minted == [pid]
    async with factory() as session:
        project = await ProjectRepository(session).get(pid)
    assert project is not None
    assert (project.settings or {}).get("llm_gateway_key") == "sk-virt-1"


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
