"""The model endpoint machines use.

A machine is handed the raw upstream key today, in plain sight on that host,
and its spend lands in the provider's bill under one undifferentiated key —
which is why 300 RMB could vanish with no way to say what spent it. This route
exists so the credential never leaves the box and every call is attributed to a
project by the backend rather than on the machine's word.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token

pytestmark = pytest.mark.anyio


def _token(project_id: uuid.UUID) -> str:
    return mint_scoped_token(project_id=str(project_id), topic_id=None)


async def test_admin_paths_are_not_reachable(client):
    """Key minting, spend and budgets must stay on the box. An allowlist keeps
    that true as the gateway grows a new endpoint."""
    pid = uuid.uuid4()
    for path in ("key/generate", "spend/logs", "model/new", "v1/chat/completions"):
        r = client.post(
            f"/api/llm/{path}",
            headers={"X-Cheese-Token": _token(pid), "X-Cheese-Project": str(pid)},
            json={},
        )
        assert r.status_code != 200, f"{path} must not be proxied"


async def test_a_call_without_this_projects_token_is_refused(client):
    pid, other = uuid.uuid4(), uuid.uuid4()

    r = client.post(
        "/api/llm/v1/messages",
        headers={"X-Cheese-Token": _token(other), "X-Cheese-Project": str(pid)},
        json={"model": "glm-5.2", "messages": []},
    )
    assert r.status_code != 200

    r = client.post(
        "/api/llm/v1/messages",
        headers={"X-Cheese-Project": str(pid)},
        json={"model": "glm-5.2", "messages": []},
    )
    assert r.status_code != 200


async def test_no_project_key_means_no_call_at_all(client, monkeypatch):
    """The failure must be refusal, never a quiet fall back to the upstream key.

    Falling back would 'work' and produce exactly the unattributed spend this
    route was built to end — the worst kind of bug, because nothing looks wrong.
    """
    sent: list = []

    async def _no_key(_self, _pid):
        return None

    monkeypatch.setattr(
        "app.domain.agent.chat.ChatService._gateway_project_env", _no_key
    )
    monkeypatch.setattr(
        "app.api.routes.llm_proxy.httpx.AsyncClient",
        lambda **kw: sent.append(1) or (_ for _ in ()).throw(AssertionError("called")),
    )

    pid = uuid.uuid4()
    r = client.post(
        "/api/llm/v1/messages",
        headers={"X-Cheese-Token": _token(pid), "X-Cheese-Project": str(pid)},
        json={"model": "glm-5.2", "messages": []},
    )

    assert r.status_code != 200
    assert not sent, "it must not reach upstream without a project key"
