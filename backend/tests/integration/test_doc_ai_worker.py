"""Real project catalog, budget and HTTP transport, with no room runner."""

import json

import httpx
import pytest

from app.core.config import settings
from app.domain.doc_ai.completion import complete
from app.domain.doc_ai.routing import project_binding
from app.domain.doc_ai.services import DocAiService
from app.domain.doc_ai.worker import run_one
from app.domain.project.models import Project
from app.domain.topic.services import TopicService
from app.domain.usage.models import ComputeGrant
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
        row = await DocAiService(session).create(
            project_id=project.id,
            room_id=room,
            document_id=doc.id,
            actor="user:1",
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

    assert await run_one(factory, invoke, meter)
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

    assert await run_one(factory, invoke, meter)
    assert not calls
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "failed" and reason in row.error
        assert row.binding == bound
        assert (await TopicService(session).get_doc(room)).doc_version == 1
