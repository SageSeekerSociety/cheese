"""A previewed app whose machine sleeps: its kept snapshot, waking it, and the steps.

The panel shows the last page that loaded while the app cannot be reached, and a
button that asks the teammate who declared the app to bring it back. The tunnel
here is the real hub with a fake machine on the other end; the agent's turn is
recorded instead of run.
"""

import uuid

import pytest

from app.api.deps import get_work_runner
from app.api.routes import topics_preview_app
from app.core.redis import get_redis_client
from tests.integration.conftest import session_auth_headers
from tests.integration.test_app_preview_proxy import (
    FakeMachine,
    _project_topic,
    _room_agent,
)
from tests.integration.test_app_preview_proxy import preview_config as preview_config

ALICE = session_auth_headers("alice")


class RecordingRunner:
    """Stands in for the work runner: the turn a wake starts, written down."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit(self, chat, topic_id, **kwargs):
        self.submitted.append({"topic_id": topic_id, **kwargs})
        return uuid.uuid4()


@pytest.fixture
def runner(client):
    recording = RecordingRunner()
    client.app.dependency_overrides[get_work_runner] = lambda: recording
    yield recording
    client.app.dependency_overrides.pop(get_work_runner, None)


def _declare(client, topic_id, path="npm run dev") -> str:
    shown = client.post(
        f"/topics/{topic_id}/shown", json={"path": path, "as": "app"}, headers=ALICE
    )
    assert shown.status_code == 200, shown.text
    return client.get(f"/topics/{topic_id}/preview", headers=ALICE).json()["data"][
        "artifact_id"
    ]


def _room(client):
    _, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    return topic_id, _room_agent(client, topic_id)


def test_a_snapshot_is_kept_per_app_and_read_back_by_the_room(client, preview_config):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        artifact = _declare(client, topic_id)
    finally:
        machine.detach()
    url = f"/topics/{topic_id}/preview/snapshot"
    assert client.get(url, headers=ALICE).json()["data"] is None
    page = "<!DOCTYPE html><html><body>第 2 步</body></html>"
    kept = client.put(
        url,
        json={
            "artifact_id": artifact,
            "html": page,
            "href": "https://x/signup",
            "title": "报名",
        },
        headers=ALICE,
    )
    assert kept.status_code == 200, kept.text
    read = client.get(url, headers=ALICE).json()["data"]
    assert read["html"] == page and read["href"] == "https://x/signup"
    assert read["title"] == "报名" and read["taken_at"] > 0

    # 同一个应用再声明一次（新的一行 artifact）：快照还在。
    machine = FakeMachine().attach(topic_id, seat)
    try:
        again = _declare(client, topic_id)
        assert again != artifact
        assert client.get(url, headers=ALICE).json()["data"]["html"] == page
        # 写给旧那一行的快照不算数：预览已经换过了。
        stale = client.put(
            url, json={"artifact_id": artifact, "html": page}, headers=ALICE
        )
        assert stale.status_code == 422, stale.text
        # 换成另一个应用：不继承别人的快照。
        _declare(client, topic_id, path="python -m http.server")
        assert client.get(url, headers=ALICE).json()["data"] is None
    finally:
        machine.detach()


def test_an_oversized_snapshot_or_a_file_preview_is_refused(client, preview_config):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        artifact = _declare(client, topic_id)
    finally:
        machine.detach()
    url = f"/topics/{topic_id}/preview/snapshot"
    big = "x" * (topics_preview_app.SNAPSHOT_MAX_BYTES + 1)
    refused = client.put(
        url, json={"artifact_id": artifact, "html": big}, headers=ALICE
    )
    assert refused.status_code == 422, refused.text
    # 一个外人读不到这间房的快照。
    outsider = client.get(url, headers=session_auth_headers("mallory"))
    assert outsider.status_code in (403, 404), outsider.text


def test_waking_a_sleeping_app_asks_its_teammate_once(client, preview_config, runner):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id)
        # 应用还答着：不必叫醒谁。
        up = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
        assert up.json()["data"] == {"started": False, "reason": "up"}, up.text
        status = client.get(f"/topics/{topic_id}/preview/wake-status", headers=ALICE)
        assert status.json()["data"]["tunnel_up"] is True
        assert status.json()["data"]["app_up"] is True
    finally:
        machine.detach()
    assert runner.submitted == []

    # 电脑睡了：通道断开。
    status = client.get(f"/topics/{topic_id}/preview/wake-status", headers=ALICE)
    assert status.status_code == 200, status.text
    assert status.json()["data"] == {
        "machine": None,
        "tunnel_up": False,
        "agent_working": False,
        "app_up": False,
    }
    woke = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
    assert woke.status_code == 200, woke.text
    assert woke.json()["data"]["started"] is True
    [turn] = runner.submitted
    assert turn["author"] == "system"
    assert "npm run dev" in turn["content"] and "cheese serve" in turn["content"]
    assert turn["nudge_event"].key == "previewWakeRequested"
    assert seat in str(turn["addressed"])
    if get_redis_client() is not None:
        # 紧接着再按一次：不再起第二轮。
        twice = client.post(f"/topics/{topic_id}/preview/wake", headers=ALICE)
        assert twice.json()["data"] == {"started": False, "reason": "pending"}
        assert len(runner.submitted) == 1


def test_waking_needs_a_member_who_may_ask_the_agent(client, preview_config, runner):
    topic_id, seat = _room(client)
    machine = FakeMachine().attach(topic_id, seat)
    try:
        _declare(client, topic_id)
    finally:
        machine.detach()
    refused = client.post(
        f"/topics/{topic_id}/preview/wake", headers=session_auth_headers("mallory")
    )
    assert refused.status_code in (403, 404), refused.text
    assert runner.submitted == []
