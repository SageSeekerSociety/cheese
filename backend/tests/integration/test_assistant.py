"""A person's 芝士 on a task page, over the real HTTP stack, database and Valkey.

The rules pinned here are the ones a person could state without reading the
code: 芝士 answers from the task it was asked about; the answer is kept for the
asker and paid for from their personal credits; a new conversation starts clean;
nobody else can see, list or continue someone's conversation; a person who cannot
open a task cannot ask about it; with no credits left the model is not asked; and
the "my tasks" tool reports only what the asker takes part in.

The gateway is a real local HTTP server answering the way LiteLLM does for an
OpenAI-compatible model, stepping through a script of tool calls and answers.
"""

import asyncio
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from redis.asyncio import from_url
from sqlalchemy import select, update

from app.api.routes import assistant as route
from app.core.config import settings
from app.domain.feature_stats import pricing
from app.domain.task.models import Task, TaskMembership
from app.domain.usage.models import ComputeGrant, ResourceUsage
from app.domain.user.models import User
from tests.conftest import seed_task_with_protocol, seed_user

USAGE = {
    "prompt_tokens": 300,
    "completion_tokens": 20,
    "prompt_tokens_details": {"cached_tokens": 200},
}
RATES = (1e-6, 2e-6, 1e-8)  # input, output, cached input — USD per token
CREDIT_USD = 1e-5


class Gateway:
    """The script: each model request takes the next step; a step is
    ``("tool", name, args)`` or ``("text", answer)``."""

    def __init__(self) -> None:
        self.script: list[tuple] = []
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.startswith("/model/info"):
                    body = {
                        "data": [
                            {
                                "model_name": settings.assistant_model,
                                "litellm_params": {
                                    "input_cost_per_token": RATES[0],
                                    "output_cost_per_token": RATES[1],
                                    "cache_read_input_token_cost": RATES[2],
                                },
                            }
                        ]
                    }
                    self._json(body)
                else:
                    self._json({}, 404)

            def do_POST(self) -> None:
                sent = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.path == "/key/generate":
                    self._json({"key": "sk-assistant"})
                    return
                outer.requests.append(sent)
                step = outer.script.pop(0) if outer.script else ("text", "好的。")
                self._stream(step) if sent.get("stream") else self._whole(step)

            def _json(self, body: dict, status: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _whole(self, step: tuple) -> None:
                if step[0] == "tool":
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{len(outer.requests)}",
                                "type": "function",
                                "function": {
                                    "name": step[1],
                                    "arguments": json.dumps(step[2]),
                                },
                            }
                        ],
                    }
                else:
                    message = {"role": "assistant", "content": step[1]}
                self._json(
                    {
                        "id": "x",
                        "object": "chat.completion",
                        "created": 0,
                        "model": settings.assistant_model,
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": "stop"}
                        ],
                        "usage": USAGE,
                    }
                )

            def _stream(self, step: tuple) -> None:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()

                def chunk(delta: dict | None, finish=None, usage=None) -> None:
                    c = {
                        "id": "x",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": settings.assistant_model,
                        "choices": []
                        if delta is None
                        else [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    if usage:
                        c["usage"] = usage
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())

                if step[0] == "tool":
                    chunk(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{len(outer.requests)}",
                                    "type": "function",
                                    "function": {
                                        "name": step[1],
                                        "arguments": json.dumps(step[2]),
                                    },
                                }
                            ],
                        }
                    )
                    chunk({}, finish="tool_calls")
                else:
                    chunk({"role": "assistant", "content": ""})
                    for piece in (step[1][:4], step[1][4:]):
                        chunk({"content": piece})
                    chunk({}, finish="stop")
                chunk(None, usage=USAGE)
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def gateway(client, monkeypatch: pytest.MonkeyPatch):
    gw = Gateway()
    monkeypatch.setattr(settings, "llm_gateway_admin_base", gw.url)
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    monkeypatch.setattr(settings, "llm_gateway_credit_usd", CREDIT_USD)
    pricing.forget()
    # Answers settle on sessions of their own, after the response.
    monkeypatch.setattr(route, "async_session_factory", client.test_request_factory)
    # TestClient runs each request on its own loop; the cached client cannot follow.
    monkeypatch.setattr(route, "get_redis_client", lambda: from_url(settings.redis_url))
    import redis

    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("assistant:busy:*"):
        r.delete(key)
    yield gw
    gw.close()
    pricing.forget()


def _auth(client, handle: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _task(client, *, name: str = "图像分类基线复现", **fields) -> int:
    task_id = seed_task_with_protocol(client)

    async def dress() -> None:
        async with client.test_factory() as s:
            await s.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(
                    name=name,
                    intro="在 CIFAR-10 上复现 ResNet-18",
                    description="提交代码仓库链接，附一页说明。",
                    **fields,
                )
            )
            await s.commit()

    asyncio.run(dress())
    return task_id


def _start(client, task_id: int, headers: dict) -> str:
    r = client.post(f"/assistant/tasks/{task_id}/conversations", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _ask(client, conversation: str, question: str, headers: dict):
    return client.post(
        f"/assistant/conversations/{conversation}/ask",
        json={"question": question},
        headers=headers,
    )


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _ledger(client, handle: str):
    async def read():
        async with client.test_factory() as s:
            user = (
                await s.execute(select(User).where(User.username == handle))
            ).scalar_one()
            grants = list(
                (
                    await s.execute(
                        select(ComputeGrant).where(ComputeGrant.user_id == user.id)
                    )
                ).scalars()
            )
            usage = list(
                (
                    await s.execute(
                        select(ResourceUsage).where(ResourceUsage.user_id == user.id)
                    )
                ).scalars()
            )
            return user.id, grants, usage

    return asyncio.run(read())


def test_a_question_is_answered_from_the_task_kept_and_paid_for(client, gateway):
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)
    gateway.script = [("text", "要先会 PyTorch。")]

    r = _ask(client, conversation, "做这道题要先会什么？", me)

    assert r.status_code == 200, r.text
    events = _events(r.text)
    assert "".join(d["text"] for e, d in events if e == "delta") == "要先会 PyTorch。"
    assert events[-1] == ("done", {})
    # The request keeps to the shape the gateway and model accept today (the
    # docs assistant's): no strict tool schemas, max_tokens, thinking off.
    sent = gateway.requests[0]
    assert "max_completion_tokens" not in sent and sent["max_tokens"] > 0
    assert sent.get("thinking") == {"type": "disabled"}
    assert all("strict" not in t["function"] for t in sent.get("tools", []))
    # 芝士 was told the task it was asked about.
    told = json.dumps(gateway.requests[0], ensure_ascii=False)
    assert "图像分类基线复现" in told and "提交代码仓库链接" in told

    got = client.get(f"/assistant/conversations/{conversation}", headers=me).json()
    assert [(m["role"], m["text"]) for m in got["data"]["messages"]] == [
        ("user", "做这道题要先会什么？"),
        ("assistant", "要先会 PyTorch。"),
    ]
    assert got["data"]["title"] == "做这道题要先会什么？"

    _, [grant], [spent] = _ledger(client, "asker")
    cost = 100 * RATES[0] + 200 * RATES[2] + 20 * RATES[1]
    assert spent.project_id is None and spent.kind == "assistant"
    assert spent.cost_usd == pytest.approx(cost)
    assert grant.credits_used == pytest.approx(cost / CREDIT_USD)


def test_a_new_conversation_starts_without_the_old_ones_turns(client, gateway):
    me = _auth(client, "asker")
    task_id = _task(client)
    first = _start(client, task_id, me)
    assert _ask(client, first, "学习率一般设多少？", me).status_code == 200

    second = _start(client, task_id, me)
    assert _ask(client, second, "数据增强要不要做？", me).status_code == 200

    sent = json.dumps(gateway.requests[-1], ensure_ascii=False)
    assert "数据增强要不要做" in sent and "学习率一般设多少" not in sent

    listed = client.get(f"/assistant/tasks/{task_id}/conversations", headers=me)
    titles = [c["title"] for c in listed.json()["data"]["conversations"]]
    assert titles == ["数据增强要不要做？", "学习率一般设多少？"]


def test_a_conversation_is_its_owners_alone(client, gateway):
    me = _auth(client, "asker")
    stranger = _auth(client, "stranger")
    task_id = _task(client)
    mine = _start(client, task_id, me)
    assert _ask(client, mine, "从哪里入手？", me).status_code == 200
    asked = len(gateway.requests)

    assert (
        client.get(f"/assistant/conversations/{mine}", headers=stranger).status_code
        == 404
    )
    assert _ask(client, mine, "给我看看他问了什么", stranger).status_code == 404
    listed = client.get(f"/assistant/tasks/{task_id}/conversations", headers=stranger)
    assert listed.json()["data"]["conversations"] == []
    assert len(gateway.requests) == asked


def test_a_person_who_cannot_open_the_task_cannot_ask_about_it(client, gateway):
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)

    # The task goes back to waiting for review: to anyone but its publisher and
    # the board's managers it no longer exists.
    async def unpublish() -> None:
        async with client.test_factory() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(approved=2))
            await s.commit()

    asyncio.run(unpublish())

    assert _ask(client, conversation, "这道题难在哪？", me).status_code in (403, 404)
    assert client.post(
        f"/assistant/tasks/{task_id}/conversations", headers=me
    ).status_code in (403, 404)
    assert gateway.requests == []


def test_with_no_credits_left_the_model_is_not_asked(client, gateway, monkeypatch):
    monkeypatch.setattr(settings, "personal_credits_monthly", 1.0)
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)
    # One question overdraws the month; the next is refused before the model.
    assert _ask(client, conversation, "从哪里入手？", me).status_code == 200
    asked = len(gateway.requests)

    r = _ask(client, conversation, "还有呢？", me)

    assert r.status_code == 429 and "额度已用完" in r.json()["message"]
    assert len(gateway.requests) == asked


def test_my_tasks_reports_only_what_the_asker_takes_part_in(client, gateway):
    me = _auth(client, "asker")
    mine = _task(client, name="我领的那道")
    _task(client, name="别人的那道")
    here = _task(client, name="正在看的这道")
    user_id, _, _ = _ledger_user(client, "asker")

    async def join() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            s.add(
                TaskMembership(
                    task_id=mine,
                    member_id=user_id,
                    approved=0,
                    is_team=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

    asyncio.run(join())
    conversation = _start(client, here, me)
    gateway.script = [("tool", "my_tasks", {}), ("text", "你领了一道题。")]

    assert _ask(client, conversation, "我领了哪些题？", me).status_code == 200

    tool_result = json.dumps(gateway.requests[-1]["messages"][-1], ensure_ascii=False)
    assert "我领的那道" in tool_result
    assert "别人的那道" not in tool_result


def _ledger_user(client, handle: str):
    async def read():
        async with client.test_factory() as s:
            user = (
                await s.execute(select(User).where(User.username == handle))
            ).scalar_one()
            return user.id, None, None

    return asyncio.run(read())
