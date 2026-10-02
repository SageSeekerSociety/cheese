"""Real project catalog, budget and HTTP transport, with no room runner."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.api.doc_ai_runtime import authorize_work, reconcile_usage
from app.core.config import settings
from app.domain.doc_ai.completion import complete
from app.domain.doc_ai.models import DocAiAttempt
from app.domain.doc_ai.routing import project_binding
from app.domain.doc_ai.services import DocAiService
from app.domain.doc_ai.worker import run_one
from app.domain.project.models import Project
from app.domain.topic.services import TopicService
from app.domain.usage.ledger import Ledger
from app.domain.usage.models import ComputeGrant, Plan
from app.domain.usage.repositories import UsageRepository
from app.domain.user.services import user_by_handle
from tests.integration.conftest import put_on_plan
from tests.integration.test_living_doc_journal import seed
from tests.support.living_doc import write_doc


async def _spent_earmark(session, project_id) -> None:
    """A project whose only credits are earmarked and already spent: its
    team's plan issues nothing."""
    await session.execute(
        update(Plan).where(Plan.key == "free").values(credits_per_period=0)
    )
    pack = await Ledger(session).grant_earmark(
        project_id=project_id, source_task_id=None, credits_total=1
    )
    pack.credits_used = 1


async def pending(factory, *, supply="gateway", empty_budget=False):
    room = await seed(factory)
    async with factory() as session:
        topic = await TopicService(session).get_or_404(room)
        project = await session.get(Project, topic.project_id)
        project.settings = {
            **(project.settings or {}),
            "supply": supply,
            "default_model": settings.agent_model if supply == "gateway" else "sonnet",
        }
        if supply != "gateway":
            # Free leaves the Claude subscription models out; Reserve has them.
            await put_on_plan(session, project.team_id, "reserve")
        doc, _ = await write_doc(session, room, "原文", "alice")
        bound = await project_binding(session, room)
        user = await user_by_handle(session, "alice")
        row = await DocAiService(session).create(
            project_id=project.id,
            room_id=room,
            document_id=doc.id,
            actor=f"user:{user.id}",
            kind="ask",
            question="解释",
            base_version=1,
            source=doc.content,
            selection=None,
            binding=bound,
        )
        if empty_budget:
            await _spent_earmark(session, project.id)
        await session.commit()
        return room, row.id, bound


@pytest.mark.anyio
async def test_worker_uses_real_binding_and_http_no_tools_before_persisting_answer(
    business_db_factory,
):
    factory = business_db_factory
    room, request_id, bound = await pending(factory)
    sent = []
    metered = []

    def gateway(request):
        body = json.loads(request.content)
        sent.append(body)
        assert body["model"] == bound["wire_model"]
        assert "tools" not in body
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.01"},
            json={
                "id": "actual-http-fixture",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"解释结果"}',
                        },
                    }
                ],
            },
        )

    async def invoke(lease):
        return await complete(
            lease,
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(gateway),
        )

    async def meter(lease):
        metered.append(lease.request_id)

    assert await run_one(factory, invoke, meter, authorize_work)
    assert len(sent) == 1 and metered == [request_id]
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "succeeded" and row.answer == "解释结果"
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cost_present,stage", [(False, "cost_header"), (True, "result_schema")]
)
async def test_contract_failure_persists_safe_stage_and_available_usage_once(
    business_db_factory, cost_present, stage
):
    factory = business_db_factory
    room, request_id, bound = await pending(factory)
    sent = []
    metered = []

    def gateway(request):
        sent.append(request)
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.01"} if cost_present else {},
            json={
                "id": "contract-receipt",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"private","extra":"private"}',
                        },
                    }
                ],
            },
        )

    async def invoke(lease):
        return await complete(
            lease,
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(gateway),
        )

    async def meter(lease):
        metered.append(lease.request_id)

    assert await run_one(factory, invoke, meter, authorize_work)
    assert not await run_one(factory, invoke, meter, authorize_work)
    assert len(sent) == 1 and metered == [request_id]
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        attempt = await session.scalar(
            select(DocAiAttempt).where(DocAiAttempt.request_id == request_id)
        )
        assert row.state == "failed" and row.answer is None
        assert row.binding == bound
        assert row.error == attempt.error
        assert row.error == f"模型返回的文档结果不符合无工具数据合同 [doc_ai:{stage}]"
        assert (attempt.usage is not None) == cost_present
        if cost_present:
            assert attempt.usage == {
                "model": bound["wire_model"],
                "input_tokens": 10,
                "output_tokens": 4,
                "cost_usd": 0.01,
                "upstream_id": "contract-receipt",
            }
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["http_status:429", "timeout", "transport"])
async def test_http_failure_is_durable_unpriced_metered_and_not_reinvoked(
    business_db_factory, failure
):
    factory = business_db_factory
    room, request_id, bound = await pending(factory)
    sent = []
    metered = []

    def gateway(request):
        sent.append(request)
        assert json.loads(request.content)["model"] == bound["wire_model"]
        if failure == "timeout":
            raise httpx.ReadTimeout("private-token-url", request=request)
        if failure == "transport":
            raise httpx.ConnectError("private-token-url", request=request)
        return httpx.Response(429, text="private-provider-body")

    async def invoke(lease):
        return await complete(
            lease,
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(gateway),
        )

    async def meter(lease):
        metered.append(lease.request_id)

    assert await run_one(factory, invoke, meter, authorize_work)
    assert not await run_one(factory, invoke, meter, authorize_work)
    assert len(sent) == 1 and metered == [request_id]
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        attempt = await session.scalar(
            select(DocAiAttempt).where(DocAiAttempt.request_id == request_id)
        )
        assert row.state == "failed" and row.answer is None
        assert row.binding == bound
        assert row.error == attempt.error
        assert row.error == f"文档模型调用失败 [doc_ai:{failure}]"
        assert attempt.usage is None
        assert await DocAiService(session).proposal_id(request_id) is None
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "supply,empty_budget,reason",
    [
        ("subscription", False, "尚不支持"),
        ("gateway", True, "额度已用完"),
    ],
)
async def test_subscription_or_budget_refusal_is_durable_without_fallback(
    business_db_factory,
    supply,
    empty_budget,
    reason,
):
    factory = business_db_factory
    room, request_id, bound = await pending(
        factory, supply=supply, empty_budget=empty_budget
    )
    calls = []

    async def invoke(lease):
        calls.append(lease)
        raise AssertionError("refused work must not reach completion")

    async def meter(lease):
        pass

    assert await run_one(factory, invoke, meter, authorize_work)
    assert not calls
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "failed" and reason in row.error
        assert row.binding == bound
        assert row.meter_after is None
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["archive-room", "archive-project", "revoke"])
async def test_background_generation_rechecks_live_human_access(
    business_db_factory, change
):
    factory = business_db_factory
    room, request_id, _ = await pending(factory)
    async with factory() as session:
        topic = await TopicService(session).get_or_404(room)
        if change == "archive-room":
            topic.archived_at = datetime.now(UTC)
        elif change == "archive-project":
            project = await session.get(Project, topic.project_id)
            project.archived_at = datetime.now(UTC)
        else:
            # Private room membership is exact; project ownership is no bypass.
            topic.is_private = True
            from sqlalchemy import delete

            from app.domain.topic.models import TopicMembership

            await session.execute(
                delete(TopicMembership).where(TopicMembership.topic_id == room)
            )
        await session.commit()

    async def forbidden(_):
        raise AssertionError("revoked work must not call HTTP or meter")

    assert await run_one(factory, forbidden, forbidden, authorize_work)
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "failed" and row.error
        assert row.meter_after is None
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["budget", "cancel", "revoke"])
async def test_key_wait_cannot_bypass_changed_admission_or_lease(
    business_db_factory, monkeypatch, change
):
    from app.api import doc_ai_runtime as runtime

    factory = business_db_factory
    room, request_id, _ = await pending(factory)
    monkeypatch.setattr(runtime, "async_session_factory", factory)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "https://gateway.example")
    calls = []

    class KeyLookup:
        async def project_gateway_key(self, project_id):
            async with factory() as session:
                if change == "budget":
                    await _spent_earmark(session, project_id)
                elif change == "cancel":
                    await DocAiService(session).cancel(room, request_id)
                else:
                    from sqlalchemy import delete

                    from app.domain.topic.models import TopicMembership

                    topic = await TopicService(session).get_or_404(room)
                    topic.is_private = True
                    await session.execute(
                        delete(TopicMembership).where(TopicMembership.topic_id == room)
                    )
                await session.commit()
            return "project-key"

    async def completion(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("changed access/admission must not reach network")

    async def meter(_):
        pass

    monkeypatch.setattr(runtime, "get_chat_service", lambda: KeyLookup())
    monkeypatch.setattr(runtime, "complete", completion)
    assert await run_one(factory, runtime.invoke, meter, authorize_work)
    assert not calls
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == ("cancelled" if change == "cancel" else "failed")
        if change == "budget":
            assert "额度已用完" in row.error
        assert (await TopicService(session).get_doc(room)).content == "原文"
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
async def test_executable_completion_fails_with_usage_but_no_proposal_or_doc_effect(
    business_db_factory,
):
    from app.domain.doc_ai.models import DocAiAttempt

    factory = business_db_factory
    room, request_id, _ = await pending(factory)

    def gateway(_):
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.01"},
            json={
                "id": "malicious-completion",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"执行覆盖"}',
                            "tool_calls": [{"function": {"name": "cheese_doc_set"}}],
                        },
                    }
                ],
            },
        )

    async def invoke(lease):
        return await complete(
            lease,
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(gateway),
        )

    async def meter(_):
        pass

    assert await run_one(factory, invoke, meter, authorize_work)
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "failed" and row.answer is None
        assert await DocAiService(session).proposal_id(request_id) is None
        attempt = await session.scalar(
            select(DocAiAttempt).where(DocAiAttempt.request_id == request_id)
        )
        assert attempt.finished_at is not None
        assert attempt.usage["input_tokens"] == 10
        assert attempt.usage["cost_usd"] == 0.01
        doc = await TopicService(session).get_doc(room)
        assert doc.content == "原文" and doc.doc_version == 1


@pytest.mark.anyio
async def test_crash_before_settle_and_late_spend_reconcile_without_double_charge(
    business_db_factory, tmp_path
):
    from app.domain.agent import gateway as gw
    from app.domain.agent.chat import ChatService
    from tests.conftest import stub_compute
    from tests.integration.test_gateway_usage import FakeGateway

    factory = business_db_factory
    room, request_id, _ = await pending(factory)
    fake = FakeGateway()
    chat = ChatService(
        session_factory=factory,
        compute=stub_compute(),
        base_system_prompt="",
        workspace_root=str(tmp_path / "ws"),
        gateway=fake,
    )
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        pid = row.project_id
        await Ledger(session).grant_earmark(
            project_id=pid, source_task_id=None, credits_total=100
        )
        await session.commit()
    await chat.project_gateway_key(pid)

    async def crash(_):
        # Cancellation bypasses Exception handling, like losing the process.
        raise asyncio.CancelledError()

    async def drain(lease):
        await chat._drain_gateway_usage(pid, room, lease.request_id)

    with pytest.raises(asyncio.CancelledError):
        await run_one(factory, crash, drain, authorize_work)
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "running" and row.meter_after is not None
    fake.days[gw.utc_today()] = {settings.agent_model: (100, 20, 0.04)}
    await reconcile_usage(factory, drain)
    async with factory() as session:
        usage = await UsageRepository(session).for_project(pid)
        grant = await session.scalar(
            select(ComputeGrant).where(ComputeGrant.project_id == pid)
        )
        charged = grant.credits_used
        assert charged > 0
        assert (usage["input_tokens"], usage["output_tokens"]) == (100, 20)
        row = await DocAiService(session).get(room, request_id)
        row.meter_after = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    # A normal room drain racing/repeating the same spend owns the same checkpoint.
    await chat._drain_gateway_usage(pid, room, request_id)
    await reconcile_usage(factory, drain)
    async with factory() as session:
        grant = await session.scalar(
            select(ComputeGrant).where(ComputeGrant.project_id == pid)
        )
        assert grant.credits_used == charged
        assert (await TopicService(session).get_doc(room)).doc_version == 1
