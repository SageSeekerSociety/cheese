"""Topic compute-profile API (execution-architecture v4 会话级选择).

A topic picks its compute pool before its first turn; the choice sticks as the
project default and freezes once the topic has run (session_id set).
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import Team
from app.domain.topic.repositories import TopicRepository


def _project(client, owner: str = "andyl") -> str:
    return client.post(
        "/api/projects", json={"name": "P", "owner_handle": owner}
    ).json()["data"]["id"]


def _topic(client, pid: str) -> str:
    return client.post(
        "/api/topics", json={"project_id": pid, "title": "T", "created_by": "andyl"}
    ).json()["data"]["id"]


def _mark_started(client, tid: str) -> None:
    """Simulate the topic having run one turn (session_id captured)."""

    async def _run() -> None:
        async with client.test_factory() as s:
            repo = TopicRepository(s)
            topic = await repo.get(uuid.UUID(tid))
            await repo.set_session_id(topic, "sess-1")
            await s.commit()

    asyncio.run(_run())


def test_new_topic_inherits_default_and_is_unlocked(client):
    pid = _project(client)
    tid = _topic(client, pid)
    body = client.get(f"/api/topics/{tid}/compute-profile").json()["data"]
    assert body["current"] == "local-docker"  # the always-on default
    assert body["locked"] is False
    assert body["inherited"] is True
    assert "local-docker" in {p["id"] for p in body["profiles"]}


def test_fresh_project_inherits_its_team_default(client):
    pid = _project(client)
    tid = _topic(client, pid)

    async def _seed_team_default() -> None:
        async with client.test_factory() as session:
            now = datetime.now(UTC)
            team = Team(
                name="Default compute team",
                intro="",
                description="",
                avatar_id=1,
                compute_profile="remote-cheesed",
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            project = await ProjectRepository(session).get(uuid.UUID(pid))
            project.team_id = team.id
            await session.commit()

    asyncio.run(_seed_team_default())
    body = client.get(f"/api/topics/{tid}/compute-profile").json()["data"]
    assert body["current"] == "remote-cheesed"
    assert body["sticky"] == "remote-cheesed"
    assert body["inherited"] is True


def test_select_persists_to_topic_and_project_sticky(client):
    pid = _project(client)
    tid = _topic(client, pid)

    # An undeployed pool can't be selected.
    bad = client.put(f"/api/topics/{tid}/compute-profile", json={"profile": "gpu"})
    assert bad.status_code == 422

    r = client.put(
        f"/api/topics/{tid}/compute-profile", json={"profile": "local-docker"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["current"] == "local-docker"
    assert r.json()["data"]["inherited"] is False

    # Persisted on the topic (no longer inheriting)...
    tbody = client.get(f"/api/topics/{tid}/compute-profile").json()["data"]
    assert tbody["inherited"] is False
    # ...and remembered as the project's sticky default for the next new topic.
    pbody = client.get(f"/api/projects/{pid}/compute-profiles").json()["data"]
    assert pbody["current"] == "local-docker"


def test_visibility_block_is_present_non_default_and_carries_the_notice(client):
    """#282 §四 / #358: the compute-profile response a room reads carries the
    visibility 档 so the room can SHOW whether a turn sees the whole machine. Boxed
    is the default-but-undeployed option; whole-machine is available yet non-default
    and describes itself with the honest #282 warning. A topic with no pinned device
    is not a Hosted Machine turn, so `machine_access` is False."""
    pid = _project(client)
    tid = _topic(client, pid)
    vis = client.get(f"/api/topics/{tid}/compute-profile").json()["data"]["visibility"]

    opts = {o["id"]: o for o in vis["options"]}
    assert opts["isolated"]["default"] is True
    assert opts["isolated"]["available"] is False
    assert opts["host"]["default"] is False
    assert opts["host"]["available"] is True
    assert "整台机器" in opts["host"]["description"]

    assert vis["effective"] is None  # not pinned to any device
    assert vis["machine_access"] is False
    assert "整台机器" in vis["notice"]  # badge / tooltip copy is present


def test_two_topics_on_one_machine_report_their_own_visibility(client):
    """Visibility belongs to each topic↔machine binding, not to the device."""
    pid = _project(client)
    host_tid = _topic(client, pid)
    isolated_tid = _topic(client, pid)

    async def _pin_both_topics() -> None:
        from app.domain.device.service import DeviceService
        from app.domain.device.sql_repository import SqlDeviceRepository
        from app.domain.device.supply import Supply, Visibility

        async with client.test_factory() as session:
            svc = DeviceService(SqlDeviceRepository(session))
            code = await svc.start("dev-box")
            device = await svc.approve(
                code,
                owner_user_id=1,
                supply=Supply.self_hosted,
                visibility=Visibility.isolated,
            )
            await svc.bind_topic_device(
                uuid.UUID(host_tid), device.device_id, Visibility.host
            )
            await svc.bind_topic_device(
                uuid.UUID(isolated_tid), device.device_id, Visibility.isolated
            )
            await session.commit()

    asyncio.run(_pin_both_topics())
    host_visibility = client.get(f"/api/topics/{host_tid}/compute-profile").json()[
        "data"
    ]["visibility"]
    isolated_visibility = client.get(
        f"/api/topics/{isolated_tid}/compute-profile"
    ).json()["data"]["visibility"]
    assert host_visibility["effective"] == "host"
    assert host_visibility["machine_access"] is True
    assert isolated_visibility["effective"] == "isolated"
    assert isolated_visibility["machine_access"] is False


def test_locked_once_topic_has_run(client):
    pid = _project(client)
    tid = _topic(client, pid)
    _mark_started(client, tid)

    body = client.get(f"/api/topics/{tid}/compute-profile").json()["data"]
    assert body["locked"] is True

    # Switching after the first turn is rejected — the pin is frozen.
    r = client.put(
        f"/api/topics/{tid}/compute-profile", json={"profile": "local-docker"}
    )
    assert r.status_code == 422
