"""Actual HTTP boundary offers no tools and rejects executable/truncated data."""

import json
import uuid

import httpx
import pytest

from app.domain.doc_ai.completion import InvalidCompletion, complete
from app.domain.doc_ai.services import Lease


def lease(kind="ask"):
    return Lease(
        uuid.uuid4(),
        1,
        kind,
        "解释",
        "😀原文",
        None,
        {"wire_model": "chosen-model", "supply": "gateway"},
        uuid.uuid4(),
        uuid.uuid4(),
    )


def response(content, **overrides):
    message = {"role": "assistant", "content": content}
    message.update(overrides.pop("message", {}))
    return {
        "id": "upstream-123",
        "model": "provider-model",
        "usage": {"prompt_tokens": 17, "completion_tokens": 8},
        "choices": [
            {
                "message": message,
                "finish_reason": overrides.pop("finish_reason", "stop"),
            }
        ],
        **overrides,
    }


@pytest.mark.anyio
async def test_exact_model_project_key_and_no_tools_on_wire():
    work = lease()
    seen = []

    def upstream(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["model"] == "chosen-model"
        assert "tools" not in body and "functions" not in body
        assert request.headers["authorization"] == "Bearer project-key"
        assert request.headers["idempotency-key"] == f"doc-ai:{work.request_id}:1"
        return httpx.Response(
            200,
            json=response('{"answer":"cheese_doc_set is text, not a call"}'),
            headers={"x-litellm-response-cost": "0.012"},
        )

    result = await complete(
        work,
        base="https://gateway.example",
        key="project-key",
        transport=httpx.MockTransport(upstream),
    )
    assert len(seen) == 1
    assert result.result.answer == "cheese_doc_set is text, not a call"
    assert result.usage.input_tokens == 17 and result.usage.cost_usd == 0.012


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        response(
            '{"answer":"ignored"}',
            message={"tool_calls": [{"function": {"name": "cheese_doc_set"}}]},
        ),
        response('{"answer":"ignored"}', message={"function_call": {"name": "curl"}}),
        response('{"answer":"cut', finish_reason="length"),
        response('{"answer":"ok","target":"another doc"}'),
        response('{"answer":"ok","replacement":"unauthorized"}'),
        response("curl -X PUT /doc"),
    ],
)
async def test_malicious_or_invalid_completion_is_not_an_effect(payload):
    def upstream(request):
        return httpx.Response(
            200, json=payload, headers={"x-litellm-response-cost": "0.01"}
        )

    with pytest.raises(InvalidCompletion) as caught:
        await complete(
            lease(),
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(upstream),
        )
    assert caught.value.usage.upstream_id == "upstream-123"


@pytest.mark.anyio
async def test_missing_cost_is_unknown_not_free():
    def upstream(request):
        return httpx.Response(200, json=response('{"answer":"ok"}'))

    with pytest.raises(InvalidCompletion):
        await complete(
            lease(),
            base="https://gateway.example",
            key="project-key",
            transport=httpx.MockTransport(upstream),
        )
