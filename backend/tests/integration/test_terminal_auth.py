"""Who is told a topic's live terminal exists, and when.

The status endpoint is what decides whether 现场 embeds the pane or shows the
施工记录 timeline. It is not the access decision — the screen socket checks its
own credential — but getting it wrong is what shipped the terminal as a white
box: `available` used to be computed from the machine alone, so the panel
replaced the timeline with a frame the socket would refuse, leaving no visible
content and no way back.
"""

import uuid

import pytest

from app.api.routes import terminal


def _project_topic(client, handle: str = "alice"):
    project = client.post(
        "/projects", json={"name": "T", "owner_handle": handle}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "t"}
    ).json()["data"]
    return project, topic


def _screen_open(sid: str):
    def _resolve(_topic_id: uuid.UUID) -> str:
        return sid

    return _resolve


def test_status_says_unavailable_without_a_credential(client, monkeypatch):
    """现场 must fall back to the 施工记录 timeline, not embed a pane whose socket
    would then refuse the viewer."""
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open("s-1"))

    _project, topic = _project_topic(client)

    data = client.get(f"/topics/{topic['id']}/terminal").json()["data"]

    assert data["available"] is False, data
    assert "ws" not in data


def test_status_says_unavailable_when_no_screen_is_open(client, monkeypatch):
    """A member with every right still has nothing to watch when the topic is not
    running anywhere."""
    from tests.integration.test_connector_viewer import _login

    monkeypatch.setattr(terminal, "_device_screen_id", lambda _t: None)

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    data = client.get(
        f"/topics/{topic['id']}/terminal",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]

    assert data["available"] is False, data


def test_status_hands_a_member_the_screen_that_is_really_open(client, monkeypatch):
    """Credential + an open screen → the socket path the viewer attaches to."""
    from tests.integration.test_connector_viewer import _login

    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open("s-42"))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    data = client.get(
        f"/topics/{topic['id']}/terminal",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]

    assert data["available"] is True, data
    assert data["ws"] == "/connector/session/s-42/screen"


@pytest.mark.anyio
async def test_a_harness_that_draws_nothing_keeps_the_timeline(client, monkeypatch):
    """A screen is open and the viewer may watch it — and there is still nothing
    in it.

    pi and Codex run a RUNNER as the screen's program and drive the agent over
    RPC, so their pane stays empty for the life of the session. Embedding it
    puts the same black frame in front of the same person a refused socket did,
    and takes the 施工记录 timeline away to do it.
    """
    from app.domain.topic.models import Topic
    from tests.integration.test_connector_viewer import _login

    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open("s-9"))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    async with client.test_factory() as session:
        room = await session.get(Topic, uuid.UUID(topic["id"]))
        room.session_placement = {
            "device_id": "dev",
            "resource_id": topic["id"],
            "channel": "device",
            "runtime": {"harness": "pi"},
        }
        await session.commit()

    data = client.get(
        f"/topics/{topic['id']}/terminal",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]

    assert data["available"] is False, data
