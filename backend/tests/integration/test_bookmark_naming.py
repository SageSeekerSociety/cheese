"""A bookmark's name: the rule writes one at once, the model refines it.

The rules, stated before the code:

- the naming step writes the model's name over the rule's name (these tests
  drive `naming.refine` the way the route does after its response, not through
  the route itself — the scheduling in `PUT .../chapters/{id}` is unchanged);
- a message the rule cannot name (no words) is still asked about — the row
  shows a cut-off excerpt until the model answers.

The gateway is a MockTransport: minting and the completion both go through it,
so nothing here talks to LiteLLM.
"""

import json
import uuid

import httpx
import pytest

from app.core.config import settings
from app.domain.chapter import naming
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _setup(client):
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob"):
        join_project_team(client, p["id"], handle)
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "前端"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _say(client, tid, who, text):
    r = client.post(
        f"/topics/{tid}/messages",
        json={"request_id": str(uuid.uuid4()), "content": text},
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _mark(client, tid, block, who="alice"):
    r = client.put(f"/topics/{tid}/chapters/{block}", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text


def _title_of(client, tid, block):
    rows = client.get(
        f"/topics/{tid}/chapters", headers=session_auth_headers("bob")
    ).json()["data"]
    return next(row["title"] for row in rows if row["block"]["id"] == block)


@pytest.fixture
def gateway(monkeypatch):
    """A gateway that mints one key and answers with what the test queued."""
    seen: dict = {"asked": [], "answers": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-bookmark"})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            seen["asked"].append(body["messages"][-1]["content"])
            answer = seen["answers"].pop(0)
            return httpx.Response(
                200,
                headers={"x-litellm-response-cost": "0.0001"},
                json={
                    "choices": [
                        {"message": {"content": answer}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 8},
                },
            )
        return httpx.Response(404)

    # Handed to `refine` itself rather than stubbed into httpx: the call has to
    # carry this transport through to both the mint and the completion, and a
    # stub on the client would hide it if it stopped doing that.
    seen["transport"] = httpx.MockTransport(handler)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    return seen


def _refine(client, gateway, tid, message):
    """Run the background naming on the app's own loop, as the route does
    after its response. ``test_request_factory`` keeps the session on that
    loop (a plain ``asyncio.run`` would attach to a second one)."""
    return client.portal.call(
        lambda: naming.refine(
            uuid.UUID(tid),
            uuid.UUID(message),
            session_factory=client.test_request_factory,
            transport=gateway["transport"],
        )
    )


def test_the_model_replaces_the_rule_name(client, gateway):
    tid = _setup(client)
    message = _say(client, tid, "alice", "登录报错：token 刷新之后仍然 401")
    gateway["answers"].append("登录 401 排查")

    _mark(client, tid, message)
    assert _refine(client, gateway, tid, message) is True

    assert _title_of(client, tid, message) == "登录 401 排查"


def test_the_whole_message_is_what_the_model_reads(client, gateway):
    tid = _setup(client)
    message = _say(
        client,
        tid,
        "alice",
        "登录报错：token 刷新之后仍然 401\n复现步骤：退出后重进，第二次刷新就失效",
    )
    gateway["answers"].append("登录 401 排查")

    _mark(client, tid, message)
    assert _refine(client, gateway, tid, message) is True

    asked = gateway["asked"][0]
    assert "复现步骤" in asked


def test_a_message_with_no_words_is_still_asked_about(client, gateway):
    tid = _setup(client)
    # Only an attachment link: the rule has nothing to name it with, so the row
    # keeps NULL and the rail shows a cut-off excerpt until the model answers.
    message = _say(client, tid, "alice", "[截图](https://example.com/a.png)")
    gateway["answers"].append("附件截图")

    _mark(client, tid, message)
    assert _refine(client, gateway, tid, message) is True

    assert _title_of(client, tid, message) == "附件截图"


def test_the_naming_role_carries_the_bookmark_key():
    """Pointing `topic_naming_model` at another model has to move the key this
    naming runs on too; a key the role does not name keeps the old model."""
    from app.core import model_roles

    assert "bookmark-naming" in model_roles.ROLES["topic_naming_model"].keys


def test_the_naming_timeout_follows_the_setting(monkeypatch):
    """Read per call, like task naming: freezing it at import would make one
    side of the same budget ignore a changed setting."""
    monkeypatch.setattr(settings, "topic_naming_timeout_seconds", 3.0)
    assert naming._timeout() == 3.0


def test_the_production_path_names_without_a_transport(client, monkeypatch):
    """What the route actually runs: ``refine`` with no transport at all. The
    other tests hand it a mock transport, which would hide a broken default
    client, so this one stubs the client itself."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-bookmark-default"})
        if request.url.path == "/v1/chat/completions":
            asked.append("asked")
            return httpx.Response(
                200,
                headers={"x-litellm-response-cost": "0.0001"},
                json={
                    "choices": [
                        {
                            "message": {"content": "登录 401 排查"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 8},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")

    tid = _setup(client)
    message = _say(client, tid, "alice", "登录报错：token 刷新之后仍然 401")
    _mark(client, tid, message)

    claimed = client.portal.call(
        lambda: naming.refine(
            uuid.UUID(tid),
            uuid.UUID(message),
            session_factory=client.test_request_factory,
        )
    )

    assert claimed is True
    assert asked, "the model was never called"
    assert _title_of(client, tid, message) == "登录 401 排查"
