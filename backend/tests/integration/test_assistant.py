"""A person's 芝士 on a task page, over the real HTTP stack, database and Valkey,
answered by the pinned pi on a session host.

The rules pinned here are the ones a person could state without reading the
code: 芝士 answers from the task it was asked about, as it writes; the answer is
kept for the asker and paid for from their personal credits, at what the
gateway spent; a new conversation starts clean, and one asked before 芝士 ran
on the session host goes on from what was said in it; nobody else can see, list
or continue someone's conversation; a person who cannot open a task cannot ask
about it; with no credits left the model is not asked; the "my tasks" tool
reports only what the asker takes part in; and nothing of a machine or a room
is touched to answer.

The session host is this machine (``tests/support/session_host.py``). The
gateway is a real local HTTP server answering the way LiteLLM does: the model
behind the platform's ``/llm/v1``, keys, prices and the spend log. pi reaches
the backend over HTTP through a small relay into the test client.
"""

import asyncio
import hashlib
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from redis.asyncio import from_url
from sqlalchemy import select, text, update

from app.api.deps import (
    consumptions_for,
    get_chat_service,
    get_consumptions,
    get_session_host,
)
from app.api.routes import assistant as route
from app.api.routes import llm_proxy
from app.core.config import settings
from app.domain.agent.session_host.host import SessionHost
from app.domain.assistant.models import AssistantMessage
from app.domain.feature_stats import pricing
from app.domain.task.models import Task, TaskMembership
from app.domain.team.models import Team
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.models import ComputeGrant, ResourceUsage
from app.domain.user.models import User
from app.main import app
from tests.conftest import seed_task_with_protocol, seed_user
from tests.integration.conftest import free_plan_credits
from tests.support.session_host import DEVICE, Host, install_pi, stop_all

USAGE = {
    "prompt_tokens": 300,
    "completion_tokens": 20,
    "total_tokens": 320,
    "prompt_tokens_details": {"cached_tokens": 200},
}
RATES = (1e-3, 2e-3, 1e-5)  # input, output, cached input — USD per token
#: What the gateway spends on one call of ``USAGE``, cache share at its rate.
CALL_USD = 100 * RATES[0] + 200 * RATES[2] + 20 * RATES[1]


def _chat():
    """The chat service the app is serving this test with."""
    return app.dependency_overrides.get(get_chat_service, get_chat_service)()


class Gateway:
    """The script: each model request takes the next step; a step is
    ``("tool", name, args)``, ``("text", answer)``, or ``("held", first,
    rest)``: ``first`` is streamed, then nothing until ``release`` is set."""

    def __init__(self) -> None:
        self.script: list[tuple] = []
        self.requests: list[dict] = []
        self.keys: list[str] = []
        self.spend: list[dict] = []
        #: Every key minted, with the models it may call; and keys revoked.
        self.minted: dict[str, list[str]] = {}
        self.revoked: list[str] = []
        #: Every model call fails, as a gateway that is down answers.
        self.failing = False
        #: A held step: set once its first part is out, and what lets it go on.
        self.holding = threading.Event()
        self.release = threading.Event()
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
                elif self.path.startswith("/user/daily/activity"):
                    # As LiteLLM keeps them: a day's totals per model, each
                    # split by key. Every call here was made today.
                    query = parse_qs(urlsplit(self.path).query)
                    key = query["api_key"][0]
                    models: dict[str, dict] = {}
                    for r in outer.spend:
                        if r["key"] != key:
                            continue
                        shares = models.setdefault(r["model"], {})
                        totals = shares.setdefault(
                            key,
                            {"prompt_tokens": 0, "completion_tokens": 0, "spend": 0.0},
                        )
                        for field in totals:
                            totals[field] += r[field]
                    breakdown = {
                        name: {
                            "api_key_breakdown": {
                                k: {"metrics": totals} for k, totals in shares.items()
                            }
                        }
                        for name, shares in models.items()
                    }
                    day = query["start_date"][0]
                    self._json(
                        {"results": [{"date": day, "breakdown": {"models": breakdown}}]}
                    )
                else:
                    self._json({}, 404)

            def do_POST(self) -> None:
                sent = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.path == "/key/generate":
                    key = f"sk-{sent['user_id']}-{len(outer.minted) + 1}"
                    outer.minted[key] = list(sent.get("models") or [])
                    self._json({"key": key})
                    return
                if self.path == "/key/delete":
                    outer.revoked += sent["keys"]
                    self._json({"deleted_keys": sent["keys"]})
                    return
                key = self.headers["authorization"].removeprefix("Bearer ")
                if key in outer.revoked or sent.get("model") not in outer.minted.get(
                    key, []
                ):
                    # As LiteLLM answers a key that is gone, or not for this model.
                    self._json({"error": {"message": "key not allowed"}}, 401)
                    return
                outer.requests.append(sent)
                outer.keys.append(key)
                outer.spend.append(
                    {
                        "key": hashlib.sha256(key.encode()).hexdigest(),
                        "model": settings.assistant_model,
                        "prompt_tokens": USAGE["prompt_tokens"],
                        "completion_tokens": USAGE["completion_tokens"],
                        "spend": CALL_USD,
                    }
                )
                if outer.failing:
                    self._json({"error": {"message": "upstream fell over"}}, 500)
                    return
                step = outer.script.pop(0) if outer.script else ("text", "好的。")
                if step[0] == "held":
                    self._held(step[1], step[2])
                    return
                self._stream(step)

            def _held(self, first: str, rest: str) -> None:
                # No length: the body ends when the connection does.
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                chunk = self._chunk
                self.wfile.write(chunk({"role": "assistant", "content": ""}))
                self.wfile.write(chunk({"content": first}))
                self.wfile.flush()
                outer.holding.set()
                outer.release.wait(30)
                try:
                    self.wfile.write(chunk({"content": rest}))
                    self.wfile.write(chunk({}, finish="stop"))
                    self.wfile.write(chunk(None, usage=USAGE) + b"data: [DONE]\n\n")
                except OSError:
                    pass  # the caller stopped listening

            @staticmethod
            def _chunk(delta: dict | None, finish=None, usage=None) -> bytes:
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
                return f"data: {json.dumps(c)}\n\n".encode()

            def _json(self, body, status: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(self, step: tuple) -> None:
                def chunk(delta: dict | None, finish=None, usage=None) -> bytes:
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
                    return f"data: {json.dumps(c)}\n\n".encode()

                if step[0] == "tool":
                    call = {
                        "index": 0,
                        "id": f"call_{len(outer.requests)}",
                        "type": "function",
                        "function": {
                            "name": step[1],
                            "arguments": json.dumps(step[2]),
                        },
                    }
                    parts = [
                        chunk({"role": "assistant", "tool_calls": [call]}),
                        chunk({}, finish="tool_calls"),
                    ]
                else:
                    parts = [
                        chunk({"role": "assistant", "content": ""}),
                        *(chunk({"content": p}) for p in (step[1][:4], step[1][4:])),
                        chunk({}, finish="stop"),
                    ]
                data = b"".join([*parts, chunk(None, usage=USAGE), b"data: [DONE]\n\n"])
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.release.set()
        self._httpd.shutdown()


class Relay:
    """The backend's address as the session host dials it: each request is
    handed to the test client and its answer handed back."""

    def __init__(self, client) -> None:
        class Handler(BaseHTTPRequestHandler):
            def _relay(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                answer = client.request(
                    self.command,
                    self.path,
                    content=self.rfile.read(length) if length else None,
                    headers={
                        k: v
                        for k, v in self.headers.items()
                        if k.lower() not in ("host", "content-length", "connection")
                    },
                )
                self.send_response(answer.status_code)
                for k, v in answer.headers.items():
                    if k.lower() not in ("content-length", "transfer-encoding"):
                        self.send_header(k, v)
                self.send_header("content-length", str(len(answer.content)))
                self.end_headers()
                self.wfile.write(answer.content)

            do_GET = do_POST = _relay

            def log_message(self, *args) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def gateway(client, monkeypatch: pytest.MonkeyPatch, tmp_path):
    gw = Gateway()
    monkeypatch.setattr(settings, "llm_gateway_admin_base", gw.url)
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    monkeypatch.setattr(settings, "anthropic_base_url", gw.url)
    pricing.forget()
    # Answers settle on sessions of their own, after the response.
    for module in (route, llm_proxy):
        monkeypatch.setattr(
            module, "async_session_factory", client.test_request_factory
        )
    # TestClient runs each request on its own loop; a cached client cannot
    # follow.
    monkeypatch.setattr(route, "get_redis_client", lambda: from_url(settings.redis_url))
    import redis

    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("assistant:busy:*"):
        r.delete(key)

    relay = Relay(client)
    monkeypatch.setattr(settings, "agent_session_api_base", relay.url)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    home = tmp_path / "host"
    install_pi(home)
    host = Host(home)
    gw.host = host  # type: ignore[attr-defined]
    gw.sessions = client.test_request_factory  # type: ignore[attr-defined]
    _people(gw)
    try:
        yield gw
    finally:
        app.dependency_overrides.pop(get_session_host, None)
        app.dependency_overrides.pop(get_consumptions, None)
        stop_all(home)
        relay.close()
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
    """What the answer said, in order; the question's own id (``answering``),
    which only a reader reconnecting needs, is left out."""
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        if lines["event"] != "answering":
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _ledger(client, handle: str):
    async def read():
        async with client.test_factory() as s:
            # One snapshot for the whole read. Under READ COMMITTED each SELECT
            # takes its own, so the single transaction that writes the usage row
            # and the grant deduction landing in between reads as a charged call
            # on a grant still untouched — and _charged returns on the usage
            # alone, before the deduction exists to it. Every poll opens its own
            # transaction here, so the next one sees the settled pair together.
            await s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            user = (
                await s.execute(select(User).where(User.username == handle))
            ).scalar_one()
            grants = list(
                (
                    await s.execute(
                        select(ComputeGrant)
                        .join(Team, Team.id == ComputeGrant.team_id)
                        .where(Team.personal_owner_user_id == user.id)
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


def _charged(client, handle: str, calls: int):
    """The asker's ledger once ``calls`` model calls have been charged."""
    deadline = time.monotonic() + 30
    while True:
        user_id, grants, usage = _ledger(client, handle)
        if sum(u.cost_usd for u in usage) >= calls * CALL_USD * 0.999:
            return user_id, grants, usage
        assert time.monotonic() < deadline, usage
        time.sleep(0.2)


def _ledger_user(client, handle: str):
    async def read():
        async with client.test_factory() as s:
            user = (
                await s.execute(select(User).where(User.username == handle))
            ).scalar_one()
            return user.id, None, None

    return asyncio.run(read())


def test_a_question_is_answered_from_the_task_kept_and_paid_for(client, gateway):
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)
    gateway.script = [("text", "要先会 PyTorch。")]

    r = _ask(client, conversation, "做这道题要先会什么？", me)

    assert r.status_code == 200, r.text
    events = _events(r.text)
    deltas = [d["text"] for e, d in events if e == "delta"]
    assert "".join(deltas) == "要先会 PyTorch。"
    assert events[-1] == ("done", {"stopped": False})
    # The model is asked without thinking, for a bounded answer.
    sent = gateway.requests[0]
    assert sent["thinking"] == {"type": "disabled"}
    assert "max_completion_tokens" not in sent and sent["max_tokens"] > 0
    # 芝士 was told the task it was asked about.
    told = json.dumps(sent, ensure_ascii=False)
    assert "图像分类基线复现" in told and "提交代码仓库链接" in told

    got = client.get(f"/assistant/conversations/{conversation}", headers=me).json()
    assert [(m["role"], m["text"]) for m in got["data"]["messages"]] == [
        ("user", "做这道题要先会什么？"),
        ("assistant", "要先会 PyTorch。"),
    ]
    assert got["data"]["title"] == "做这道题要先会什么？"

    # Paid for by the asker, at what the gateway spent on their own key.
    user_id, [grant], [spent] = _charged(client, "asker", 1)
    assert gateway.keys == [f"sk-user:{user_id}-1"]
    assert spent.project_id is None and spent.kind == "assistant"
    assert spent.cost_usd == pytest.approx(CALL_USD)
    assert grant.credits_used == pytest.approx(CALL_USD / CREDIT_USD)


def test_answering_touches_no_machine_and_no_room(client, gateway):
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)

    assert _ask(client, conversation, "从哪里入手？", me).status_code == 200

    ran = [argv for argv in gateway.host.execs if argv != ["cat", "/proc/meminfo"]]
    assert ran and all(argv == ["python3", "-"] for argv in ran)


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


def test_an_older_conversation_goes_on_from_what_was_said_in_it(client, gateway):
    """A conversation asked before 芝士 ran on the session host has no session
    yet; its first question there brings what was said along, once."""
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)

    async def said_before() -> None:
        async with client.test_factory() as s:
            for seq, (role, text) in enumerate(
                [("user", "学习率设多少？"), ("assistant", "从 0.1 起步。")], 1
            ):
                s.add(
                    AssistantMessage(
                        conversation_id=uuid.UUID(conversation),
                        seq=seq,
                        role=role,
                        text=text,
                    )
                )
            await s.commit()

    asyncio.run(said_before())

    assert _ask(client, conversation, "那批大小呢？", me).status_code == 200
    assert _ask(client, conversation, "还有呢？", me).status_code == 200

    def told(request) -> int:
        return json.dumps(request["messages"], ensure_ascii=False).count("从 0.1 起步")

    assert told(gateway.requests[0]) == 1
    assert told(gateway.requests[1]) == 1
    shown = client.get(f"/assistant/conversations/{conversation}", headers=me)
    assert [m["text"] for m in shown.json()["data"]["messages"]] == [
        "学习率设多少？",
        "从 0.1 起步。",
        "那批大小呢？",
        "好的。",
        "还有呢？",
        "好的。",
    ]


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


def test_with_no_credits_left_the_model_is_not_asked(client, gateway):
    free_plan_credits(client, 1.0)
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)
    # One question overdraws the month; the next is refused before the model.
    assert _ask(client, conversation, "从哪里入手？", me).status_code == 200
    _charged(client, "asker", 1)
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
    gateway.script = [("tool", "cheese_my_tasks", {}), ("text", "你领了一道题。")]

    r = _ask(client, conversation, "我领了哪些题？", me)

    assert r.status_code == 200
    assert ("tool", {"name": "cheese_my_tasks"}) in _events(r.text)
    tool_result = json.dumps(gateway.requests[-1]["messages"][-1], ensure_ascii=False)
    assert "我领的那道" in tool_result
    assert "别人的那道" not in tool_result


def test_the_retired_task_advice_is_gone(client, gateway):
    """启星研导 was replaced, not kept beside 芝士: its routes answer nothing."""
    me = _auth(client, "asker")
    task_id = _task(client)
    for path in (
        f"/tasks/{task_id}/ai-advice",
        f"/tasks/{task_id}/ai-advice/status",
        f"/tasks/{task_id}/ai-advice/conversations/grouped",
        "/ai/quota",
    ):
        assert client.get(path, headers=me).status_code == 404, path


def test_a_refused_question_leaves_nothing_in_the_list(client, gateway):
    free_plan_credits(client, 0.0)
    me = _auth(client, "asker")
    task_id = _task(client)
    conversation = _start(client, task_id, me)

    assert _ask(client, conversation, "从哪里入手？", me).status_code == 429

    listed = client.get(f"/assistant/tasks/{task_id}/conversations", headers=me)
    assert listed.json()["data"]["conversations"] == []


def test_an_answer_that_fails_says_so_with_its_sentence_key(
    client, gateway, monkeypatch
):
    """The panel says the failure in its reader's language, from the key the
    stream's ``error`` event carries beside the Chinese sentence."""
    from app.domain.agent.personal import session as personal

    # pi still retries the failing gateway three times, without the pauses.
    monkeypatch.setattr(personal, "RETRY_BASE_DELAY_MS", 0)
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    gateway.failing = True

    r = _ask(client, conversation, "从哪里入手？", me)

    assert r.status_code == 200, r.text
    assert (
        "error",
        {
            "message": "芝士暂时答不上来，稍后再试。",
            "i18n": {"key": "assistantFailed", "params": {}},
        },
    ) in _events(r.text)


def test_a_change_of_model_reissues_the_persons_key(client, gateway, monkeypatch):
    """A person's key may call one model. When the deployment answers with
    another, the next question still works: on a new key for the new model,
    with the old one revoked at the gateway and what it spent still charged."""
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    assert _ask(client, conversation, "从哪里入手？", me).status_code == 200
    old = gateway.keys[-1]

    monkeypatch.setattr(settings, "assistant_model", "deepseek-next")
    pricing.forget()
    r = _ask(client, conversation, "还有呢？", me)

    assert r.status_code == 200, r.text
    assert "error" not in [name for name, _ in _events(r.text)]
    new = gateway.keys[-1]
    assert new != old
    assert gateway.minted[new] == ["deepseek-next"]
    assert gateway.requests[-1]["model"] == "deepseek-next"
    assert old in gateway.revoked
    # Both questions are paid for: the old key's spend before it went.
    _charged(client, "asker", 2)


class FullHost:
    """The session host's memory, full for the first ``full`` looks."""

    def __init__(self, full: float) -> None:
        self.full = full

    async def can_start(self, mb: int) -> bool:
        self.full -= 1
        return self.full < 0


def _people(gateway, memory=None) -> None:
    """The session host the routes reach, and the questions read on it."""
    people = SessionHost(gateway.host, memory=memory)
    questions = consumptions_for(
        people,
        _chat(),
        redis=lambda: from_url(settings.redis_url),
        sessions=gateway.sessions,
    )
    app.dependency_overrides[get_session_host] = lambda: people
    app.dependency_overrides[get_consumptions] = lambda: questions


def test_a_question_waits_for_a_full_host_and_is_then_answered(client, gateway):
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    _people(gateway, FullHost(2))
    gateway.script = [("text", "要先会 PyTorch。")]

    events = _events(_ask(client, conversation, "做这道题要先会什么？", me).text)

    assert events[0] == ("queued", {})
    assert "".join(d["text"] for e, d in events if e == "delta") == "要先会 PyTorch。"
    assert events[-1] == ("done", {"stopped": False})


def test_a_host_full_for_too_long_says_so(client, gateway, monkeypatch):
    from app.domain.agent.personal import session as personal

    monkeypatch.setattr(personal, "HOST_WAIT_S", 1.5)
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    _people(gateway, FullHost(float("inf")))

    events = _events(_ask(client, conversation, "做这道题要先会什么？", me).text)

    assert events[0] == ("queued", {})
    [(_, refused)] = [(e, d) for e, d in events if e == "error"]
    assert refused["i18n"]["key"] == "assistantBusy"
    assert gateway.requests == []


def test_a_question_stopped_while_waiting_is_not_answered(client, gateway):
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    _people(gateway, FullHost(float("inf")))
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(_ask(client, conversation, "在吗？", me))
    )
    asking.start()
    deadline = time.monotonic() + 30
    while not _answering(conversation):
        assert time.monotonic() < deadline
        time.sleep(0.05)

    stopped = client.post(f"/assistant/conversations/{conversation}/stop", headers=me)

    assert stopped.status_code == 200, stopped.text
    asking.join(30)
    events = _events(answered[0].text)
    assert events[-1] == ("done", {"stopped": True})
    assert gateway.requests == []
    got = client.get(f"/assistant/conversations/{conversation}", headers=me).json()
    assert [(m["role"], m["text"], m["stopped"]) for m in got["data"]["messages"]] == [
        ("user", "在吗？", False),
        ("assistant", "", True),
    ]


def test_a_stopped_answer_keeps_what_was_written(client, gateway):
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)
    gateway.script = [("held", "先要会 PyTorch，", "然后再看论文。")]
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(_ask(client, conversation, "先会什么？", me))
    )
    asking.start()
    assert gateway.holding.wait(60)

    stopped = client.post(f"/assistant/conversations/{conversation}/stop", headers=me)

    assert stopped.status_code == 200, stopped.text
    asking.join(60)
    gateway.release.set()
    events = _events(answered[0].text)
    written = "".join(d["text"] for e, d in events if e == "delta")
    assert "然后再看论文" not in written
    assert events[-1] == ("done", {"stopped": True})
    got = client.get(f"/assistant/conversations/{conversation}", headers=me).json()
    [*_, answer] = got["data"]["messages"]
    assert (answer["role"], answer["text"], answer["stopped"]) == (
        "assistant",
        written.strip(),
        True,
    )


def _entries(text: str) -> list[tuple[str | None, str, dict]]:
    """Every event of a stream, with its place in it."""
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines.get("id"), lines["event"], json.loads(lines["data"])))
    return out


def test_an_answer_is_read_on_from_where_its_reader_left_it(client, gateway):
    """A reader that lost the stream — a closed tab, a backend restarted under
    it — finds the question still being answered when it opens the
    conversation, and reads the rest of it from where it was: only the rest,
    and only it."""
    me = _auth(client, "asker")
    other = _auth(client, "other")
    conversation = _start(client, _task(client), me)
    gateway.script = [("held", "先要会 PyTorch，", "然后再看论文。")]
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(_ask(client, conversation, "先会什么？", me))
    )
    asking.start()
    assert gateway.holding.wait(60)

    opened = client.get(f"/assistant/conversations/{conversation}", headers=me)
    work = opened.json()["data"]["answering"]
    assert work
    elsewhere = client.get(
        f"/assistant/conversations/{conversation}/answers/{work}", headers=other
    )
    assert elsewhere.status_code == 404

    gateway.release.set()
    asking.join(60)
    whole = _entries(answered[0].text)
    assert whole[0] == (None, "answering", {"id": work})
    first, *rest = whole[1:]
    again = client.get(
        f"/assistant/conversations/{conversation}/answers/{work}",
        params={"after": first[0]},
        headers=me,
    )

    assert again.status_code == 200, again.text
    assert _entries(again.text) == rest
    assert rest[-1][1:] == ("done", {"stopped": False})
    reopened = client.get(f"/assistant/conversations/{conversation}", headers=me)
    assert reopened.json()["data"]["answering"] is None


def test_someone_else_cannot_stop_my_question(client, gateway):
    me = _auth(client, "asker")
    conversation = _start(client, _task(client), me)

    r = client.post(
        f"/assistant/conversations/{conversation}/stop",
        headers=_auth(client, "stranger"),
    )

    assert r.status_code == 404


def _answering(conversation: str) -> bool:
    import redis

    return bool(
        redis.Redis.from_url(settings.redis_url).exists(
            f"assistant:busy:{conversation}"
        )
    )
