"""A project cannot hold two active teammates a member cannot tell apart.

Same name and same saved configuration is refused, on the way in (adding a
platform model from the team list) and on the way through (editing one into
the other). Anything visible that differs lets both stay, and a retired
teammate is not compared.
"""

import pytest

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.machine import environments
from tests.integration.conftest import (
    approve_overseas,
    post_project,
    put_on_plan,
    session_auth_headers,
)

MODEL = "deepseek-flash"


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", MODEL)
    # Adding from the team list needs a sandbox spec to run on.
    monkeypatch.setattr(environments, "cloud_provisionable", lambda _settings: True)
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def _project(client) -> str:
    response = post_project(
        client, json={"name": "Twins"}, headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    client.headers.update(session_auth_headers("alice"))

    async def reserve() -> None:
        async with client.test_request_factory() as session:  # type: ignore[attr-defined]
            await put_on_plan(session, project["team_id"], "reserve")
            await approve_overseas(session, project["team_id"])
            await session.commit()

    client.portal.call(reserve)  # type: ignore[union-attr]
    return project["id"]


def _add(client, pid: str, model: str = MODEL):
    return client.post(
        f"/projects/{pid}/own-agents",
        json={"connection_id": "platform", "model": model, "device_id": None},
    )


def _named(client, pid: str, name: str) -> list[dict]:
    rows = client.get(f"/projects/{pid}/agents").json()["data"]["data"]
    return [a for a in rows if a["display_name"] == name]


def _refused(response, name: str) -> None:
    assert response.status_code in (400, 422), response.text
    assert name in response.text, response.text


def test_the_same_platform_model_cannot_be_added_twice(client):
    pid = _project(client)
    assert _add(client, pid).status_code == 200
    _refused(_add(client, pid), MODEL)
    assert len(_named(client, pid, MODEL)) == 1


def test_a_renamed_or_retuned_twin_lets_another_in(client):
    """芝士high and 芝士low on one model stay possible."""
    pid = _project(client)
    assert _add(client, pid).status_code == 200
    first = _named(client, pid, MODEL)[0]

    renamed = client.put(
        f"/projects/{pid}/agents/{first['id']}", json={"display_name": "芝士high"}
    )
    assert renamed.status_code == 200, renamed.text
    assert _add(client, pid).status_code == 200

    second = _named(client, pid, MODEL)[0]
    retuned = client.put(
        f"/projects/{pid}/agents/{second['id']}",
        json={"configuration": {**second["configuration"], "effort": "high"}},
    )
    assert retuned.status_code == 200, retuned.text
    assert _add(client, pid).status_code == 200
    assert len(_named(client, pid, MODEL)) == 2


def test_an_edit_cannot_make_one_teammate_the_twin_of_another(client):
    pid = _project(client)
    assert _add(client, pid).status_code == 200
    first = _named(client, pid, MODEL)[0]
    client.put(f"/projects/{pid}/agents/{first['id']}", json={"display_name": "A"})
    assert _add(client, pid).status_code == 200

    back = client.put(
        f"/projects/{pid}/agents/{first['id']}", json={"display_name": MODEL}
    )
    _refused(back, MODEL)
    assert len(_named(client, pid, "A")) == 1


def test_a_retired_twin_is_not_compared(client):
    pid = _project(client)
    assert _add(client, pid).status_code == 200
    first = _named(client, pid, MODEL)[0]
    retired = client.delete(f"/projects/{pid}/agents/{first['id']}")
    assert retired.status_code == 200, retired.text
    assert _add(client, pid).status_code == 200
    assert [a["is_active"] for a in _named(client, pid, MODEL)] == [False, True]
