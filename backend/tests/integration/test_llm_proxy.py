"""The model route remote machines use (/api/llm).

A MicroCloud machine gets its own scoped cheese token, never a provider key —
so this route must authenticate that token, swap in the project's virtual
gateway key, and pass the upstream answer through untouched.
"""

import json
import uuid

import httpx
import pytest

from app.api.routes import llm_proxy
from app.core.db import pool_status
from app.core.sandbox_auth import SANDBOX_TOKEN, mint_scoped_token
from tests.integration.conftest import post_project, set_free_plan_credits


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_admission_releases_its_database_connection_before_gateway_key(
    client, monkeypatch
):
    from app.domain.agent.chat import ChatService

    project_id = _make_project(client)
    checked_out = []

    async def gateway_key(self, project_id):
        status = pool_status(client.test_app_engine)
        assert status is not None
        checked_out.append(status["checked_out"])
        return "project-key"

    monkeypatch.setattr(ChatService, "project_gateway_key", gateway_key)
    token = mint_scoped_token(project_id=project_id)
    response = client.post(
        "/llm/admission",
        headers={"Authorization": f"Bearer {token}", "X-Cheese-Token": SANDBOX_TOKEN},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["supply"]["key"] == "project-key"
    assert checked_out == [0]


def test_only_the_metering_proxy_is_handed_the_gateway_key(client, monkeypatch):
    """A session holds the same scoped token the proxy admits it with, so that
    token alone gets the decision and never the project's gateway key. The key
    goes to a caller that also presents the proxy's own credential."""
    from app.domain.agent.chat import ChatService

    async def gateway_key(self, project_id):
        return "project-key"

    monkeypatch.setattr(ChatService, "project_gateway_key", gateway_key)
    project_id = _make_project(client)
    token = mint_scoped_token(project_id=project_id)
    bearer = {"Authorization": f"Bearer {token}"}
    # The test client sends the platform's secret on every request; a session
    # has no such thing.
    monkeypatch.delitem(client.headers, "X-Cheese-Token")

    for headers in (
        bearer,
        # What a session could add on its own: its scoped token again, or a guess.
        {**bearer, "X-Cheese-Token": token},
        {**bearer, "X-Cheese-Token": "not-the-secret"},
    ):
        body = client.post("/llm/admission", headers=headers).json()["data"]
        assert body["allow"] is True
        assert body["supply"]["pool"] == "gateway"
        assert body["supply"]["model"]
        assert "key" not in body["supply"]
        assert "project-key" not in json.dumps(body)

    proxy = client.post(
        "/llm/admission", headers={**bearer, "X-Cheese-Token": SANDBOX_TOKEN}
    ).json()["data"]
    assert proxy["supply"]["key"] == "project-key"


class _FakeResponse:
    def __init__(self, status_code: int, chunks: list[bytes], headers: dict):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self) -> None:
        return None


class _FakeClient:
    """Captures the outbound request instead of talking to a real gateway."""

    seen: dict = {}

    def __init__(self, *a, **kw):
        pass

    def build_request(self, method, url, *, headers, content, params):
        _FakeClient.seen = {
            "method": method,
            "url": url,
            "headers": headers,
            "content": content,
            "params": params,
        }
        return _FakeClient.seen

    async def send(self, request, stream: bool = False):
        return _FakeResponse(
            200,
            [b'{"ok":', b"true}"],
            {"content-type": "application/json", "content-length": "999"},
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def _pool(monkeypatch):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _FakeClient)


@pytest.fixture
def _project_key(monkeypatch):
    async def fake_key(self, project_id):  # noqa: ANN001
        return "sk-virtual-for-" + str(project_id)[:8]

    monkeypatch.setattr(
        "app.domain.agent.chat.ChatService.project_gateway_key", fake_key
    )


def test_scoped_token_is_swapped_for_the_project_key(client, _pool, _project_key):
    project = post_project(client, json={"name": "P"}).json()["data"]
    pid = project["id"]
    token = mint_scoped_token(project_id=pid, topic_id=project["root_topic_id"])

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"},
        content=b'{"model":"m","messages":[]}',
    )

    assert r.status_code == 200
    assert r.content == b'{"ok":true}'
    sent = _FakeClient.seen
    assert sent["url"] == "http://pool:4000/v1/messages"
    assert sent["headers"]["authorization"] == f"Bearer sk-virtual-for-{pid[:8]}"
    assert sent["headers"]["x-api-key"] == f"sk-virtual-for-{pid[:8]}"
    # The machine's own token must not ride along past the swap.
    assert token not in str(sent["headers"])
    # Protocol headers the client set are preserved.
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["content"] == b'{"model":"m","messages":[]}'


def test_x_api_key_presentation_also_works(client, _pool, _project_key):
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post("/llm/v1/messages", headers={"x-api-key": token}, content=b"{}")

    assert r.status_code == 200


def test_missing_or_bad_token_is_rejected(client, _pool, _project_key):
    assert client.post("/llm/v1/messages", content=b"{}").status_code == 401
    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": "Bearer not-a-real-token"},
        content=b"{}",
    )
    assert r.status_code == 401


def test_no_pool_configured_is_refused(client, monkeypatch, _project_key):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", None)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


def test_no_project_key_refuses_rather_than_using_pool_credentials(
    client, _pool, monkeypatch
):
    async def no_key(self, project_id):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.domain.agent.chat.ChatService.project_gateway_key", no_key)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


def test_upstream_failure_surfaces_as_gateway_error(client, monkeypatch, _project_key):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")

    class _Boom(_FakeClient):
        async def send(self, request, stream: bool = False):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _Boom)
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    r = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    assert r.status_code >= 400


@pytest.mark.anyio
async def test_admission_requires_a_scoped_token(client):
    r = client.post("/llm/admission")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_admission_answers_from_the_grant_balance(client):
    """One budget, two enforcement points: the same credits the gateway prices
    into max_budget answer the subscription proxy's yes/no here."""
    from app.domain.usage.ledger import Ledger, payer_for_project

    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)
    async with client.test_factory() as session:
        await set_free_plan_credits(session, 0)

    # A plan that issues nothing, and no other credits: refused.
    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        await Ledger(session).grant_earmark(
            project_id=uuid.UUID(pid), source_task_id=None, credits_total=5.0
        )
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["data"]["allow"] is True

    async with client.test_factory() as session:
        payer = await payer_for_project(session, uuid.UUID(pid))
        await Ledger(session).charge(payer, 5.0)
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    body = r.json()["data"]
    assert body["allow"] is False
    assert "额度已用完" in body["reason"]


async def _exhaust(client, pid: str) -> None:
    from app.domain.usage.ledger import Ledger, payer_for_project

    async with client.test_factory() as session:
        await set_free_plan_credits(session, 0)
        ledger = Ledger(session)
        await ledger.grant_earmark(
            project_id=uuid.UUID(pid), source_task_id=None, credits_total=1.0
        )
        await ledger.charge(await payer_for_project(session, uuid.UUID(pid)), 1.0)
        await session.commit()


@pytest.mark.anyio
async def test_admission_tells_the_room_once_for_a_refused_turn_in_flight(client):
    """The metering proxy caches a verdict for 30s and Claude Code retries ten
    times, so admission gets asked again and again for the SAME refusal
    (#715) — asking five times must still post the room's exhaustion notice
    exactly once, on the turn admission actually refused."""
    from app.domain.block.repositories import BlockRepository
    from tests.turn_log import open_turn

    pid = _make_project(client)
    topic_id = client.post("/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    turn_id = client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(topic_id))
    )
    await _exhaust(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(5):
        r = client.post("/llm/admission", headers=headers)
        assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
    notices = [b for b in blocks if "额度已用完" in b.content]
    assert len(notices) == 1
    assert notices[0].turn_id == turn_id


@pytest.mark.anyio
async def test_a_refused_subagent_tells_the_room_when_the_credits_come_back(client):
    """A 分身 refused mid-turn gets the same sentence a refused turn does: what
    ran out and the day it resets, not a bare "spent"."""
    from datetime import UTC, datetime

    from app.domain.block.repositories import BlockRepository
    from app.domain.usage.ledger import Ledger, month_end, month_of, payer_for_project
    from tests.turn_log import open_turn

    pid = _make_project(client)
    topic_id = client.post("/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(topic_id))
    )
    async with client.test_factory() as session:
        await set_free_plan_credits(session, 1)
        payer = await payer_for_project(session, uuid.UUID(pid))
        await Ledger(session).record(
            payer,
            credits=1,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            route="gateway",
        )
        await session.commit()
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    r = client.post(
        "/llm/admission",
        headers={"Authorization": f"Bearer {token}", "X-Cheese-Subagent": "1"},
    )
    assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
    resets = month_end(month_of(datetime.now(UTC)))
    notices = [b.content for b in blocks if "额度已用完" in b.content]
    assert len(notices) == 1
    assert f"{resets.month}月{resets.day}日重置" in notices[0]


@pytest.mark.anyio
async def test_a_monthly_refusal_says_when_it_reopens(client):
    """The proxy turns this into the reset headers Claude Code reads, so the
    refused turn waits for the allowance to come back rather than retrying."""
    from datetime import UTC, datetime

    from app.domain.usage.ledger import Ledger, month_end, month_of, payer_for_project

    pid = _make_project(client)
    async with client.test_factory() as session:
        await set_free_plan_credits(session, 1)
        payer = await payer_for_project(session, uuid.UUID(pid))
        await Ledger(session).record(
            payer,
            credits=1,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            route="gateway",
        )
        await session.commit()
    token = mint_scoped_token(project_id=pid)

    body = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]

    assert body["allow"] is False
    resets = month_end(month_of(datetime.now(UTC)))
    assert body["reopens_at"] == int(resets.timestamp())


@pytest.mark.anyio
async def test_admission_refusal_posts_nothing_with_no_turn_running(client):
    """No turn is in flight at this place — that is the turn-START refusal
    path's job (it already posts its own exhaustion notice), not admission's.
    A refusal here must invent nothing."""
    from app.domain.block.repositories import BlockRepository
    from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT

    pid = _make_project(client)
    topic_id = client.post("/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    await _exhaust(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/llm/admission", headers=headers)
    assert r.json()["data"]["allow"] is False

    async with client.test_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
    assert not any(b.content == CREDITS_EXHAUSTED_EVENT for b in blocks)


@pytest.mark.anyio
async def test_admission_says_which_pool_serves_the_project(client, monkeypatch):
    """The proxy asks once and learns both things: may it run, and where does
    it go (#243). Where it goes is a project setting, so supply can change
    without touching the proxy or restarting the sandbox."""
    from app.core.config import settings as app_settings
    from app.domain.project.repositories import ProjectRepository

    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(app_settings, "agent_model", "deepseek-flash")
    # An unset project uses the configured default for inference, even though
    # the client was bootstrapped with subscription-shaped credentials.
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"]["pool"] == "gateway"
    # And which model, because the launch environment names none: the binding
    # resolved here is what the proxy writes into the request body.
    assert body["supply"]["model"] == "deepseek-flash"
    assert body["allow"] is True
    assert body["supply"].get("key") is None

    async with client.test_factory() as session:
        project = await ProjectRepository(session).get(uuid.UUID(pid))
        assert project is not None
        project.settings = {"supply": "subscription"}
        await session.commit()

    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["supply"]["pool"] == "subscription"
    assert body["supply"]["model"] == "claude-sonnet-5"
    assert body["supply"].get("key") is None


@pytest.mark.anyio
async def test_admission_refuses_by_name_when_it_cannot_resolve_a_model(
    client, monkeypatch
):
    """答不出就拒绝，不换池（I27）。The launch environment carries no fallback
    model any more, so this is the whole exit: a request whose model cannot be
    resolved is refused, in the resolver's own words, and never quietly served
    from the other pool."""
    from app.domain.agent import gateway_catalog
    from app.domain.project.repositories import ProjectRepository

    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)
    headers = {"Authorization": f"Bearer {token}"}

    async with client.test_factory() as session:
        project = await ProjectRepository(session).get(uuid.UUID(pid))
        assert project is not None
        # A project on the pool, on a deployment whose pool serves nothing —
        # an operator pulling the last model out of the catalogue does this.
        project.settings = {"supply": "gateway"}
        await session.commit()
    monkeypatch.setattr(gateway_catalog, "offerable", lambda: [])

    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["allow"] is False
    assert "默认模型" in body["reason"]
    assert body["supply"] == {}
    # Named as its own kind of refusal. The proxy renders every `allow=false`
    # it cannot tell apart as a 429 "cheese project budget: …", so without this
    # the user of a project whose catalogue serves nothing is told their quota
    # ran out — and sent to top up an account that is fine.
    assert body["reason_kind"] == "binding"
