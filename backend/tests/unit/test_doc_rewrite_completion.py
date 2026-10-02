"""What the model answers for a selection rewrite is only ever replacement
text: an answer that tries anything else is refused, not applied."""

import json
import uuid

import httpx
import pytest

from app.api.doc_rewrite import complete
from app.core.errors import ValidationError


def _gateway(message: dict, *, cost: str | None = "0.01"):
    def answer(_request: httpx.Request) -> httpx.Response:
        headers = {"x-litellm-response-cost": cost} if cost is not None else {}
        return httpx.Response(
            200,
            headers=headers,
            json={
                "id": "completion",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", **message},
                    }
                ],
            },
        )

    return httpx.MockTransport(answer)


async def _complete(transport) -> str:
    return await complete(
        base="https://gateway.example",
        key="project-key",
        model="fixture",
        request_id=uuid.uuid4(),
        context={"selection": "范围", "instruction": "说得更具体"},
        transport=transport,
    )


@pytest.mark.anyio
async def test_the_replacement_is_what_the_model_answered():
    content = json.dumps({"replacement": "交付边界"}, ensure_ascii=False)
    assert await _complete(_gateway({"content": content})) == "交付边界"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        {
            "content": '{"replacement": "x"}',
            "tool_calls": [{"function": {"name": "cheese_doc_set"}}],
        },
        {"content": "直接写成一段话，不是 JSON"},
        {"content": '{"replacement": ["x"]}'},
    ],
)
async def test_an_answer_that_is_not_just_replacement_text_is_refused(message):
    with pytest.raises(ValidationError):
        await _complete(_gateway(message))
