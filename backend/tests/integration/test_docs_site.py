"""The docs site's server side over the real HTTP stack, database and Valkey.

/docs/dev/ is for platform admins: the pass is issued only to them, every
check re-asks, and losing admin closes the door. 问芝士 needs a signed-in
person, never calls the model when the docs have nothing to say, mints its
gateway key once, records every question, and refuses past its limits. The
gateway and the frontend's index are stubbed with one MockTransport.
"""

import asyncio
import json
import time

import httpx
import pytest
from redis.asyncio import from_url
from sqlalchemy import func, select

from app.api.routes import docs_site as route
from app.core.config import settings
from app.domain.docs_site import access, assistant, retrieval
from app.domain.docs_site.limits import AskLimits
from app.domain.docs_site.models import DocsQuestion, ServiceCredential
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers

ADMIN = "docs-admin"
INDEX = [
    {
        "title": "验收与采纳",
        "heading": "采纳交付",
        "url": "/docs/accept#is-merge",
        "text": "确认改动符合要求后，在任务面板中点击「采纳」。"
        "采纳并合并成功后，改动进入项目主线。",
    },
    {
        "title": "团队",
        "heading": "邀请成员",
        "url": "/docs/teams#invite-member",
        "text": "队长和管理员可以通过 UID 邀请成员。",
    },
]


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    access.admins.forget()
    return ADMIN


@pytest.fixture
def gateway(client, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The frontend's index and the LiteLLM gateway, stubbed; returns what they saw."""
    seen: dict = {"mints": 0, "completions": [], "index": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/ask-index.json":
            seen["index"] += 1
            return httpx.Response(200, json=INDEX)
        if request.url.path == "/key/generate":
            seen["mints"] += 1
            seen["mint_body"] = json.loads(request.content)
            return httpx.Response(200, json={"key": "sk-docs-virtual"})
        if request.url.path == "/v1/chat/completions":
            seen["completions"].append(
                (request.headers["authorization"], json.loads(request.content))
            )
            deltas = ("采纳就是合并，", "见 [验收与采纳](/docs/accept#is-merge)。")
            chunks = [{"choices": [{"delta": {"content": d}}]} for d in deltas]
            usage = {"prompt_tokens": 300, "completion_tokens": 20}
            chunks.append({"choices": [], "usage": usage})
            body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
            body += "data: [DONE]\n\n"
            return httpx.Response(
                200,
                content=body.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(
        retrieval,
        "source",
        retrieval.IndexSource("http://frontend/docs/ask-index.json"),
    )
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    # Questions are recorded after the response, on a session of their own.
    monkeypatch.setattr(route, "async_session_factory", client.test_request_factory)
    # TestClient without a ``with`` block runs each request on its own loop, so
    # the process-wide cached client cannot follow it; production has one loop.
    monkeypatch.setattr(
        route, "_limits", AskLimits(lambda: from_url(settings.redis_url))
    )
    # Counters and locks are per user id, and ids restart with every test's database.
    import redis

    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("docs-ask:*"):
        r.delete(key)
    return seen


@pytest.fixture
def asker(client) -> dict[str, str]:
    """A signed-in person who exists in the database the questions are recorded in."""
    return {"Authorization": f"Bearer {seed_user(client, 'docs-asker')}"}


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _rows(client) -> list[DocsQuestion]:
    async def read():
        async with client.test_factory() as s:
            return list(
                (
                    await s.execute(
                        select(DocsQuestion).order_by(DocsQuestion.created_at)
                    )
                ).scalars()
            )

    # The row is written after the stream ends, off the request; give it a moment.
    for _ in range(50):
        rows = asyncio.run(read())
        if rows:
            return rows
        time.sleep(0.05)
    return []


# ---------- /docs/dev/ ----------


def test_the_pass_is_for_platform_admins_only(client, as_admin):
    assert (
        client.post(
            "/docs/dev-access", headers=session_auth_headers("stranger")
        ).status_code
        == 403
    )
    r = client.post("/docs/dev-access", headers=session_auth_headers(ADMIN))
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{access.COOKIE}=")
    assert (
        "Path=/docs/dev" in cookie
        and "HttpOnly" in cookie
        and "SameSite=strict" in cookie
    )


def test_every_check_reasks_and_losing_admin_closes_the_door(
    client, as_admin, monkeypatch
):
    assert client.get("/docs/dev-access/check").status_code == 401
    token, _ = access.issue(ADMIN)
    client.cookies.set(access.COOKIE, token)
    assert client.get("/docs/dev-access/check").status_code == 204

    monkeypatch.setattr(settings, "platform_admin_handles", [])
    access.admins.forget()  # the one-minute cache, run out
    assert client.get("/docs/dev-access/check").status_code == 403
    client.cookies.clear()


def test_a_pass_for_someone_else_or_a_forged_one_is_refused(client, as_admin):
    client.cookies.set(access.COOKIE, "forged.token.value")
    assert client.get("/docs/dev-access/check").status_code == 401
    client.cookies.clear()


# ---------- 问芝士 ----------


def test_asking_needs_a_signed_in_person(client, gateway):
    assert client.post("/docs/ask", json={"question": "怎么采纳"}).status_code == 401


def test_a_question_the_docs_do_not_cover_never_reaches_the_model(
    client, asker, gateway
):
    r = client.post("/docs/ask", json={"question": "今天天气怎么样"}, headers=asker)
    assert r.status_code == 200, r.text
    assert _events(r.text) == [
        ("sources", {"sources": []}),
        ("delta", {"text": assistant.NO_MATCH}),
        ("done", {}),
    ]
    assert gateway["completions"] == [] and gateway["mints"] == 0
    [row] = _rows(client)
    assert row.outcome == "no_match" and row.model is None


def test_an_answer_is_grounded_streamed_and_recorded(client, asker, gateway):
    body = {
        "question": "采纳和合并是一回事吗",
        "page": "accept",
        "history": [{"role": "user", "content": "你好"}],
    }
    first = client.post("/docs/ask", json=body, headers=asker)
    assert first.status_code == 200
    events = _events(first.text)
    assert (
        events[0][0] == "sources"
        and events[0][1]["sources"][0]["url"] == "/docs/accept#is-merge"
    )
    assert (
        "".join(d["text"] for e, d in events if e == "delta")
        == "采纳就是合并，见 [验收与采纳](/docs/accept#is-merge)。"
    )
    assert events[-1] == ("done", {})

    auth, sent = gateway["completions"][0]
    assert auth == "Bearer sk-docs-virtual"
    assert (
        sent["model"] == settings.docs_assistant_model
        and sent["max_tokens"] == assistant.MAX_ANSWER_TOKENS
    )
    assert "<docs>" in sent["messages"][-1]["content"]
    # The key is minted with a budget, once.
    assert gateway["mint_body"]["max_budget"] == settings.docs_assistant_budget_usd
    [row] = _rows(client)
    assert (row.outcome, row.page, row.prompt_tokens, row.completion_tokens) == (
        "answered",
        "accept",
        300,
        20,
    )
    assert row.sources[0] == "/docs/accept#is-merge"

    assert client.post("/docs/ask", json=body, headers=asker).status_code == 200
    assert gateway["mints"] == 1

    async def keys():
        async with client.test_factory() as s:
            return await s.scalar(select(func.count()).select_from(ServiceCredential))

    assert asyncio.run(keys()) == 1


def test_past_the_hourly_limit_the_answer_is_429(client, asker, gateway, monkeypatch):
    monkeypatch.setattr(settings, "docs_assistant_hourly_limit", 1)
    assert (
        client.post(
            "/docs/ask", json={"question": "今天天气"}, headers=asker
        ).status_code
        == 200
    )
    _rows(client)  # the first question's lock is released once its row is written
    r = client.post("/docs/ask", json={"question": "今天天气"}, headers=asker)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 60


def test_without_a_gateway_the_assistant_says_it_is_not_open(
    client, asker, gateway, monkeypatch
):
    monkeypatch.setattr(settings, "llm_gateway_admin_base", None)
    # A question the docs do cover: one they don't never needs the gateway.
    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    assert r.status_code == 503
    assert "暂未开放" in r.json()["message"]


def test_one_question_at_a_time_per_person(client, gateway):
    limits = route.ask_limits()

    async def twice():
        first = await limits.admit(424242)
        second = await limits.admit(424242)
        await limits.release(424242)
        third = await limits.admit(424242)
        await limits.release(424242)
        return first, second, third

    first, second, third = client.portal.call(twice)
    assert first.allowed and not second.allowed and third.allowed
    assert "还在回答" in second.message


@pytest.mark.parametrize(
    "bad",
    [
        {"question": ""},
        {"question": "x" * 501},
        {"question": "q", "history": [{"role": "system", "content": "x"}]},
    ],
)
def test_malformed_questions_are_rejected(client, asker, gateway, bad):
    assert client.post("/docs/ask", json=bad, headers=asker).status_code == 400
