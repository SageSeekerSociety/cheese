"""Freeing a topic's compute (on 采纳/archive, via stop_topic_container) must reap
BOTH backends' boxes. The old code only removed the SDK container, so every
tmux-backed topic leaked its `cheesex-tmux-*` container forever."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.workspace import service as ws


def test_sdk_and_tmux_container_names_are_distinct():
    tid = uuid.uuid4()
    assert ws.container_name(tid) == f"cheesex-sbx-{tid.hex[:12]}"
    assert ws.tmux_container_name(tid) == f"cheesex-tmux-{tid.hex[:12]}"
    assert ws.container_name(tid) != ws.tmux_container_name(tid)


def test_stop_topic_container_reaps_both_backends(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(ws.subprocess, "run", lambda argv, **kw: calls.append(argv))

    tid = uuid.uuid4()
    ws.stop_topic_container(tid)

    removed = {argv[-1] for argv in calls if argv[:3] == ["docker", "rm", "-f"]}
    assert ws.container_name(tid) in removed  # cheesex-sbx-… (SDK box)
    assert ws.tmux_container_name(tid) in removed  # cheesex-tmux-… (tmux box)


def test_stop_topic_container_noop_without_docker(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: False)
    monkeypatch.setattr(ws.subprocess, "run", lambda argv, **kw: calls.append(argv))
    ws.stop_topic_container(uuid.uuid4())
    assert calls == []  # no docker → never shells out


# The accept/archive path frees a topic's compute for BOTH backends: the Docker
# container AND, when it ran on an enrolled device, its screen. A device screen was
# never reaped before, so the claude process behind it leaked on the machine.


@pytest.mark.anyio
async def test_accept_release_frees_both_the_container_and_the_device_screen(
    monkeypatch,
):
    from app.domain.agent import device_provider
    from app.domain.review.services import AcceptService

    containers: list[uuid.UUID] = []
    screens: list[tuple] = []
    monkeypatch.setattr(ws, "stop_topic_container", containers.append)

    async def fake_release(project_id, topic_id, **kw):
        screens.append((project_id, topic_id))

    monkeypatch.setattr(device_provider, "release_topic_screen", fake_release)

    pid, tid = uuid.uuid4(), uuid.uuid4()
    topic = SimpleNamespace(id=tid, project_id=pid)
    # The method needs no DB — exercise it on a bare instance.
    svc = AcceptService.__new__(AcceptService)
    svc._machines = AsyncMock()
    await svc._release_topic_compute(topic)

    svc._machines.release_topic_machine.assert_awaited_once_with(tid)
    assert containers == [tid]  # the sandbox container is freed
    assert screens == [(pid, tid)]  # AND the device screen (project, topic)


@pytest.mark.anyio
async def test_accept_release_never_fails_the_accept(monkeypatch):
    """Both halves are best-effort: a docker error or an offline device must not
    propagate out of the archive path and fail the accept itself."""
    from app.domain.agent import device_provider
    from app.domain.review.services import AcceptService

    def boom_container(_tid):
        raise RuntimeError("docker daemon down")

    async def boom_release(project_id, topic_id, **kw):
        raise RuntimeError("device channel dropped")

    monkeypatch.setattr(ws, "stop_topic_container", boom_container)
    monkeypatch.setattr(device_provider, "release_topic_screen", boom_release)

    topic = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    svc = AcceptService.__new__(AcceptService)
    svc._machines = AsyncMock()
    await svc._release_topic_compute(topic)  # must not raise


@pytest.mark.anyio
async def test_accept_release_propagates_cloud_deletion_failure():
    from app.domain.review.services import AcceptService

    topic = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    svc = AcceptService.__new__(AcceptService)
    svc._machines = AsyncMock()
    svc._machines.release_topic_machine.side_effect = RuntimeError(
        "MicroCloud refused deletion"
    )

    with pytest.raises(RuntimeError, match="refused deletion"):
        await svc._release_topic_compute(topic)
