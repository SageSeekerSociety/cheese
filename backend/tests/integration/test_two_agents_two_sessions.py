"""Two agents in one room hold two live control sessions.

A room seats collaborators, and each of its agents runs its own worker. One
"the room's live session" slot meant the second agent to launch took it, and
from then on the first agent's questions could not be answered and its
permission prompts could not be approved — the panel was asking the room,
which cannot say which agent it means.
"""

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers


@pytest.fixture
def place(client):
    project = client.post(
        "/projects", json={"name": "Two agents", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room", "created_by": "alice"},
    ).json()["data"]
    return project["id"], topic["id"]


def _seat_a_second_agent(client, project: str, topic: str) -> str:
    made = client.post(f"/projects/{project}/agents", json={"handle": "reviewer"})
    assert made.status_code == 200, made.text
    seat = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{topic}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    return seat


def _open_session(client, project: str, topic: str, agent: str) -> str:
    r = client.post(
        "/v1/code/sessions",
        json={"title": agent},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project,
                topic_id=topic,
                remote_control=True,
                agent_handle=agent,
            )
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["session"]["id"]


def test_a_second_agents_session_does_not_evict_the_first(client, place):
    from app.api.routes.remote_control import store
    from app.domain.agent.remote_control import live_key

    project, topic = place
    rows = client.get(f"/topics/{topic}/members").json()["data"]["data"]
    first = next(m["member_handle"] for m in rows if m["agent"])
    second = _seat_a_second_agent(client, project, topic)

    first_sid = _open_session(client, project, topic, first)
    second_sid = _open_session(client, project, topic, second)
    assert first_sid != second_sid

    async def live(agent: str) -> str | None:
        session = await store().current(topic, agent)
        return session["id"] if session else None

    # Each agent's own session is still the live one for that agent.
    assert client.portal.call(live, first) == first_sid
    assert client.portal.call(live, second) == second_sid

    async def cleanup():
        redis = store().redis
        for sid, agent in ((first_sid, first), (second_sid, second)):
            await redis.delete(f"cheese:rc:{sid}:session", live_key(topic, agent))

    client.portal.call(cleanup)


def test_controlling_the_first_agents_session_is_not_refused(client, place):
    """The panel controls a session by id. Before this, that id stopped being
    "the room's current one" the moment another agent launched, and every
    control came back 409 — the room had two agents and one slot."""
    from app.api.routes.remote_control import store
    from app.domain.agent.remote_control import live_key

    project, topic = place
    rows = client.get(f"/topics/{topic}/members").json()["data"]["data"]
    first = next(m["member_handle"] for m in rows if m["agent"])
    second = _seat_a_second_agent(client, project, topic)
    first_sid = _open_session(client, project, topic, first)
    second_sid = _open_session(client, project, topic, second)

    r = client.post(
        f"/topics/{topic}/agent/control",
        json={
            "session_id": first_sid,
            "request_id": "probe",
            "request": {"subtype": "background_tasks"},
        },
        params={"wait": 0},
        headers=session_auth_headers("alice"),
    )
    # Whatever the worker does or does not answer, the session is reachable:
    # the one refusal that must not happen is "the active session changed".
    assert "active session changed" not in r.text, r.text

    async def cleanup():
        redis = store().redis
        for sid, agent in ((first_sid, first), (second_sid, second)):
            await redis.delete(f"cheese:rc:{sid}:session", live_key(topic, agent))

    client.portal.call(cleanup)
