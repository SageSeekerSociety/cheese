"""The docs site's server side over the real HTTP stack, database and Valkey.

The docs live on a host of their own here (``docs_host``), and a reader signs
in to it through the platform: a grant from the platform, spent once on the
docs host for a cookie that only that host takes, which lasts no longer than
the platform sign-in it came from. dev/ is for platform admins: every check
re-asks, and losing admin closes the door. 问芝士 needs a signed-in
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
from app.domain.docs_site import access, assistant, retrieval, site
from app.domain.docs_site.limits import AskLimits
from app.domain.docs_site.models import DocsQuestion, ServiceCredential
from app.domain.feature_stats import pricing
from app.domain.team.models import Team
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.models import ComputeGrant, ResourceUsage
from tests.integration.conftest import (
    DOCS_HOST,
    PLATFORM_ORIGIN,
    docs_cookie,
    docs_sign_in,
    free_plan_credits,
    on_docs,
    sign_in,
)

ADMIN = "docs-admin"
INDEX = [
    {
        "title": "验收与采纳",
        "heading": "采纳交付",
        "url": "/accept#is-merge",
        "text": "确认改动符合要求后，在任务面板中点击「采纳」。"
        "采纳并合并成功后，改动进入项目主线。",
    },
    {
        "title": "团队",
        "heading": "邀请成员",
        "url": "/teams#invite-member",
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
        # The gateway's keys by alias; it takes each alias once.
        "keys": {},
        "completions": [],
        "index": 0,
        "pages": [],
        "tool_rounds": 0,
        "script": [],
        "answer": "文档里没有讲到。",
        "deltas": ("采纳就是合并，", "见 [验收与采纳](/accept#is-merge)。"),
        "usage": (300, 20),
        # Of the prompt tokens, how many the provider served from its cache.
        "cached": 0,
        # USD per input, output and cached input token for 问芝士's model.
        "rates": (1e-6, 2e-6, 1e-8),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sections.json"):
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
            body = json.loads(request.content)
            if body["key_alias"] in seen["keys"]:
                # As LiteLLM refuses a second key under an alias.
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": f"Key with alias '{body['key_alias']}' "
                            "already exists. Unique key aliases across all keys "
                            "are required."
                        }
                    },
                )
            seen["mints"] += 1
            seen["mint_body"] = body
            key = f"sk-docs-virtual-{seen['mints']}"
            seen["keys"][body["key_alias"]] = key
            return httpx.Response(200, json={"key": key})
        if path == "/key/delete":
            aliases = json.loads(request.content).get("key_aliases") or []
            gone = [a for a in aliases if seen["keys"].pop(a, None) is not None]
            if not gone:
                return httpx.Response(404, json={"error": "No keys found"})
            return httpx.Response(200, json={"deleted_keys": gone})
        if path.endswith(".md"):
            seen["pages"].append(path)
            return httpx.Response(
                200, text=PAGE_MD, headers={"content-type": "text/markdown"}
            )
        if path == "/v1/chat/completions":
            held = request.headers["authorization"].removeprefix("Bearer ")
            if held not in seen["keys"].values():
                return httpx.Response(401, json={"error": {"message": "bad key"}})
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
    # A fresh copy: the cached index belongs to whichever test read it first.
    monkeypatch.setattr(retrieval, "source", retrieval.IndexSource(site.index_url))
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
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
def asker(client, docs_host) -> dict[str, str]:
    """A person signed in to the docs, as the docs page's requests carry it."""
    return on_docs(docs_cookie(docs_sign_in(client, sign_in(client, "docs-asker"))))


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _rows(client, more_than: int = 0) -> list[DocsQuestion]:
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
        if len(rows) > more_than:
            return rows
        time.sleep(0.05)
    return []


# ---------- signing in to the docs ----------


def test_a_signed_in_person_gets_a_docs_cookie_for_the_docs_host_alone(
    client, docs_host
):
    r = docs_sign_in(client, sign_in(client, "docs-reader"), path="/quickstart#talk")
    assert r.status_code == 303
    assert r.headers["location"] == "/quickstart#talk"
    [cookie] = [
        c
        for c in r.headers.get_list("set-cookie")
        if c.startswith(f"{access.cookie_name()}=")
    ]
    # __Host-: Secure, Path=/ and no Domain, so the browser keeps it to this
    # one host and never sends it to the platform's.
    assert access.cookie_name().startswith("__Host-")
    assert "Domain" not in cookie and "Path=/" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert (
        client.get(
            "/docs/dev-access/check", headers=on_docs(docs_cookie(r))
        ).status_code
        == 403
    )


def test_the_docs_send_a_reader_to_the_platform_to_sign_in(client, docs_host):
    r = client.get(
        "/docs/signin",
        params={"path": "/quickstart#talk"},
        headers=on_docs(),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == (
        f"{PLATFORM_ORIGIN}/docs-signin?path=%2Fquickstart%23talk"
    )
    # Somewhere off the site is not a page to come back to.
    elsewhere = client.get(
        "/docs/signin",
        params={"path": "//evil.example/x"},
        headers=on_docs(),
        follow_redirects=False,
    )
    assert elsewhere.headers["location"] == f"{PLATFORM_ORIGIN}/docs-signin?path=%2F"


def test_the_platform_hands_out_grants_only_to_someone_signed_in(client, docs_host):
    assert client.post("/docs/grant").status_code == 401
    granted = client.post(
        "/docs/grant",
        headers={"Authorization": f"Bearer {sign_in(client, 'docs-g').token}"},
    )
    assert granted.status_code == 200
    assert granted.json()["data"]["url"] == f"{docs_host}/api/docs/session"
    assert granted.headers["cache-control"] == "no-store"


def _post_grant(
    client, grant: str, *, origin: str = PLATFORM_ORIGIN, host: str = DOCS_HOST
):
    return client.post(
        "/docs/session",
        content=f"grant={grant}&path=/",
        headers={
            "host": host,
            "origin": origin,
            "content-type": "application/x-www-form-urlencoded",
        },
        follow_redirects=False,
    )


def _grant(client, person) -> str:
    return client.post(
        "/docs/grant", headers={"Authorization": f"Bearer {person.token}"}
    ).json()["data"]["grant"]


def test_a_grant_is_spent_once(client, docs_host):
    grant = _grant(client, sign_in(client, "docs-once"))
    assert _post_grant(client, grant).status_code == 303
    assert _post_grant(client, grant).status_code == 401


def test_an_expired_grant_is_refused(client, docs_host, monkeypatch):
    person = sign_in(client, "docs-late")
    # Handed out longer ago than a grant lasts, as if the browser sat on it.
    earlier = time.time() - access.GRANT_TTL - 5
    with monkeypatch.context() as m:
        m.setattr(access.time, "time", lambda: earlier)
        grant = _grant(client, person)
    assert _post_grant(client, grant).status_code == 401


def test_a_grant_counts_only_from_the_platform_and_only_on_the_docs_host(
    client, docs_host
):
    person = sign_in(client, "docs-where")
    # Another okcheese.com host is same-site, so it is the Origin that tells.
    assert (
        _post_grant(
            client, _grant(client, person), origin=f"https://{DOCS_HOST}"
        ).status_code
        == 403
    )
    # Posted to the platform's own host, the cookie would land there.
    assert (
        _post_grant(client, _grant(client, person), host="example.test").status_code
        == 404
    )


def test_the_docs_cookie_is_not_taken_on_the_platform_host(client, docs_host, as_admin):
    cookie = docs_cookie(docs_sign_in(client, sign_in(client, ADMIN)))
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(cookie)).status_code == 204
    )
    assert (
        client.get(
            "/docs/dev-access/check", headers=on_docs(cookie, host="example.test")
        ).status_code
        == 401
    )


def test_signing_out_of_the_platform_signs_out_of_the_docs(client, docs_host, as_admin):
    person = sign_in(client, ADMIN)
    cookie = docs_cookie(docs_sign_in(client, person))
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(cookie)).status_code == 204
    )
    out = client.post(
        "/users/auth/logout",
        headers={
            "origin": PLATFORM_ORIGIN,
            "cookie": f"cheese_refresh={person.refresh}",
        },
    )
    assert out.status_code == 200, out.text
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(cookie)).status_code == 401
    )


def test_the_docs_sign_in_ends_when_its_time_is_up(client, docs_host, monkeypatch):
    monkeypatch.setattr(settings, "docs_session_seconds", 1)
    cookie = docs_cookie(docs_sign_in(client, sign_in(client, "docs-timed")))
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(cookie)).status_code == 403
    )
    time.sleep(2.1)
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(cookie)).status_code == 401
    )


# ---------- dev/: platform admins ----------


def test_dev_pages_are_for_admins_and_losing_admin_closes_the_door(
    client, docs_host, as_admin, monkeypatch
):
    assert client.get("/docs/dev-access/check", headers=on_docs()).status_code == 401
    stranger = docs_cookie(docs_sign_in(client, sign_in(client, "stranger")))
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(stranger)).status_code
        == 403
    )

    admin = docs_cookie(docs_sign_in(client, sign_in(client, ADMIN)))
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(admin)).status_code == 204
    )
    monkeypatch.setattr(settings, "platform_admin_handles", [])
    # Within the minute the list is cached, the door is still open ...
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(admin)).status_code == 204
    )
    access.admins.forget()  # ... and once it has run out, it is shut.
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(admin)).status_code == 403
    )


def test_a_forged_cookie_is_refused(client, docs_host, as_admin):
    forged = f"{access.cookie_name()}=forged.token.value"
    assert (
        client.get("/docs/dev-access/check", headers=on_docs(forged)).status_code == 401
    )


def test_the_docs_still_work_under_the_platform_with_no_docs_host(
    client, monkeypatch, as_admin
):
    """No docs host configured: the same sign-in, on the platform's own host."""
    monkeypatch.setattr(settings, "frontend_url", PLATFORM_ORIGIN)
    monkeypatch.setattr(settings, "docs_origin", "")
    r = docs_sign_in(client, sign_in(client, ADMIN), path="/dev/turn")
    assert r.status_code == 303 and r.headers["location"] == "/dev/turn"
    cookie = docs_cookie(r)
    assert (
        client.get(
            "/docs/dev-access/check", headers=on_docs(cookie, host="example.test")
        ).status_code
        == 204
    )


# ---------- 问芝士 ----------


def test_asking_needs_a_signed_in_person(client, docs_host, gateway):
    r = client.post("/docs/ask", json={"question": "怎么采纳"}, headers=on_docs())
    assert r.status_code == 401


def test_a_question_from_another_page_is_refused_even_with_the_cookie(
    client, asker, gateway
):
    # Any okcheese.com page is same-site with the docs and may send the cookie
    # along; only the docs' own pages may spend the reader's credits.
    elsewhere = {**asker, "origin": PLATFORM_ORIGIN}
    r = client.post("/docs/ask", json={"question": "怎么采纳"}, headers=elsewhere)
    assert r.status_code == 403
    assert gateway["completions"] == []


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
        and events[0][1]["sources"][0]["url"] == "/accept#is-merge"
    )
    assert (
        "".join(d["text"] for e, d in events if e == "delta")
        == "采纳就是合并，见 [验收与采纳](/accept#is-merge)。"
    )
    assert events[-1] == ("done", {})

    auth, sent = gateway["completions"][0]
    assert auth == "Bearer sk-docs-virtual-1"
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
    assert row.sources[0] == "/accept#is-merge"

    assert client.post("/docs/ask", json=body, headers=asker).status_code == 200
    assert gateway["mints"] == 1

    async def keys():
        async with client.test_factory() as s:
            return await s.scalar(select(func.count()).select_from(ServiceCredential))

    assert asyncio.run(keys()) == 1


def test_a_key_the_gateway_still_holds_without_a_stored_secret_is_replaced(
    client, asker, gateway, monkeypatch
):
    # The gateway keeps 问芝士's old key, but its secret is no longer stored here
    # (the row was dropped so the key would be re-minted without a budget), and
    # the gateway takes each alias only once.
    monkeypatch.setattr(settings, "docs_assistant_agentic", False)
    gateway["keys"]["docs-assistant"] = "sk-lost"
    body = {"question": "采纳和合并是一回事吗", "page": "accept"}

    r = client.post("/docs/ask", json=body, headers=asker)
    assert r.status_code == 200, r.text
    assert _events(r.text)[-1] == ("done", {})
    # The old key is gone and the new one carries today's limits.
    assert gateway["keys"] == {"docs-assistant": "sk-docs-virtual-1"}
    assert gateway["mint_body"]["rpm_limit"] == 120
    assert "max_budget" not in gateway["mint_body"]

    assert client.post("/docs/ask", json=body, headers=asker).status_code == 200
    assert gateway["mints"] == 1
    assert {auth for auth, _ in gateway["completions"]} == {"Bearer sk-docs-virtual-1"}


def test_the_model_searches_reads_and_answers_from_the_page_it_read(
    client, asker, gateway
):
    gateway["script"] = [
        ("search_docs", {"query": "采纳 合并"}),
        ("fetch_doc", {"url": "/accept#is-merge"}),
    ]
    gateway["answer"] = "采纳就是合并，见 [验收与采纳](/accept#is-merge)。"
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
        "url": "/accept",
    }
    # The pages it actually read: once before the answer is finished, again
    # under it, so a reader who joined late still gets the list.
    read = {"sources": [{"title": "验收与采纳", "heading": "", "url": "/accept"}]}
    assert events[2][1] == read and events[4][1] == read
    assert events[3][1] == {"text": gateway["answer"]}
    assert events[5] == ("done", {})

    # The tools went out with the round; the fetch went to the page's public
    # .md twin; the page came back to the model as data.
    assert gateway["pages"] == ["/accept.md"]
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
    assert row.sources == ["/accept"]


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
    before = len(_rows(client, more_than=-1))
    r = client.post(
        "/docs/ask",
        json={"question": "采纳和合并是一回事吗", "page": "accept"},
        headers=asker,
    )
    if r.status_code == 200:
        # Settled once THIS question's row is written, not an earlier one's.
        _rows(client, more_than=before)
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
    assert grant.credits_used == pytest.approx(cost / CREDIT_USD)
    assert grant.credits_total == 500  # Free's month
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
    assert grant.credits_used == pytest.approx(cost / CREDIT_USD)


def test_a_month_gives_one_grant_however_many_questions(client, asker, gateway):
    assert _ask(client, asker).status_code == 200
    assert _ask(client, asker).status_code == 200
    grants, spent = _ledger(client)
    assert len(grants) == 1 and len(spent) == 2


def test_with_no_credits_left_the_question_is_refused_until_the_month_turns(
    client, asker, gateway, monkeypatch
):
    # One question overdraws the month; the next is refused before the model.
    free_plan_credits(client, 0.01)
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


def test_a_question_asked_from_a_default_port_origin_still_counts(
    client, monkeypatch, gateway
):
    # Browsers leave :443 out of Origin; a DOCS_ORIGIN written with it must not
    # turn every question into a cross-site refusal.
    monkeypatch.setattr(settings, "frontend_url", PLATFORM_ORIGIN + ":443")
    monkeypatch.setattr(settings, "docs_origin", "https://docs.example.test:443")
    headers = on_docs(docs_cookie(docs_sign_in(client, sign_in(client, "docs-port"))))
    r = client.post(
        "/docs/ask", json={"question": "采纳和合并是一回事吗"}, headers=headers
    )
    assert r.status_code == 200, r.text
