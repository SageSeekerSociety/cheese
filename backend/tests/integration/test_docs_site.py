"""The docs site's server side over the real HTTP stack, database and Valkey.

/docs/dev/ is for platform admins: the pass is issued only to them, every
check re-asks, and losing admin closes the door. 问芝士 needs a signed-in
person, mints its gateway key once, records every question, and charges it to
the asker's personal credits at the model's price. Over the model's own
searches it reads the public pages and answers from them
(``docs_assistant_agentic``); with the switch off it answers from one round of
retrieval, and never calls the model when the docs have nothing to say. The
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
from app.domain.feature_stats import pricing
from app.domain.team.models import Team
from app.domain.usage.models import ComputeGrant, ResourceUsage
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
# The page's public ``.md`` twin, as the reader (and the agent's fetch_doc) gets it.
PAGE_MD = (
    "## 采纳交付 {#is-merge}\n\n"
    "确认改动符合要求后，在任务面板中点击「采纳」。采纳并合并成功后，改动进入项目主线。\n"
)


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    access.admins.forget()
    return ADMIN


@pytest.fixture
def gateway(client, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The frontend's index and the LiteLLM gateway, stubbed; returns what they saw.

    A test that wants the model to search before it answers sets ``script`` to
    the tool calls it should make, one per round; then ``answer`` is what it
    says once the script runs out. Without a script the first round already
    answers, which is the shape a gateway that does not want tools replies in."""
    seen: dict = {
        "mints": 0,
        "completions": [],
        "index": 0,
        "pages": [],
        "tool_rounds": 0,
        "script": [],
        "answer": "文档里没有讲到。",
        "deltas": ("采纳就是合并，", "见 [验收与采纳](/docs/accept#is-merge)。"),
        "usage": (300, 20),
        # Of the prompt tokens, how many the provider served from its cache.
        "cached": 0,
        # USD per input, output and cached input token for 问芝士's model.
        "rates": (1e-6, 2e-6, 1e-8),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/docs/ask-index.json":
            seen["index"] += 1
            return httpx.Response(200, json=INDEX)
        if path == "/model/info":
            rows = []
            if seen["rates"] is not None:
                inp, out, cache_read = seen["rates"]
                rows.append(
                    {
                        "model_name": settings.docs_assistant_model,
                        "litellm_params": {
                            "input_cost_per_token": inp,
                            "output_cost_per_token": out,
                            "cache_read_input_token_cost": cache_read,
                        },
                    }
                )
            return httpx.Response(200, json={"data": rows})
        if path == "/key/generate":
            seen["mints"] += 1
            seen["mint_body"] = json.loads(request.content)
            return httpx.Response(200, json={"key": "sk-docs-virtual"})
        if path.endswith(".md"):
            seen["pages"].append(path)
            return httpx.Response(
                200, text=PAGE_MD, headers={"content-type": "text/markdown"}
            )
        if path == "/v1/chat/completions":
            sent = json.loads(request.content)
            seen["completions"].append((request.headers["authorization"], sent))
            prompt, completion = seen["usage"]
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "prompt_tokens_details": {"cached_tokens": seen["cached"]},
            }
            if sent.get("tools"):
                at = seen["tool_rounds"]
                seen["tool_rounds"] += 1
                if at < len(seen["script"]):
                    name, args = seen["script"][at]
                    calls = [
                        {
                            "id": f"call_{at}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args, ensure_ascii=False),
                            },
                        }
                    ]
                    return httpx.Response(
                        200,
                        json={
                            "choices": [
                                {"message": {"content": None, "tool_calls": calls}}
                            ],
                            "usage": usage,
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": seen["answer"]}}],
                        "usage": usage,
                    },
                )
            chunks = [{"choices": [{"delta": {"content": d}}]} for d in seen["deltas"]]
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
    monkeypatch.setattr(settings, "llm_gateway_credit_usd", 1e-5)
    pricing.forget()
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
    client, asker, gateway, monkeypatch
):
    # The one-shot path's guarantee, kept for a deployment that turns the switch
    # off: the retrieval that would feed the model comes back empty, so there is
    # nothing to feed it and no key is minted.
    monkeypatch.setattr(settings, "docs_assistant_agentic", False)
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


def test_an_answer_is_grounded_streamed_and_recorded(
    client, asker, gateway, monkeypatch
):
    # The original path, end to end: one round of retrieval, no tools.
    monkeypatch.setattr(settings, "docs_assistant_agentic", False)
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
    # No tools on the old path: the sections were chosen here, not by the model.
    assert "tools" not in sent and "thinking" not in sent
    # The asker pays for the question, so the key carries no budget of its own.
    assert "max_budget" not in gateway["mint_body"]
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


def test_the_model_searches_reads_and_answers_from_the_page_it_read(
    client, asker, gateway
):
    gateway["script"] = [
        ("search_docs", {"query": "采纳 合并"}),
        ("fetch_doc", {"url": "/docs/accept#is-merge"}),
    ]
    gateway["answer"] = "采纳就是合并，见 [验收与采纳](/docs/accept#is-merge)。"
    gateway["usage"] = (100, 10)

    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    assert r.status_code == 200, r.text
    events = _events(r.text)
    assert [e for e, _ in events] == [
        "tool",
        "tool",
        "sources",
        "delta",
        "sources",
        "done",
    ]
    assert events[0][1] == {"kind": "search", "query": "采纳 合并"}
    assert events[1][1] == {
        "kind": "fetch",
        "title": "验收与采纳",
        "url": "/docs/accept",
    }
    # The pages it actually read: once before the answer is finished, again
    # under it, so a reader who joined late still gets the list.
    read = {"sources": [{"title": "验收与采纳", "heading": "", "url": "/docs/accept"}]}
    assert events[2][1] == read and events[4][1] == read
    assert events[3][1] == {"text": gateway["answer"]}
    assert events[5] == ("done", {})

    # The tools went out with the round; the fetch went to the page's public
    # .md twin; the page came back to the model as data.
    assert gateway["pages"] == ["/docs/accept.md"]
    first, second, third = (sent for _, sent in gateway["completions"])
    assert [t["function"]["name"] for t in first["tools"]] == [
        "search_docs",
        "fetch_doc",
        "list_docs",
    ]
    assert first["thinking"] == {"type": "disabled"} and "stream" not in first
    assert second["messages"][-1]["role"] == "tool"
    assert "在任务面板中点击「采纳」" in second["messages"][-1]["content"]
    assert third["messages"][-1]["role"] == "tool"

    # Every round was billed to the question, not just the one that answered.
    [row] = _rows(client)
    assert (row.outcome, row.page) == ("answered", "accept")
    assert (row.prompt_tokens, row.completion_tokens) == (300, 30)
    assert row.sources == ["/docs/accept"]


def test_a_question_the_docs_do_not_cover_is_refused_without_reading_anything(
    client, asker, gateway
):
    gateway["answer"] = "文档里没有讲到。"
    r = client.post("/docs/ask", json={"question": "今天天气怎么样"}, headers=asker)
    assert r.status_code == 200, r.text
    events = _events(r.text)
    assert [e for e, _ in events] == ["delta", "sources", "done"]
    assert events[0][1] == {"text": "文档里没有讲到。"}
    assert events[1][1] == {"sources": []}
    # It answered from what it knows: no page was read, and nothing is offered
    # to the reader to click.
    assert gateway["pages"] == []
    [row] = _rows(client)
    assert row.outcome == "answered" and row.sources == []


def _ledger(client) -> tuple[list[ComputeGrant], list[ResourceUsage]]:
    async def read():
        async with client.test_factory() as s:
            # A person's credits are packs on their personal team.
            grants = (
                await s.execute(
                    select(ComputeGrant)
                    .join(Team, Team.id == ComputeGrant.team_id)
                    .where(Team.personal_owner_user_id.is_not(None))
                )
            ).scalars()
            usage = (await s.execute(select(ResourceUsage))).scalars()
            return list(grants), list(usage)

    return asyncio.run(read())


def _personal_team_owner(client, team_id: int) -> int | None:
    async def read():
        async with client.test_factory() as s:
            return (await s.get(Team, team_id)).personal_owner_user_id

    return asyncio.run(read())


def _ask(client, asker) -> httpx.Response:
    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    _rows(client)  # settled once the question's row is written
    return r


@pytest.mark.parametrize("rates", [(1e-6, 2e-6, 1e-8), (4e-6, 8e-6, 4e-8)])
def test_a_question_is_paid_for_by_the_asker_at_the_models_price(
    client, asker, gateway, rates
):
    gateway["rates"] = rates
    gateway["usage"] = (300, 20)
    assert _ask(client, asker).status_code == 200

    [grant], [spent] = _ledger(client)
    # Priced at what the model charges, so a dearer model costs more credits
    # for the same tokens.
    cost = 300 * rates[0] + 20 * rates[1]
    assert grant.credits_used == pytest.approx(cost / settings.llm_gateway_credit_usd)
    assert grant.credits_total == settings.personal_credits_monthly
    # Outside any project, against the person who asked.
    assert spent.project_id is None
    assert _personal_team_owner(client, grant.team_id) == spent.user_id
    assert (spent.input_tokens, spent.output_tokens) == (300, 20)
    assert spent.cost_usd == pytest.approx(cost)


def test_prompt_tokens_served_from_the_cache_cost_the_cache_price(
    client, asker, gateway
):
    gateway["rates"] = (1e-6, 2e-6, 1e-8)
    gateway["usage"] = (300, 20)
    gateway["cached"] = 200
    assert _ask(client, asker).status_code == 200

    [grant], [spent] = _ledger(client)
    cost = 100 * 1e-6 + 200 * 1e-8 + 20 * 2e-6
    assert spent.cost_usd == pytest.approx(cost)
    assert grant.credits_used == pytest.approx(cost / settings.llm_gateway_credit_usd)


def test_a_month_gives_one_grant_however_many_questions(client, asker, gateway):
    assert _ask(client, asker).status_code == 200
    assert _ask(client, asker).status_code == 200
    grants, spent = _ledger(client)
    assert len(grants) == 1 and len(spent) == 2


def test_with_no_credits_left_the_question_is_refused_until_the_month_turns(
    client, asker, gateway, monkeypatch
):
    # One question overdraws the month; the next is refused before the model.
    monkeypatch.setattr(settings, "personal_credits_monthly", 1.0)
    assert _ask(client, asker).status_code == 200
    calls = len(gateway["completions"])

    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    assert r.status_code == 429
    assert "额度已用完" in r.json()["message"]
    assert int(r.headers["retry-after"]) >= 60
    assert len(gateway["completions"]) == calls
    # Refusing released the person's slot: the answer is the same, not "busy".
    again = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    assert again.status_code == 429 and "额度已用完" in again.json()["message"]


def test_a_model_with_no_price_cannot_be_charged_so_it_is_not_asked(
    client, asker, gateway
):
    gateway["rates"] = None
    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    assert r.status_code == 503
    assert gateway["completions"] == []
    _, spent = _ledger(client)
    assert spent == []


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
