"""POST /sandbox/hooks/{topic_id} — auth, the durable record, and live routing.

The endpoint's contract is that a 200 means the platform has the event on its
own disk: the sender deletes its copy on that ack, and on a self-hosted machine
that copy is the only one in the world. So every test here that expects a 200
also expects the event to be readable back out of the topic's log.
"""

import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import SANDBOX_TOKEN, mint_scoped_token
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code import hook_router, read_log


@pytest.fixture
def spool_root(tmp_path, monkeypatch):
    """Keep the topic logs these tests write inside the test's own directory."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    return tmp_path


def _post(client, topic, *, project=None, event_id=None, body=None):
    project = project or uuid.uuid4()
    headers = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=str(project), topic_id=str(topic)
        )
    }
    if event_id is not None:
        headers["X-Cheese-Event-Id"] = event_id
    return client.post(
        f"/sandbox/hooks/{topic}",
        json=body or {"hook_event_name": "MessageDisplay", "delta": "hi"},
        headers=headers,
    )


def _logged(project, topic):
    return read_log(SessionRef(project, topic))


def test_hook_rejected_without_valid_token(client):
    topic = str(uuid.uuid4())
    r = client.post(
        f"/sandbox/hooks/{topic}",
        json={"hook_event_name": "Stop"},
        headers={"X-Cheese-Token": "wrong"},
    )
    assert r.status_code == 401


def test_session_owner_is_taken_from_verified_token(client, spool_root):
    project, topic = uuid.uuid4(), uuid.uuid4()
    token = mint_scoped_token(
        project_id=str(project),
        topic_id=str(topic),
        agent_handle="original-agent",
    )
    response = client.post(
        f"/sandbox/hooks/{topic}",
        json={
            "hook_event_name": "SessionStart",
            "session_id": "old-session",
            "_agent_handle": "replacement-agent",
        },
        headers={"X-Cheese-Token": token, "X-Cheese-Event-Id": "original-start"},
    )
    assert response.status_code == 200
    assert _logged(project, topic)[0].record["_agent_handle"] == "original-agent"


def test_hook_accepted_but_undelivered_without_listener(client, spool_root):
    project, topic = uuid.uuid4(), uuid.uuid4()
    eid = str(uuid.uuid4())
    r = _post(
        client,
        topic,
        project=project,
        event_id=eid,
        body={"hook_event_name": "SessionStart", "session_id": "x"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["delivered"] is False
    assert [event.eid for event in _logged(project, topic)] == [eid]


def test_hook_routed_to_subscribed_sink_and_written_down(client, spool_root):
    """Delivered to the live screen AND on our disk — a subscriber is not a
    place an event has been put.

    The sink is an in-process queue. The ack that lets the machine delete its
    copy is only honest once the platform has written the event down, so a
    backend that dies between the 200 and the consumer still has it — and a
    lost `Stop` no longer leaves that turn showing as never finished.
    """
    project, topic = uuid.uuid4(), uuid.uuid4()
    eid = str(uuid.uuid4())
    sink = hook_router.subscribe(str(topic))
    try:
        r = _post(client, topic, project=project, event_id=eid)
        assert r.status_code == 200
        assert r.json()["data"]["delivered"] is True
        assert sink.queue.get_nowait()["delta"] == "hi"
    finally:
        hook_router.unsubscribe(str(topic), sink)
    logged = _logged(project, topic)
    assert [event.eid for event in logged] == [eid]
    assert logged[0].record is not None
    assert logged[0].record["delta"] == "hi"


def test_hook_without_event_id_is_not_acked(client, spool_root):
    """No id, no name to file it under — so no ack that would delete it."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    sink = hook_router.subscribe(str(topic))
    try:
        r = _post(client, topic, project=project)
        assert r.status_code == 400
        assert sink.queue.empty()
    finally:
        hook_router.unsubscribe(str(topic), sink)
    assert _logged(project, topic) == []


def test_hook_the_platform_cannot_record_is_not_acked(client, tmp_path, monkeypatch):
    """A spool the platform cannot write to must not produce a 200.

    The sender's retry is the only thing left holding the event, and it only
    retries what was refused.
    """
    broken = tmp_path / "not-a-directory"
    broken.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "workspace_root", str(broken))
    project, topic = uuid.uuid4(), uuid.uuid4()
    sink = hook_router.subscribe(str(topic))
    try:
        r = _post(client, topic, project=project, event_id=str(uuid.uuid4()))
        assert r.status_code == 503
        assert sink.queue.empty()
    finally:
        hook_router.unsubscribe(str(topic), sink)


def test_hook_rejects_non_object_body(client):
    topic = str(uuid.uuid4())
    r = client.post(
        f"/sandbox/hooks/{topic}",
        json=["not", "an", "object"],
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    )
    assert r.status_code == 400
