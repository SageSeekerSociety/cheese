"""The model route remote machines use (/api/llm).

A MicroCloud machine gets its own scoped cheese token, never a provider key —
so this route must authenticate that token, swap in the project's virtual
gateway key, and pass the upstream answer through untouched.
"""

import uuid

import httpx
import pytest

from app.api.routes import llm_proxy
from app.core.sandbox_auth import mint_scoped_token


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


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
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid, topic_id=str(uuid.uuid4()))

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
    """One budget, two enforcement points: the same compute grants the gateway
    prices into max_budget answer the subscription proxy's yes/no here."""
    pid = _make_project(client)
    token = mint_scoped_token(project_id=pid)

    # No grants at all = 自治项目: never refused.
    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"] == {"allow": True, "reason": "unlimited"}

    from app.domain.usage.repositories import ComputeGrantRepository

    async with client.test_factory() as session:
        await ComputeGrantRepository(session).grant(
            project_id=uuid.UUID(pid), source_task_id=None, credits_total=5.0
        )
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["data"]["allow"] is True

    async with client.test_factory() as session:
        await ComputeGrantRepository(session).consume(uuid.UUID(pid), 5.0)
        await session.commit()

    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    body = r.json()["data"]
    assert body["allow"] is False
    assert "5.0000" in body["reason"]
