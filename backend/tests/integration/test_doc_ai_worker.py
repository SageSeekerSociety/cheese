"""Real project catalog, budget and HTTP transport, with no room runner."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.api.doc_ai_runtime import authorize_work, reconcile_usage
from app.core.config import settings
from app.domain.doc_ai.completion import complete
from app.domain.doc_ai.routing import project_binding
from app.domain.doc_ai.services import DocAiService
from app.domain.doc_ai.worker import run_one
from app.domain.project.models import Project
from app.domain.topic.services import TopicService
from app.domain.usage.models import ComputeGrant
from app.domain.usage.repositories import UsageRepository
from app.domain.user.services import user_by_handle
from tests.integration.test_living_doc_journal import seed


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
        doc, _ = await TopicService(session).edit_doc(
            topic_id=room, content="原文", author="alice", expected_version=0
        )
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
            session.add(
                ComputeGrant(project_id=project.id, credits_total=1, credits_used=1)
            )
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
    "supply,empty_budget,reason",
    [
        ("subscription", False, "尚不支持"),
        ("gateway", True, "budget spent"),
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
        session.add(ComputeGrant(project_id=pid, credits_total=100, credits_used=0))
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
