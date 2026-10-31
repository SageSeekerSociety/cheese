"""The gateway a memory consolidation's calls go to, stood in for.

Each pass of a dream is one chat completion on the project's dream key
(`memory_ledger._dream_scope`). `answer_dream_calls` puts a stand-in behind
`httpx` that answers every completion with what ``answer`` makes of the
request, and hands back the requests it was asked.
"""

import json
from collections.abc import Callable

import httpx

from app.core.config import settings
from app.domain.agent import dream_usage

Answer = Callable[[dict], dict]


def nothing_to_change(_asked: dict) -> dict:
    return {"write": {}, "delete": [], "summary": "本来就够紧"}


def answer_dream_calls(monkeypatch, answer: Answer = nothing_to_change) -> list[dict]:
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        body = json.loads(request.content)
        asked.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(answer(body), ensure_ascii=False)
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    # Nothing is drained from the stand-in gateway: do not wait for it.
    monkeypatch.setattr(dream_usage, "TURN_RETRY_AFTER", ())
    monkeypatch.setattr(dream_usage, "LATE_S", 0.0)
    return asked


def system_of(asked: dict) -> str:
    return next(m["content"] for m in asked["messages"] if m["role"] == "system")


def prompt_of(asked: dict) -> str:
    content = next(m["content"] for m in asked["messages"] if m["role"] == "user")
    return content if isinstance(content, str) else json.dumps(content)
